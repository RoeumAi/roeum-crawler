"""
legislation_expc 상세 페이지 크롤러
requests + BeautifulSoup (JS 렌더링 불필요)
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup, NavigableString
import re
import os
import sys
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse, parse_qs

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.identifiers import section_chunk_id
from scripts.core.database.source_versioning import (
    build_mongo_set_fields,
    enrich_source_document,
    is_complete_chunk_save,
)
logger = get_logger(__name__, scraper_type='legislation_expc')


def clean_spaces(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r'([\[【]\d+[\]】])\n\s*', r'\1 ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()


def get_doc_id_from_url(url: str) -> str | None:
    try:
        params = parse_qs(urlparse(url).query)
        return params.get('expcSeq', [None])[0]
    except Exception:
        return None


SECTION_KEY_MAP = {
    "질의요지": "query",
    "회답": "answer",
    "이유": "reasoning",
    "관계 법령": "related_laws",
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
            for br in element.find_all("br"):
                br.replace_with("\n")
            current_chunk["text"] += " " + element.get_text()

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
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        try:
            from scripts.utils.tokenizer_utils import chunk_text_by_tokens, count_tokens
            use_tokenizer = True
        except Exception:
            use_tokenizer = False

        db = get_mongo_db()
        collection = db['legislation_expc']
        saved = 0
        failed = 0

        for chunk_idx, chunk in enumerate(chunks, 1):
            content = chunk.get('text', '')
            chunk_title = chunk.get('title', '')

            if use_tokenizer:
                try:
                    if count_tokens(content) > 8000:
                        sub_chunks = chunk_text_by_tokens(content, max_tokens=8000, overlap_tokens=200)
                    else:
                        sub_chunks = [content]
                except Exception:
                    sub_chunks = [content]
            else:
                sub_chunks = [content]

            for seq, sub_content in enumerate(sub_chunks, 1):
                token_cnt = None
                if use_tokenizer:
                    try:
                        token_cnt = count_tokens(sub_content)
                    except Exception:
                        pass

                chunk_meta = {
                    "source_url": url,
                    "source_type": "web",
                    "updated_at": datetime.now().isoformat(),
                    "is_active": True,
                    "chunk_index": chunk_idx,
                    "total_chunks": len(chunks),
                    "total_sub_chunks": len(sub_chunks),
                    **chunk.get("metadata", {}),
                }
                if token_cnt is not None:
                    chunk_meta["token_count"] = token_cnt

                chunk_doc = {
                    "chunk_id": section_chunk_id("legislation_expc", doc_id, chunk_title, seq),
                    "doc_id": doc_id,
                    "doc_type": chunk_title,
                    "title": doc_title,
                    "sub_title": doc_subtitle,
                    "article_number": str(seq),
                    "article_title": chunk_title,
                    "chunk_seq": seq,
                    "content": sub_content,
                }
                chunk_doc = enrich_source_document(chunk_doc, "legislation_expc")
                set_fields = build_mongo_set_fields(chunk_doc, chunk_meta)

                try:
                    collection.update_one(
                        {"doc_id": doc_id, "doc_type": chunk_title, "chunk_seq": seq},
                        {
                            "$set": set_fields,
                            "$setOnInsert": {"metadata.created_at": datetime.now().isoformat()},
                        },
                        upsert=True,
                    )
                    saved += 1
                except Exception as e:
                    logger.error(f"청크 저장 실패 {chunk_title}: {e}")
                    failed += 1

        logger.info(f"✅ {doc_id}: {saved}개 저장 (실패: {failed})")
        return is_complete_chunk_save(saved=saved, failed=failed)
    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {e}", exc_info=True)
        return False


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

        subtit = soup.select_one(".subtit1, .subtit2, div.subtit2")
        doc_subtitle = clean_spaces(subtit.get_text()) if subtit else ""
        doc_subtitle = re.sub(r'^\[(.+)\]$', r'\1', doc_subtitle.strip())

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

        return {"doc_id": doc_id, "status": "success", "action": "insert"}

    except Exception as e:
        logger.error(f"스크래핑 실패 {url}: {e}", exc_info=True)
        return {"doc_id": None, "status": "failed"}
