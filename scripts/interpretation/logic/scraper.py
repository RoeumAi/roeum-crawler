import asyncio
from concurrent.futures import ThreadPoolExecutor
from playwright.async_api import async_playwright, expect
import json
import re
import os
import sys
import math
from datetime import datetime
import random

# 프로젝트 루트 경로 설정
# __file__이 정의되지 않은 환경(예: Jupyter notebook)을 위해 경로를 안전하게 설정합니다.
try:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
except NameError:
    project_root = os.path.abspath('.') # 현재 작업 디렉토리를 기준으로 설정
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.identifiers import article_chunk_id, normalize_article_number
from scripts.core.database.source_versioning import enrich_source_document

# 스크레이퍼 타입에 맞는 로거 생성
logger = get_logger(__name__, scraper_type='interpretation')

def parse_subtitle(subtitle_text):
    """부제목 text에서 부서, 문서번호, 날짜를 추출합니다."""
    logger.debug(f"Subtitle 파싱 시도: {subtitle_text}")
    date_pattern = r'(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\.'

    match = re.search(r'\[\s*(.*?)\s+(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_number = match.group(2).strip()
        doc_date = match.group(3).strip().replace(" ", "")
        logger.debug(f"패턴 1로 파싱 성공: 부서='{department}', 번호='{doc_number}', 날짜='{doc_date}'")
        return department, doc_number, doc_date

    match = re.search(r'\[\s*(.*?),\s*' + date_pattern + r'\s*\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        doc_date = match.group(2).strip().replace(" ", "")
        logger.debug(f"패턴 2로 파싱 성공: 부서='{department}', 번호='정보 없음', 날짜='{doc_date}'")
        return department, "정보 없음", doc_date

    match = re.search(r'\[(.*?)\]', subtitle_text)
    if match:
        department = match.group(1).strip()
        logger.debug(f"패턴 3(Fallback)으로 파싱 성공: 부서='{department}', 번호/날짜 없음")
        return department, "정보 없음", "정보 없음"

    logger.warning(f"Subtitle 파싱 실패: {subtitle_text}")
    return "정보 없음", "정보 없음", "정보 없음"


def split_sections_by_header(sections: dict) -> list:
    """
    섹션별 딕셔너리를 개별 문서로 분리
    law/adrule의 article 형식에 맞춰 변환
    
    Args:
        sections: {"질의요지": "내용...", "회신": "내용..."} 형식
    
    Returns:
        섹션별로 분리된 딕셔너리 리스트 (article_number 사용)
    """
    section_list = []
    
    for idx, (header, content) in enumerate(sections.items(), 1):
        section_list.append({
            "article_number": float(idx),  # law/adrule과 동일하게 article_number 사용
            "article_title": header,
            "content": content.strip()
        })
    
    return section_list


def _parse_taxlaw_body(body_text: str) -> dict | None:
    """Parse NTS taxlaw-rendered interpretation pages exposed from law.go.kr."""
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    try:
        start_idx = lines.index("화면내 검색") + 1
    except ValueError:
        start_idx = 0

    content_lines = lines[start_idx:]
    if not content_lines:
        return None

    doc_title = content_lines[0]
    doc_number = ""
    doc_date = ""
    for line in content_lines[1:8]:
        if not doc_number and re.search(r"\d", line) and "：" not in line and ":" not in line:
            doc_number = line
        if "생산일자" in line or "등록일자" in line:
            doc_date = re.sub(r"^.*?[：:]\s*", "", line).strip().rstrip(".")
            if doc_date:
                break

    sections = {}
    current_header = None
    valid_headers = {"요지", "회신", "답변내용", "결정내용", "판결내용", "상세내용"}
    stop_headers = {"PDF로 보기", "저장", "인쇄", "보관", "관련 주제어", "관련 법령"}

    for line in content_lines[1:]:
        if line in stop_headers:
            continue
        if line in valid_headers:
            current_header = line
            sections.setdefault(current_header, "")
            continue
        if current_header:
            sections[current_header] += line + "\n"

    sections = {header: content.strip() for header, content in sections.items() if content.strip()}
    if not sections:
        return None

    return {
        "doc_title": doc_title,
        "sub_title": ", ".join(part for part in ["국세청", doc_number, doc_date] if part),
        "effective_date": doc_date.replace(".", "-") if doc_date else datetime.now().strftime('%Y-%m-%d'),
        "sections_data": split_sections_by_header(sections),
    }


def save_to_mongodb(sections_data: list, doc_title: str, doc_id: str, url: str,
                   sub_title: str = "", effective_date: str = None,
                   ofi_cls_cd: str = "350101") -> bool:
    """
    법령해석을 MongoDB에 섹션별로 분리하여 저장 (law/adrule와 동일한 스키마)
    
    처리 흐름:
    1. 각 섹션마다 독립 문서 생성 (article_number로 구분)
    2. 각 섹션을 MongoDB에 저장
    
    Args:
        sections_data: 섹션별 데이터 리스트
        doc_title: 문서 제목
        doc_id: 문서 ID (문서번호만, 예: "123456")
        url: 원본 URL
        sub_title: 부제 (부처명, 문서번호, 날짜 정보)
        effective_date: 효력일자 (YYYY-MM-DD 형식)
    
    Returns:
        저장 성공 여부
    """
    try:
        if not sections_data:
            logger.warning("저장할 섹션 데이터가 없습니다.")
            return False
        
        if effective_date is None:
            effective_date = datetime.now().strftime('%Y-%m-%d')
        
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db['interpretation']
        
        # 각 섹션마다 독립 문서 생성 및 저장
        saved_count = 0
        failed_count = 0
        
        for idx, section in enumerate(sections_data, 1):
            try:
                article_number = section.get('article_number')
                article_number = normalize_article_number(article_number)
                
                # law/adrule과 동일한 스키마 (article_number만 사용, article_number_numeric 제거)
                section_doc = {
                    "chunk_id": article_chunk_id("interpretation", doc_id, article_number),
                    "doc_id": doc_id,
                    "article_number": article_number,
                    "article_title": section.get('article_title'),
                    "title": doc_title,
                    "sub_title": sub_title,
                    "content": section.get('content'),
                    "doc_type": "법령해석",
                    "metadata": {
                        "source_url": url,
                        "source_type": "web",
                        "effective": effective_date,
                        "updated_at": datetime.now().isoformat(),
                        "is_active": True,
                        "cpt_ofi_code": "1492000",
                        "cpt_ofi_name": "고용노동부",
                        "ofi_cls_cd": ofi_cls_cd,
                        "article_index": idx,
                        "total_articles": len(sections_data),
                    }
                }
                section_doc = enrich_source_document(section_doc, "interpretation")

                # created_at을 제외한 $set 필드 구성
                set_fields = {k: v for k, v in section_doc.items() if k != "metadata"}
                set_fields.update({f"metadata.{k}": v for k, v in section_doc["metadata"].items()})

                # MongoDB에 upsert (created_at은 최초 insert 시에만 설정)
                result = collection.update_one(
                    {"doc_id": doc_id, "article_number": article_number},
                    {
                        "$set": set_fields,
                        "$setOnInsert": {"metadata.created_at": datetime.now().strftime('%Y-%m-%d')},
                    },
                    upsert=True
                )
                
                if result.upserted_id:
                    logger.info(f"✅ 새 섹션 생성: {doc_id} (article_number: {article_number})")
                else:
                    logger.info(f"🔄 섹션 업데이트: {doc_id} (article_number: {article_number})")
                
                saved_count += 1
                
            except Exception as e:
                logger.error(f"❌ 섹션 저장 실패 ({section.get('article_title')}): {str(e)}")
                failed_count += 1
                continue
        
        logger.info(f"MongoDB 저장 완료: 성공 {saved_count}개, 실패 {failed_count}개")
        return saved_count > 0
    
    except Exception as e:
        logger.error(f"MongoDB 저장 중 오류: {e}")
        return False


async def save_to_mongodb_async(sections_data: list, doc_title: str, doc_id: str, url: str,
                                sub_title: str = "", effective_date: str = None,
                                ofi_cls_cd: str = "350101",
                                executor=None) -> bool:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)
    
    try:
        result = await loop.run_in_executor(
            executor, 
            save_to_mongodb, 
            sections_data,
            doc_title, 
            doc_id, 
            url,
            sub_title,
            effective_date,
            ofi_cls_cd
        )
        return result
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False

async def scrape_and_save(url: str, output_dir: str, output_name: str, dept_code: str = None,
                          save_to_db: bool = True, save_jsonl: bool = False):
    """
    단일 법령해석 문서를 스크래핑하여 MongoDB에 저장합니다.
    unified_scraper_flow에서 호출하는 표준 함수입니다.
    
    Args:
        url: onclick 속성 또는 URL (dict 또는 str)
        output_dir: 출력 디렉토리
        output_name: 출력 파일명
        dept_code: 부처 코드
        save_to_db: MongoDB 저장 여부
        save_jsonl: JSONL 파일 저장 여부
    
    Returns:
        dict: {"status": "success", "doc_id": "...", ...}
    """
    # url이 dict인지 확인 (list_scraper에서 전달)
    if isinstance(url, dict):
        detail_url = url.get('url')
        onclick_attr = url.get('onclick')
        doc_seq = url.get('doc_seq')
        ofi_cls_cd = url.get('ofi_cls_cd', '350101')  # 기본값 설정
        logger.info(f"법령해석 문서를 스크래핑합니다: {detail_url or onclick_attr}")
    else:
        # 레거시: URL 문자열
        detail_url = url
        onclick_attr = None
        doc_seq = None
        ofi_cls_cd = '350101'  # 기본값
        logger.info(f"법령해석 문서를 스크래핑합니다 (URL): {url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            # 상세 URL이 있으면 직접 이동한다.
            if detail_url:
                logger.info(f"페이지로 이동 중: {detail_url}")
                await page.goto(detail_url, wait_until='networkidle', timeout=60000)
            elif onclick_attr:
                base_url = "https://www.law.go.kr/LSW/cgmExpcSc.do?menuId=11&subMenuId=729&tabMenuId=733&upperOfiClsCd=010501&ofiClsCd=350101"
                logger.info(f"목록 페이지로 이동: {base_url}")
                await page.goto(base_url, wait_until='networkidle', timeout=60000)
                
                # onclick 함수 실행
                js_function = onclick_attr.replace('javascript:', '').replace('return false;', '').strip()
                logger.info(f"onclick 실행: {js_function}")
                await page.evaluate(js_function)
                await asyncio.sleep(2)  # 페이지 로딩 대기
            else:
                # 레거시: 직접 URL로 이동
                logger.info(f"페이지로 이동 중: {url}")
                await page.goto(url, wait_until='networkidle', timeout=60000)
            
            # doc_seq 추출 (onclick이 없는 경우)
            if not doc_seq:
                # URL에서 추출
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(detail_url or url)
                query_params = parse_qs(parsed_url.query)
                doc_seq = (
                    query_params.get('cgmExpcDatSeq', [None])[0] or
                    query_params.get('cgmExpcSeq', [None])[0]
                )
                if not doc_seq:
                    logger.error(f"문서 ID를 찾을 수 없습니다: {detail_url or url}")
                    return {"status": "error", "error": "No doc_seq"}

            # doc_id는 doc_seq 사용 (고유 ID)
            doc_id = doc_seq

            source_url = detail_url or f"https://www.law.go.kr/LSW/cgmExpcInfoP.do?mode=2&cgmExpcDatSeq={doc_id}&ofiClsCd={ofi_cls_cd}"

            if await page.locator('#contentBody h2').count() > 0:
                doc_title = await page.locator('#contentBody h2').inner_text(timeout=30000)
                subtitle_locator = page.locator('#contentBody div.subtit1')
                subtitle = await subtitle_locator.inner_text() if await subtitle_locator.count() > 0 else ""
                department, doc_number, doc_date = parse_subtitle(subtitle)

                sub_title_parts = []
                if department and department != "정보 없음":
                    sub_title_parts.append(department)
                if doc_number and doc_number != "정보 없음":
                    sub_title_parts.append(doc_number)
                if doc_date and doc_date != "정보 없음":
                    sub_title_parts.append(doc_date)

                sub_title = ", ".join(sub_title_parts) if sub_title_parts else ""

                effective_date = datetime.now().strftime('%Y-%m-%d')
                if doc_date and doc_date != "정보 없음":
                    try:
                        effective_date = doc_date.replace(".", "-")
                    except Exception:
                        pass

                sections = {}
                content_elements = await page.locator('#contentBody h4, #contentBody .pty4').all()
                current_header = None

                for element in content_elements:
                    tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
                    element_id = await element.get_attribute('id')

                    if tag_name == 'h4' and element_id != 'expcNotice':
                        header_text = await element.inner_text()
                        current_header = header_text.strip().replace('【', '').replace('】', '')
                        if current_header and current_header not in sections:
                            sections[current_header] = ""
                    elif tag_name == 'p' and current_header:
                        paragraph_text = await element.inner_text()
                        sections[current_header] += paragraph_text.strip() + "\n"

                sections_data = split_sections_by_header(sections)
            else:
                body_text = await page.locator('body').inner_text(timeout=30000)
                parsed = _parse_taxlaw_body(body_text)
                if not parsed:
                    logger.error(f"문서 본문을 파싱할 수 없습니다: {detail_url or url}")
                    return {"status": "error", "error": "No parseable content", "doc_id": doc_id}
                doc_title = parsed["doc_title"]
                sub_title = parsed["sub_title"]
                effective_date = parsed["effective_date"]
                sections_data = parsed["sections_data"]

            logger.info(f"문서 제목: {doc_title}, ID: {doc_seq}")
            
            # MongoDB에 저장
            if save_to_db and sections_data:
                success = await save_to_mongodb_async(
                    sections_data,
                    doc_title=' '.join(doc_title.strip().split()),
                    doc_id=doc_id,
                    url=source_url,
                    sub_title=sub_title,
                    effective_date=effective_date,
                    ofi_cls_cd=ofi_cls_cd
                )
                if success:
                    logger.info(f"✅ MongoDB 저장 완료: {doc_id}")
                else:
                    logger.warning(f"⚠️ MongoDB 저장 실패: {doc_id}")
            
            # JSONL 파일로 저장 (옵션)
            if save_jsonl:
                scraped_data = {
                    "doc_id": doc_id,
                    "doc_title": ' '.join(doc_title.strip().split()),
                    "sub_title": sub_title,
                    "department": department,
                    "doc_number": doc_number,
                    "doc_date": doc_date,
                    "source_url": source_url,
                    "sections": sections,
                }
                
                safe_title = re.sub(r'[\\/*?:"<>|]', '_', doc_title)[:50]
                individual_file_path = os.path.join(output_dir, f"{output_name}_{safe_title}.jsonl")
                with open(individual_file_path, 'w', encoding='utf-8') as f:
                    f.write(json.dumps(scraped_data, ensure_ascii=False) + '\n')
                logger.info(f"JSONL 저장 완료: {os.path.basename(individual_file_path)}")
            
            logger.info(f"✅ 처리 완료: {doc_id}")
            
            return {
                "status": "success",
                "doc_id": doc_id,
                "title": doc_title,
                "sections_count": len(sections_data)
            }
        
        except Exception as e:
            logger.error(f"❌ 스크래핑 중 에러 발생: {e}", exc_info=True)
            
            # 에러 디버깅
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            screenshot_path = os.path.join(debug_dir, f'{output_name}_error_{timestamp}.png')
            html_path = os.path.join(debug_dir, f'{output_name}_error_{timestamp}.html')
            
            try:
                await page.screenshot(path=screenshot_path)
                logger.info(f"에러 스크린샷 저장: {screenshot_path}")
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(await page.content())
                logger.info(f"에러 HTML 저장: {html_path}")
            except Exception as debug_e:
                logger.error(f"디버그 파일 저장 중 추가 에러: {debug_e}")
            
            return {
                "status": "failed",
                "error": str(e),
                "doc_id": None
            }
        
        finally:
            await browser.close()
