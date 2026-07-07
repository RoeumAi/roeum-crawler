import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup, NavigableString
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from collections import defaultdict
from datetime import datetime
import hashlib

# --- 프로젝트 루트 경로 설정 ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# --- 로거 설정 ---
from scripts.utils.logger_config import get_logger
from scripts.core.identifiers import section_chunk_id
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

    # h5 중 당사자·행정 정보(원고·피고·원심판결 등)만 부모 청크에 텍스트로 병합하고,
    # 나머지(주문·이유·청구취지 등 실질 법률 내용)는 h5여도 별도 청크로 분리
    PARTY_ADMIN_KEYS = {
        "plaintiff",     # 원고
        "defendant",     # 피고
        "lower_court",   # 원심판결
        "first_court",   # 제1심판결
        "hearing_close", # 변론종결
        "section",       # 매핑되지 않는 당사자 소제목 (피고보조참가인 등)
    }

    for element in soup.children:
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text and current_chunk:
                current_chunk["text"] += " " + text
            continue
        if not getattr(element, 'name', None):
            continue
        element_text = element.get_text(strip=True)
        if not element_text:
            continue

        if element_text.startswith('【'):
            section_title = element_text.strip('【】')

            if len(section_title) == 3 and section_title[1] == ' ':
                section_title = section_title.replace(' ', '')

            section_key_raw = section_title.split(',')[0].split('(')[0].strip()
            section_key = section_key_map.get(section_key_raw, "section")

            # h4는 항상 새 청크; h5는 당사자/행정 소제목만 부모 청크에 병합
            is_party_admin_h5 = (element.name == 'h5') and (section_key in PARTY_ADMIN_KEYS)

            if not is_party_admin_h5:
                if current_chunk and current_chunk.get('text'):
                    current_chunk['text'] = clean_spaces(current_chunk['text'])
                    chunks.append(current_chunk)

                section_key_counts[section_key] += 1
                count = section_key_counts[section_key]
                final_section_key = f"{section_key}_{count}" if section_key == "section" or count > 1 else section_key

                current_chunk = {
                    "chunk_id": f"doc:{doc_id}:{final_section_key}",
                    "doc_id": doc_id, "title": section_title, "text": "",
                    "metadata": {"chapter": doc_title}, "source_url": url
                }
            elif current_chunk:
                # 당사자·행정 소제목은 전문 청크 텍스트에 병합
                current_chunk["text"] += "\n" + element_text
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
    requests로 판례 페이지를 가져와 청크로 분리 후 MongoDB에 저장합니다.
    law.go.kr 판례는 JS 렌더링 불필요 — requests + BeautifulSoup으로 처리합니다.
    """
    if isinstance(url, dict):
        url = url.get("url", "")
    try:
        logger.info(f"페이지 요청 중: {url}")
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        doc_id = get_doc_id_from_url(url)
        if not doc_id:
            logger.error("URL에서 doc_id(precSeq)를 추출하지 못했습니다.")
            return {"doc_id": None, "status": "failed"}

        h2 = soup.select_one("#contentBody h2")
        if not h2:
            logger.error(f"{doc_id}: #contentBody h2 없음 — 파싱 실패")
            return {"doc_id": None, "status": "failed"}
        doc_title = clean_spaces(h2.get_text())

        subtit = soup.select_one(".subtit1") or soup.select_one("div.subtit2")
        doc_subtitle = clean_spaces(subtit.get_text()) if subtit else ""
        doc_subtitle = re.sub(r'^\[(.+)\]$', r'\1', doc_subtitle.strip())

        con_scroll = soup.select_one("#conScroll")
        content_html = con_scroll.decode_contents() if con_scroll else ""
        chunks = parse_case_law_content(content_html, doc_id, url, doc_title)
        logger.info(f"✅ {doc_id}: {len(chunks)}개 청크 파싱 완료")

        if not chunks:
            logger.warning(f"⚠️  {doc_id}: 청크 파싱 결과가 비었습니다!")
        elif any(chunk.get('metadata', {}).get('is_metadata_only') for chunk in chunks):
            logger.warning(f"⚠️  {doc_id}: 메타데이터 청크만 생성됨 (실제 콘텐츠 파싱 실패)")

        if save_jsonl:
            doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
            save_to_file({"doc_id": doc_id, "title": doc_title, "subtitle": doc_subtitle, "source_url": url}, doc_filename)

        if save_to_db:
            logger.info(f"📝 {doc_id}: MongoDB 저장 시작...")
            success = await save_case_chunks_to_mongodb_async(
                base_doc_id=doc_id,
                doc_title=doc_title,
                doc_subtitle=doc_subtitle,
                url=url,
                chunks=chunks
            )
            if success:
                logger.info(f"✅ {doc_id}: MongoDB 저장 성공")
            else:
                logger.error(f"❌ {doc_id}: MongoDB 저장 실패")

        if process_chunks and save_jsonl and chunks:
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')
            save_to_file(chunks, chunk_filename)

        return {"doc_id": doc_id, "status": "success"}

    except Exception as e:
        logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed"}
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
            "content": content,
            "metadata.source_url": url,
            "metadata.source_type": "web",
            "metadata.updated_at": datetime.now().isoformat(),
            "metadata.is_active": True,
        }

        # doc_id 기준으로 upsert (created_at은 최초 insert 시에만 설정)
        result = collection.update_one(
            {"doc_id": doc_id},
            {
                "$set": case_doc,
                "$setOnInsert": {"metadata.created_at": datetime.now().isoformat()},
            },
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
                "metadata": {"source_url": url, "is_metadata_only": True},
            }]
        
        saved_count = 0
        no_change_count = 0
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
                    # content_hash로 변경 여부 판단 — 동일하면 last_check_at만 갱신
                    content_hash = hashlib.md5(sub_content.encode("utf-8")).hexdigest()
                    existing = collection.find_one(
                        {"doc_id": base_doc_id, "doc_type": chunk_title, "chunk_seq": seq},
                        {"content_hash": 1}
                    )
                    if existing and existing.get("content_hash") == content_hash:
                        collection.update_one(
                            {"doc_id": base_doc_id, "doc_type": chunk_title, "chunk_seq": seq},
                            {"$set": {
                                "chunk_id": section_chunk_id("case", base_doc_id, chunk_title, seq),
                                "sub_title": doc_subtitle,
                                "subtitle": doc_subtitle,
                                "article_number": str(seq),
                                "article_title": chunk_title,
                                "metadata.last_check_at": datetime.now().isoformat(),
                            }}
                        )
                        no_change_count += 1
                        continue

                    # token_count 계산 (토크나이저 있을 때만)
                    if tokenizer_available:
                        try:
                            token_cnt = count_tokens(sub_content)
                        except:
                            token_cnt = None
                    else:
                        token_cnt = None

                    chunk_meta = {
                        "source_url": url,
                        "source_type": "web",
                        "updated_at": datetime.now().isoformat(),
                        "is_active": True,
                        "chunk_index": chunk_idx,
                        "total_chunks": len(chunks),
                        "total_sub_chunks": len(sub_chunks),
                        "effective": judgment_date,
                        **chunk.get('metadata', {})
                    }
                    if token_cnt is not None:
                        chunk_meta["token_count"] = token_cnt

                    chunk_doc = {
                        "chunk_id": section_chunk_id("case", base_doc_id, chunk_title, seq),
                        "doc_id": base_doc_id,
                        "doc_type": chunk_title,
                        "title": doc_title,
                        "sub_title": doc_subtitle,
                        "subtitle": doc_subtitle,
                        "article_number": str(seq),
                        "article_title": chunk_title,
                        "chunk_seq": seq,
                        "content": sub_content,
                        "content_hash": content_hash,
                    }
                    set_fields = dict(chunk_doc)
                    set_fields.update({f"metadata.{k}": v for k, v in chunk_meta.items()})

                    collection.update_one(
                        {"doc_id": base_doc_id, "doc_type": chunk_title, "chunk_seq": seq},
                        {
                            "$set": set_fields,
                            "$setOnInsert": {"metadata.created_at": datetime.now().isoformat()},
                        },
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

        if saved_count > 0 or no_change_count > 0:
            logger.info(f"✅ {base_doc_id}: {saved_count}개 저장, {no_change_count}개 변경없음 (실패: {failed_count})")
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
