import asyncio
import requests
from bs4 import BeautifulSoup, NavigableString
import json
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from collections import defaultdict

# --- 프로젝트 루트 경로 설정 ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

# --- 로거 설정 ---
from scripts.utils.html_text import block_text
from scripts.utils.logger_config import get_logger
from scripts.core.database.section_chunks import save_section_chunks
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
            # 문단 경계를 살린다(예전에는 공백으로 이어 붙여 긴 본문이 한 줄이 됐다).
            current_chunk["text"] += "\n\n" + block_text(element)

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
        loop = asyncio.get_running_loop()
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
            # 빈 본문을 저장하면 doc_id가 생겨 다음 날부터 재수집되지 않는다 → 실패로 돌려 재시도.
            logger.warning(f"⚠️  {doc_id}: 청크 파싱 결과가 비었습니다!")
            return {"doc_id": doc_id, "status": "failed", "error": "No chunks parsed"}
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
                return {
                    "doc_id": doc_id,
                    "status": "failed",
                    "error": "MongoDB save failed",
                }

        if process_chunks and save_jsonl and chunks:
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')
            save_to_file(chunks, chunk_filename)

        # 본문 없이 메타데이터 줄만 다시 저장한 날(재확인)은 업데이트로 세지 않는다.
        metadata_only = bool(chunks) and all((c.get("metadata") or {}).get("is_metadata_only") for c in chunks)
        return {"doc_id": doc_id, "status": "success", "action": "no_change" if metadata_only else "insert"}

    except Exception as e:
        logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
def save_to_file(data, filename):
    """파싱된 데이터를 JSONL 형식으로 파일에 저장합니다."""
    if not isinstance(data, list): data = [data]
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")


def save_case_chunks_to_mongodb(
    base_doc_id: str,
    doc_title: str,
    doc_subtitle: str,
    url: str,
    chunks: list
) -> bool:
    """판례 청크를 공용 섹션 저장으로 저장한다(동기 함수).

    판례만의 규칙: 본문 해시가 같으면 건너뛰고, effective 에 부제의 선고일을 넣고,
    is_active 필드가 없는 옛 행도 비활성화 대상으로 본다.
    """
    return save_section_chunks(
        "case", base_doc_id, doc_title, doc_subtitle, url, chunks,
        hash_skip=True,
        extra_chunk_meta={"effective": extract_judgment_date(doc_subtitle)},
        include_unflagged_rows=True,
    )


async def save_case_chunks_to_mongodb_async(
    base_doc_id: str,
    doc_title: str,
    doc_subtitle: str,
    url: str,
    chunks: list,
    executor=None
) -> bool:
    """MongoDB 청크 저장을 async로 처리"""
    loop = asyncio.get_running_loop()

    try:
        return await loop.run_in_executor(
            executor,
            save_case_chunks_to_mongodb,
            base_doc_id,
            doc_title,
            doc_subtitle,
            url,
            chunks
        )
    except Exception as e:
        logger.error(f"MongoDB 비동기 청크 저장 실패: {e}")
        return False
