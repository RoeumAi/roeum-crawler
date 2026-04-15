import asyncio
from concurrent.futures import ThreadPoolExecutor
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup, NavigableString
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from collections import defaultdict
from datetime import datetime

# --- 프로젝트 루트 경로 설정 ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# --- 로거 설정 ---
from scripts.utils.logger_config import get_logger
logger = get_logger(__name__, scraper_type='case')

def clean_spaces(text: str) -> str:
    """텍스트에서 불필요한 공백과 줄바꿈을 정리합니다."""
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r'(［\d+］)\n\s*', r'\1 ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()

def get_doc_id_from_url(url: str) -> str | None:
    """URL에서 판례 고유 ID (precSeq)를 추출합니다."""
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        return query_params.get('precSeq', [None])[0]
    except Exception:
        return None

def extract_judgment_date(subtitle: str) -> str | None:
    """
    판례 subtitle에서 판결 선고 날짜를 추출합니다.
    예: "[대법원 2004. 3. 25. 선고 2003다67359 판결]" → "2004-03-25"
    """
    if not subtitle:
        return None
    
    # YYYY. MM. DD. 선고 패턴 찾기 (선고 앞에 날짜가 옴)
    match = re.search(r'(\d{4})\.\s+(\d{1,2})\.\s+(\d{1,2})\.\s+선고', subtitle)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    return None

def parse_case_law_content(html: str, doc_id: str, url: str, doc_title: str):
    """판례 상세 페이지의 HTML을 파싱하여 의미 단위의 청크(Chunks)로 분할합니다."""
    soup = BeautifulSoup(html, 'html.parser')

    chunks = []
    current_chunk = None
    section_key_map = {
        "판시사항": "issue", "판결요지": "summary", "참조조문": "references",
        "참조판례": "ref_cases", "연관판결": "related_cases", "인정근거": "basis_of_recognition",
        "원고": "plaintiff", "피고": "defendant",
        "원심판결": "lower_court", "제1심판결": "first_court", "변론종결": "hearing_close",
        "청구취지": "claim_summary", "항소취지": "appeal_summary",
        "청구취지 및 항소취지": "claim_and_appeal_summary", "상고이유": "appeal_reason",
        "주문": "order", "이유": "reasoning", "전문": "full_text"
    }

    # [추가] 중복된 섹션 키의 개수를 세기 위한 딕셔너리
    section_key_counts = defaultdict(int)

    for element in soup.find_all(True, recursive=False):
        element_text = element.get_text(strip=True)
        if not element_text:
            continue

        if element_text.startswith('【'):
            if current_chunk and current_chunk.get('text'):
                current_chunk['text'] = clean_spaces(current_chunk['text'])
                chunks.append(current_chunk)

            section_title = element_text.strip('【】')

            if len(section_title) == 3 and section_title[1] == ' ':
                section_title = section_title.replace(' ', '')

            section_key_raw = section_title.split(',')[0].split('(')[0].strip()
            section_key = section_key_map.get(section_key_raw, "section")

            # [수정] 중복 ID 방지를 위한 로직
            section_key_counts[section_key] += 1
            count = section_key_counts[section_key]

            # section_key가 'section'이거나, count가 1보다 클 때만 숫자를 붙임
            final_section_key = f"{section_key}_{count}" if section_key == "section" or count > 1 else section_key

            current_chunk = {
                "chunk_id": f"doc:{doc_id}:{final_section_key}",
                "doc_id": doc_id, "title": section_title, "text": "",
                "metadata": {"chapter": doc_title}, "source_url": url
            }
        elif current_chunk:
            for br in element.find_all("br"):
                br.replace_with("\n")
            current_chunk["text"] += " " + element.get_text()

    if current_chunk and current_chunk.get('text'):
        current_chunk['text'] = clean_spaces(current_chunk['text'])
        chunks.append(current_chunk)

    # 빈 텍스트를 가진 청크 최종 제거
    chunks = [chunk for chunk in chunks if chunk.get("text")]
    
    # [추가] 청크가 비어있으면, 최소한 제목과 URL은 포함하는 메타데이터 청크 생성
    if not chunks:
        logger.warning(f"⚠️  {doc_id}: 파싱된 청크가 없습니다. 메타데이터 청크 생성")
        chunks = [{
            "chunk_id": f"doc:{doc_id}:metadata",
            "doc_id": doc_id,
            "title": "메타데이터",
            "text": f"제목: {doc_title}\n출처: {url}",
            "metadata": {"chapter": doc_title, "is_metadata_only": True},
            "source_url": url
        }]
    
    return chunks

async def scrape_and_save(url: str, output_dir: str, output_name: str, process_chunks: bool = True, save_to_db: bool = True, dept_code: str = None, save_jsonl: bool = True):
    """
    웹페이지 컨텐츠를 가져와 document와 chunk로 나누어 파일로 저장합니다.
    process_chunks 파라미터가 False일 경우, document 정보만 저장하고 chunk 처리는 건너뜁니다.
    save_to_db 파라미터가 True일 경우, MongoDB에도 저장합니다 (async 처리).
    save_jsonl 파라미터가 False일 경우, JSONL 파일 저장을 건너뜁니다 (대용량 크롤링에서 디스크 절약).
    """
    if isinstance(url, dict):
        url = url.get("url", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=120000)

            # loadmask 대기 (로딩 인디케이터)
            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=2000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=60000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")
            
            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)

            logger.info("제목과 본문 콘텐츠가 로드되기를 기다립니다...")
            await page.locator('#contentBody h2').wait_for(timeout=60000)

            # [수정] 청크를 처리하지 않을 때는 conScroll을 기다릴 필요가 없으므로 분리
            if process_chunks:
                await page.locator('#conScroll').wait_for(timeout=60000)

            logger.info("콘텐츠 로드 완료.")

            doc_title = clean_spaces(await page.locator('#contentBody h2').text_content())
            doc_subtitle = clean_spaces(await page.locator('.subtit1, div.subtit2').first.text_content())
            # 앞뒤 대괄호 제거: "[대전고법 2006. 4. 19. ...]" → "대전고법 2006. 4. 19. ..."
            doc_subtitle = re.sub(r'^\[(.+)\]$', r'\1', doc_subtitle.strip())
            doc_id = get_doc_id_from_url(url)
            if not doc_id:
                logger.error("URL에서 doc_id(precSeq)를 추출하지 못했습니다.")
                return {"doc_id": None, "status": "failed"}

            # [수정] 먼저 content_html을 파싱해서 chunks 생성
            logger.info("청크(chunks) 데이터 처리 중...")
            content_html = await page.locator('#conScroll').inner_html()
            chunks = parse_case_law_content(content_html, doc_id, url, doc_title)
            logger.info(f"  ✅ {doc_id}: {len(chunks)}개 청크 파싱 완료")
            
            # [추가] 청크가 비었는지 확인
            if not chunks:
                logger.warning(f"⚠️  {doc_id}: 청크 파싱 결과가 비었습니다!")
            elif any(chunk.get('metadata', {}).get('is_metadata_only') for chunk in chunks):
                logger.warning(f"⚠️  {doc_id}: 메타데이터 청크만 생성됨 (실제 콘텐츠 파싱 실패)")

            document_data = {
                "doc_id": doc_id, 
                "title": doc_title,
                "subtitle": doc_subtitle, 
                "source_url": url
            }

            # JSONL 파일 저장 (save_jsonl=True일 때만)
            if save_jsonl:
                doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
                if document_data.get("title"):
                    save_to_file(document_data, doc_filename)
            
            # MongoDB에 저장 (async 처리)
            # chunks별로 개별 저장 (law scraper처럼)
            if save_to_db:
                logger.info(f"  📝 {doc_id}: MongoDB 저장 시작...")
                success = await save_case_chunks_to_mongodb_async(
                    base_doc_id=doc_id,
                    doc_title=doc_title,
                    doc_subtitle=doc_subtitle,
                    url=url,
                    chunks=chunks
                )
                if success:
                    logger.info(f"  ✅ {doc_id}: MongoDB 저장 성공")
                else:
                    logger.error(f"  ❌ {doc_id}: MongoDB 저장 실패")

            # chunks를 JSONL로 저장
            if process_chunks and save_jsonl and chunks:
                chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')
                save_to_file(chunks, chunk_filename)
            elif not chunks:
                logger.warning("페이지에서 청크 데이터를 찾지 못했습니다.")
            
            return {"doc_id": doc_id, "status": "success"}

        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            debug_dir = os.path.join(project_root, 'debug')
            os.makedirs(debug_dir, exist_ok=True)
            screenshot_path = os.path.join(debug_dir, f'{output_name}_error.png')
            html_path = os.path.join(debug_dir, f'{output_name}_error.html')
            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())
            logger.info(f"에러 스크린샷: {screenshot_path}, HTML: {html_path}")
            return {"doc_id": None, "status": "failed"}
        finally:
            await browser.close()
def save_to_file(data, filename):
    """파싱된 데이터를 JSONL 형식으로 파일에 저장합니다."""
    if not isinstance(data, list): data = [data]
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")


def save_case_to_mongodb(doc_id: str, title: str, subtitle: str, url: str, content: str = "") -> bool:
    """판례를 MongoDB에 저장합니다 (동기 함수) - 메타데이터용"""
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db['case']
        
        case_doc = {
            "doc_id": doc_id,
            "doc_type": "case",
            "title": title,
            "subtitle": subtitle,
            "source_url": url,
            "content": content,  # ← content 필드
            "metadata": {
                "source_type": "web",
                "created_at": datetime.now().isoformat(),
                "is_active": True
            }
        }
        
        # doc_id 기준으로 upsert
        result = collection.update_one(
            {"doc_id": doc_id},
            {"$set": case_doc},
            upsert=True
        )
        
        if result.upserted_id:
            logger.info(f"✅ 새 판례 생성: {doc_id}")
        elif result.modified_count > 0:
            logger.info(f"🔄 판례 업데이트: {doc_id}")
        else:
            logger.info(f"⏭️  판례 변경 없음: {doc_id}")
        
        return True
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {str(e)}")
        return False


def save_case_chunks_to_mongodb(
    base_doc_id: str, 
    doc_title: str, 
    doc_subtitle: str, 
    url: str, 
    chunks: list
) -> bool:
    """판례 청크들을 MongoDB에 저장합니다 (동기 함수) - 토큰 기반 청킹 적용"""
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        
        db = get_mongo_db()
        collection = db['case']
        
        # 토크나이저 로딩 시도 (실패해도 크롤링 계속)
        tokenizer_available = False
        try:
            from scripts.utils.tokenizer_utils import chunk_text_by_tokens, count_tokens
            tokenizer_available = True
            logger.debug("✅ 토크나이저 사용 가능")
        except Exception as e:
            logger.warning(f"⚠️  토크나이저 로딩 실패: {e}. 토큰 청킹 없이 진행합니다.")
        
        # chunks가 비어있으면 경고하고 메타데이터 청크 생성
        if not chunks:
            logger.warning(f"⚠️  {base_doc_id}: 청크가 없습니다. 메타데이터 청크 생성")
            chunks = [{
                "chunk_id": f"doc:{base_doc_id}:metadata",
                "doc_id": base_doc_id,
                "title": "메타데이터",
                "text": f"제목: {doc_title}\n출처: {url}",
                "metadata": {"is_metadata_only": True},
                "source_url": url
            }]
        
        saved_count = 0
        failed_count = 0
        judgment_date = extract_judgment_date(doc_subtitle)
        
        for chunk_idx, chunk in enumerate(chunks, 1):
            try:
                chunk_title = chunk.get('title', '')
                content = chunk.get('text', '')
                
                # 토크나이저가 있으면 토큰 기반 청킹, 없으면 그대로 저장
                if tokenizer_available:
                    try:
                        token_count = count_tokens(content)
                        
                        if token_count > 8000:
                            logger.info(f"   📏 {base_doc_id} - {chunk_title}: {token_count} 토큰 → 청킹 필요")
                            sub_chunks = chunk_text_by_tokens(content, max_tokens=8000, overlap_tokens=200)
                        else:
                            sub_chunks = [content]
                    except Exception as e:
                        logger.warning(f"   ⚠️  토큰 청킹 실패: {e}. 원본 그대로 저장합니다.")
                        sub_chunks = [content]
                else:
                    sub_chunks = [content]
                
                # 각 서브청크를 개별 문서로 저장
                for seq, sub_content in enumerate(sub_chunks, 1):
                    # token_count 계산 (토크나이저 있을 때만)
                    if tokenizer_available:
                        try:
                            token_cnt = count_tokens(sub_content)
                        except:
                            token_cnt = None
                    else:
                        token_cnt = None
                    
                    chunk_doc = {
                        "doc_id": base_doc_id,  # 모든 청크가 동일한 doc_id 공유
                        "doc_type": chunk_title,  # 섹션명을 doc_type으로 (예: "이유", "주문", "원심판결")
                        "title": doc_title,
                        "subtitle": doc_subtitle,
                        "chunk_seq": seq,  # 토큰 기반 청킹 순서
                        "content": sub_content,
                        "source_url": url,
                        "metadata": {
                            "source_type": "web",
                            "created_at": datetime.now().isoformat(),
                            "is_active": True,
                            "chunk_index": chunk_idx,
                            "total_chunks": len(chunks),
                            "total_sub_chunks": len(sub_chunks),
                            "effective": judgment_date,
                            **chunk.get('metadata', {})
                        }
                    }
                    
                    # token_count가 있으면 추가
                    if token_cnt is not None:
                        chunk_doc["metadata"]["token_count"] = token_cnt
                    
                    # doc_id + doc_type(섹션명) + chunk_seq 조합으로 upsert
                    result = collection.update_one(
                        {
                            "doc_id": base_doc_id,
                            "doc_type": chunk_title,
                            "chunk_seq": seq
                        },
                        {"$set": chunk_doc},
                        upsert=True
                    )
                    
                    saved_count += 1
                    
                    if len(sub_chunks) > 1:
                        logger.debug(f"   ✅ 청크 저장: {base_doc_id} - {chunk_title} (seq: {seq}/{len(sub_chunks)})")
                    else:
                        logger.debug(f"   ✅ 청크 저장: {base_doc_id} - {chunk_title}")
                
            except Exception as e:
                logger.error(f"   ❌ 청크 저장 실패 ({chunk.get('title', 'Unknown')}): {str(e)}")
                failed_count += 1
                continue
        
        if saved_count > 0:
            logger.info(f"✅ {base_doc_id}: {saved_count}개 청크 저장 완료 (실패: {failed_count})")
        else:
            logger.error(f"❌ {base_doc_id}: 청크 저장 완전 실패 (시도: {len(chunks)}, 성공: {saved_count})")
        
        return saved_count > 0
        
    except Exception as e:
        logger.error(f"MongoDB 청크 저장 실패: {str(e)}", exc_info=True)
        return False


async def save_case_to_mongodb_async(doc_id: str, title: str, subtitle: str, url: str, content: str = "", executor=None) -> bool:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지) - 메타데이터용"""
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)
    
    try:
        result = await loop.run_in_executor(executor, save_case_to_mongodb, doc_id, title, subtitle, url, content)
        return result
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False


async def save_case_chunks_to_mongodb_async(
    base_doc_id: str,
    doc_title: str,
    doc_subtitle: str,
    url: str,
    chunks: list,
    executor=None
) -> bool:
    """MongoDB 청크 저장을 async로 처리"""
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)
    
    try:
        result = await loop.run_in_executor(
            executor,
            save_case_chunks_to_mongodb,
            base_doc_id,
            doc_title,
            doc_subtitle,
            url,
            chunks
        )
        return result
    except Exception as e:
        logger.error(f"MongoDB 비동기 청크 저장 실패: {e}")
        return False

