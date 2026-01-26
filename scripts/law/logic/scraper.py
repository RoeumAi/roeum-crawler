import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from datetime import datetime
from typing import Optional, Dict, List, Tuple

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository

logger = get_logger(__name__, scraper_type='law')

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def parse_law_html(html: str, url: str) -> Optional[Dict]:
    """
    법령 HTML 파싱 (새로운 통합 스키마)
    
    반환:
    - 통합 문서 객체 또는 None
    {
      "doc_id": "123",
      "doc_type": "법령",
      "title": "제목",
      "sub_title": "부제",
      "content": "[조문1]\n내용1\n\n[조문2]\n내용2...",
      "metadata": {
        "source_url": "url",
        "source_type": "web",
        "effective": "2026-01-20",
        "created_at": "2026-01-20",
        "is_active": true
      }
    }
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. 문서 ID 추출
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    doc_id_keys = ['lsiSeq', 'lsId']
    doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
    
    if not doc_id:
        logger.warning(f"URL에서 doc_id를 찾을 수 없습니다. URL: {url}")
        return None
    
    # 2. 기본 정보 추출
    doc_title_tag = soup.select_one('#conTop h2')
    title = doc_title_tag.get_text(strip=True) if doc_title_tag else ""
    
    doc_subtitle_tag = soup.select_one('div.ct_sub')
    sub_title = doc_subtitle_tag.get_text(strip=True) if doc_subtitle_tag else ""
    
    if not title:
        logger.warning("문서 제목을 찾지 못했습니다.")
        return None
    
    # 3. 효력일자 추출 (sub_title 또는 메타데이터에서)
    effective_date = datetime.now().strftime('%Y-%m-%d')
    try:
        # sub_title에서 먼저 시행일자 추출 시도
        if sub_title:
            # [시행 2026. 1. 1.] 패턴 매칭
            match = re.search(r'\[시행\s+(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})[.\]]*', sub_title)
            if match:
                effective_date = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        
        # sub_title에서 없으면 다른 메타데이터 태그 확인
        if effective_date == datetime.now().strftime('%Y-%m-%d'):
            effective_tag = soup.select_one('.law-info .effective-date, .ct_info')
            if effective_tag:
                effective_text = effective_tag.get_text(strip=True)
                # YYYY-MM-DD 형식 추출 시도
                match = re.search(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', effective_text)
                if match:
                    effective_date = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
    except:
        pass
    
    # 4. sub_title 정제 (대괄호 제거)
    if sub_title:
        # [고용노동부령 ...] 형태의 대괄호 제거
        sub_title = re.sub(r'\[([^\[\]]+)\]', r'\1', sub_title)
        # 시행일자와 법령번호 사이의 여러 공백을 하나로
        sub_title = re.sub(r'\s+', ' ', sub_title).strip()
    
    # 5. 콘텐츠 (모든 청크를 통합) 추출
    content_div = soup.select_one('#contentBody, #conScroll, .law-view-content')
    if not content_div:
        logger.error("법률 내용이 담긴 메인 컨테이너를 찾지 못했습니다.")
        return None
    
    chunks = []
    current_chapter = ""
    
    for pgroup_div in content_div.select('div.pgroup'):
        chapter_tag = pgroup_div.select_one('p.gtit')
        if chapter_tag:
            chapter_text = chapter_tag.get_text(strip=True)
            if re.match(r'^제\s*\d+\s*장', chapter_text):
                current_chapter = chapter_text
        
        title_tag = pgroup_div.select_one('span.bl label')
        if not title_tag:
            continue
        
        article_title = title_tag.get_text(strip=True)
        article_id_num = "".join(filter(str.isdigit, article_title.split('(')[0]))
        
        if not article_id_num:
            continue
        
        # HTML 태그 제거
        for a_tag in pgroup_div.find_all('a'):
            a_tag.unwrap()
        
        temp_soup = BeautifulSoup(str(pgroup_div), 'html.parser')
        for tag in temp_soup.find_all(['p', 'br']):
            tag.append('\n')
        
        text_with_newlines = temp_soup.get_text(separator="")
        final_text = "\n".join(line.strip() for line in text_with_newlines.splitlines() if line.strip())
        
        # 청크 생성
        chunk_text = f"[{article_title}]\n{final_text}"
        chunks.append(chunk_text)
    
    # 6. 통합 문서 생성
    content = "\n\n".join(chunks) if chunks else ""
    
    if not content:
        logger.warning("문서 내용을 찾지 못했습니다.")
        return None
    
    unified_doc = {
        "doc_id": doc_id,
        "doc_type": "법령",
        "title": title,
        "sub_title": sub_title,
        "content": content,
        "metadata": {
            "source_url": url,
            "source_type": "web",
            "effective": effective_date,
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "is_active": True
        }
    }
    
    return unified_doc

def save_to_file(data, filename):
    if not isinstance(data, list): data = [data]
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        logger.info(f"'{directory}' 폴더를 생성했습니다.")
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")


def save_to_mongodb(unified_doc: Dict, dept_code: Optional[str] = None) -> bool:
    """
    통합 문서를 MongoDB에 저장 (변경 감지 기능 포함)
    
    첫 저장: 신규 문서로 저장 (is_active=true)
    두 번째부터: 내용 변경 감지
      - 변경됨: 새 버전 생성 (기존 버전 비활성화)
      - 변경 없음: 메타데이터만 업데이트 (같은 버전 유지)
    
    Args:
        unified_doc: 통합 문서 딕셔너리 (parse_law_html 반환값)
        dept_code: 부처 코드 (옵션)
    
    Returns:
        저장 성공 여부
    """
    try:
        if not unified_doc:
            logger.error("저장할 문서가 없습니다.")
            return False
        
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db)
        
        # 부처 코드가 있으면 메타데이터에 추가
        if dept_code:
            unified_doc["metadata"]["dept_code"] = dept_code
        
        # 변경 감지 포함 upsert
        doc_id, action_result = repo.upsert_with_change_detection(unified_doc)
        
        # 결과에 따른 로깅
        if action_result["action"] == "new_version":
            logger.info(
                f"✅ 새 버전 생성: {doc_id} (v{action_result['version']}) "
                f"- 변경사항: {', '.join(action_result['changed_fields']) or 'metadata'}"
            )
        elif action_result["action"] == "update_existing":
            logger.info(
                f"🔄 기존 버전 유지: {doc_id} (v{action_result['version']}) "
                f"- 메타데이터만 업데이트"
            )
        
        return True
            
    except Exception as e:
        logger.error(f"MongoDB 저장 중 오류 발생: {str(e)}")
        return False


async def scrape_and_save(
    url: str,
    output_dir: str,
    output_name: str,
    dept_code: Optional[str] = None,
    save_to_db: bool = True
):
    """
    Playwright로 크롤링하고 JSONL + MongoDB에 저장
    
    인자:
    - url: 크롤링할 URL
    - output_dir: JSONL 파일 저장 디렉토리
    - output_name: 파일 이름
    - dept_code: 부처 코드 (MongoDB 저장 시 필요)
    - save_to_db: MongoDB에 저장할지 여부 (기본값: True)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")
            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)
            logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()
            logger.info("데이터 파싱 중...")
            unified_doc = parse_law_html(html, url)

            # JSONL 파일로 저장
            if unified_doc:
                doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
                save_to_file(unified_doc, doc_filename)
                logger.info(f"✅ JSONL 파일 저장: {doc_filename}")
            else:
                logger.warning("문서 파싱 실패: JSONL 파일이 저장되지 않았습니다.")
            
            # MongoDB에 저장
            if save_to_db and unified_doc:
                save_to_mongodb(unified_doc, dept_code)
                
        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            error_dir = os.path.join(project_root, 'debug')
            if not os.path.exists(error_dir):
                os.makedirs(error_dir)
            screenshot_path = os.path.join(error_dir, f'{output_name}_error.png')
            html_path = os.path.join(error_dir, f'{output_name}_error.html')
            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"에러 발생 시점의 스크린샷: {screenshot_path}")
            logger.info(f"에러 발생 시점의 HTML: {html_path}")
        finally:
            await browser.close()


async def scrape_only(
    url: str,
    output_dir: str,
    output_name: str,
    dept_code: Optional[str] = None
) -> Optional[Dict]:
    """
    Playwright로 크롤링하고 문서만 반환 (MongoDB 저장하지 않음)
    
    STEP 3 개선을 위해 추가됨 - Flow 레벨에서 centralized 저장 처리
    
    인자:
    - url: 크롤링할 URL
    - output_dir: JSONL 파일 저장 디렉토리
    - output_name: 파일 이름
    - dept_code: 부처 코드 (메타데이터에만 포함)
    
    반환: 통합 문서 객체 또는 None
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=10000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=30000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")
            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)
            logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()
            logger.info("데이터 파싱 중...")
            unified_doc = parse_law_html(html, url)

            # JSONL 파일로 저장 (로컬 저장소)
            if unified_doc:
                doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
                save_to_file(unified_doc, doc_filename)
                logger.info(f"✅ JSONL 파일 저장: {doc_filename}")
                
                # 부처 코드 추가
                if dept_code:
                    unified_doc["metadata"]["dept_code"] = dept_code
            else:
                logger.warning("문서 파싱 실패")
            
            return unified_doc
                
        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            error_dir = os.path.join(project_root, 'debug')
            if not os.path.exists(error_dir):
                os.makedirs(error_dir)
            screenshot_path = os.path.join(error_dir, f'{output_name}_error.png')
            html_path = os.path.join(error_dir, f'{output_name}_error.html')
            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"에러 발생 시점의 스크린샷: {screenshot_path}")
            logger.info(f"에러 발생 시점의 HTML: {html_path}")
            return None
        finally:
            await browser.close()

# 3. if __name__ == "__main__": 블록 전체 삭제