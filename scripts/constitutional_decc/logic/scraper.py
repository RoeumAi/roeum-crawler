"""
constitutional_decc 상세 페이지 크롤러
requests + BeautifulSoup (JS 렌더링 불필요)
"""
import asyncio
import requests
from bs4 import BeautifulSoup, NavigableString
import re
import os
import sys
from collections import defaultdict
from urllib.parse import urlparse, parse_qs

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.html_text import block_text
from scripts.utils.logger_config import get_logger
from scripts.core.database.section_chunks import save_section_chunks
logger = get_logger(__name__, scraper_type='constitutional_decc')


def clean_spaces(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r'([\[【]\d+[\]】])\n\s*', r'\1 ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()


def _extract_sub_title(soup: BeautifulSoup) -> str:
    """Extract the full constitutional decision subtitle without outer brackets."""
    subtitle = soup.select_one(".subtit1, .subtit2, div.subtit2")
    value = clean_spaces(subtitle.get_text()) if subtitle else ""
    return re.sub(r"^\[(.+)\]$", r"\1", value.strip())


def get_doc_id_from_url(url: str) -> str | None:
    try:
        params = parse_qs(urlparse(url).query)
        return params.get('detcSeq', [None])[0]
    except Exception:
        return None


SECTION_KEY_MAP = {
    "판시사항": "issue",
    "결정요지": "summary",
    "심판대상조문": "subject_provisions",
    "참조조문": "references",
    "참조판례": "ref_cases",
    "전문": "full_text",
    "당사자": "parties",
    "주문": "order",
    "이유": "reasoning",
    "청구취지": "claim_summary",
    "청구인": "claimant",
    "피청구인": "respondent",
}


def parse_content(html: str, doc_id: str, url: str, doc_title: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    chunks = []
    current_chunk = None
    section_key_counts = defaultdict(int)

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
            raw_key = section_title.split(',')[0].split('(')[0].strip()
            section_key = SECTION_KEY_MAP.get(raw_key, "section")

            if current_chunk and current_chunk.get('text'):
                current_chunk['text'] = clean_spaces(current_chunk['text'])
                chunks.append(current_chunk)

            section_key_counts[section_key] += 1
            count = section_key_counts[section_key]
            final_key = f"{section_key}_{count}" if section_key == "section" or count > 1 else section_key

            current_chunk = {
                "chunk_id": f"doc:{doc_id}:{final_key}",
                "doc_id": doc_id,
                "title": section_title,
                "text": "",
                "metadata": {"chapter": doc_title},
                "source_url": url,
            }
        elif current_chunk:
            # 문단 경계를 살린다(예전에는 공백으로 이어 붙여 긴 본문이 한 줄이 됐다).
            current_chunk["text"] += "\n\n" + block_text(element)

    if current_chunk and current_chunk.get('text'):
        current_chunk['text'] = clean_spaces(current_chunk['text'])
        chunks.append(current_chunk)

    chunks = [c for c in chunks if c.get("text")]

    if not chunks:
        logger.warning(f"⚠️  {doc_id}: 파싱된 청크 없음 — 메타데이터 청크 생성")
        chunks = [{
            "chunk_id": f"doc:{doc_id}:metadata",
            "doc_id": doc_id,
            "title": "메타데이터",
            "text": f"제목: {doc_title}\n출처: {url}",
            "metadata": {"chapter": doc_title, "is_metadata_only": True},
            "source_url": url,
        }]

    return chunks


def save_to_mongodb(doc_id: str, doc_title: str, doc_subtitle: str, url: str, chunks: list) -> bool:
    return save_section_chunks("constitutional_decc", doc_id, doc_title, doc_subtitle, url, chunks)


async def scrape_and_save(url, output_dir: str = "", output_name: str = "",
                          process_chunks: bool = True, save_to_db: bool = True,
                          dept_code: str = None, save_jsonl: bool = True):
    if isinstance(url, dict):
        url = url.get("url", "")

    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        doc_id = get_doc_id_from_url(url)
        if not doc_id:
            logger.error(f"doc_id 추출 실패: {url}")
            return {"doc_id": None, "status": "failed"}

        h2 = soup.select_one("#contentBody h2")
        doc_title = clean_spaces(h2.get_text()) if h2 else ""
        if not doc_title:
            title_el = soup.select_one(".tit_doc, h2, h3")
            doc_title = clean_spaces(title_el.get_text()) if title_el else url

        doc_subtitle = _extract_sub_title(soup)
        if not doc_subtitle:
            logger.error(f"헌재결정례 부제 추출 실패: {url}")
            return {
                "doc_id": doc_id,
                "status": "failed",
                "error": "Constitutional decision subtitle extraction failed",
            }

        con_scroll = soup.select_one("#conScroll")
        content_html = con_scroll.decode_contents() if con_scroll else ""
        chunks = parse_content(content_html, doc_id, url, doc_title)
        logger.info(f"✅ {doc_id}: {len(chunks)}개 청크 파싱")

        if save_to_db:
            success = await loop.run_in_executor(
                None,
                lambda: save_to_mongodb(doc_id, doc_title, doc_subtitle, url, chunks)
            )
            if not success:
                return {
                    "doc_id": doc_id,
                    "status": "failed",
                    "error": "MongoDB save failed",
                }

        # 본문 없이 메타데이터 줄만 다시 저장한 날(재확인)은 업데이트로 세지 않는다.
        metadata_only = bool(chunks) and all((c.get("metadata") or {}).get("is_metadata_only") for c in chunks)
        return {"doc_id": doc_id, "status": "success", "action": "no_change" if metadata_only else "insert"}

    except Exception as e:
        logger.error(f"스크래핑 실패 {url}: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
