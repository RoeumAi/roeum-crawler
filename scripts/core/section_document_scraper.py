"""헌재결정례·법제처해석례·행정심판재결례 상세 페이지 수집 — 세 컬렉션이 같이 쓰는 구현.

law.go.kr 상세 페이지는 JS 렌더링이 필요 없어 requests + BeautifulSoup 으로 처리한다.
컬렉션마다 다른 점(URL 의 문서 번호 파라미터, 섹션 제목→키 매핑, 부제 필수 여부, 로거)은
`SectionScraperSpec` 에 담고, 각 컬렉션 패키지의 scraper.py 가 스펙을 넘겨 호출한다.
"""
import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup, NavigableString

from scripts.core.run_report import retry_detail_fetch_async
from scripts.utils.html_text import block_text
from scripts.core.database.section_chunks import save_section_chunks


@dataclass(frozen=True)
class SectionScraperSpec:
    collection: str                      # 저장 컬렉션 이름
    id_param: str                        # 상세 URL 에서 문서 번호를 읽는 쿼리 파라미터
    section_keys: dict                   # 섹션 제목 → 키(파싱된 청크의 chunk_id 에 쓰인다)
    logger: object                       # 컬렉션 패키지의 로거(로그 파일이 컬렉션별로 나뉜다)
    # 부제가 없으면 문서를 실패로 돌려야 하는 컬렉션만 (로그 문구, error 문구)를 채운다.
    missing_subtitle: tuple | None = None


def clean_spaces(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r'([\[【]\d+[\]】])\n\s*', r'\1 ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()


def extract_sub_title(soup: BeautifulSoup) -> str:
    """바깥 대괄호를 뺀 부제 전체를 돌려준다. 없으면 빈 문자열."""
    subtitle = soup.select_one(".subtit1, .subtit2, div.subtit2")
    value = clean_spaces(subtitle.get_text()) if subtitle else ""
    return re.sub(r"^\[(.+)\]$", r"\1", value.strip())


def get_doc_id_from_url(url: str, id_param: str) -> str | None:
    try:
        params = parse_qs(urlparse(url).query)
        return params.get(id_param, [None])[0]
    except Exception:
        return None


def parse_content(spec: SectionScraperSpec, html: str, doc_id: str, url: str, doc_title: str) -> list:
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
            section_key = spec.section_keys.get(raw_key, "section")

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
        spec.logger.warning(f"⚠️  {doc_id}: 파싱된 청크 없음 — 메타데이터 청크 생성")
        chunks = [{
            "chunk_id": f"doc:{doc_id}:metadata",
            "doc_id": doc_id,
            "title": "메타데이터",
            "text": f"제목: {doc_title}\n출처: {url}",
            "metadata": {"chapter": doc_title, "is_metadata_only": True},
            "source_url": url,
        }]

    return chunks


def save_to_mongodb(spec: SectionScraperSpec, doc_id: str, doc_title: str, doc_subtitle: str,
                    url: str, chunks: list) -> bool:
    return save_section_chunks(spec.collection, doc_id, doc_title, doc_subtitle, url, chunks)


async def scrape_and_save(spec: SectionScraperSpec, url, save_to_db: bool = True,
                          parse: Callable | None = None, save: Callable | None = None):
    """상세 페이지 하나를 받아 섹션으로 나누고 저장한다.

    parse(html, doc_id, url, doc_title) / save(doc_id, doc_title, doc_subtitle, url, chunks) 는
    각 패키지 모듈의 함수를 넘기는 자리다(없으면 이 모듈의 기본 구현).
    """
    logger = spec.logger
    parse = parse or (lambda *args: parse_content(spec, *args))
    save = save or (lambda *args: save_to_mongodb(spec, *args))
    if isinstance(url, dict):
        url = url.get("url", "")

    try:
        loop = asyncio.get_running_loop()

        def fetch():
            response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            return response

        # 네트워크 오류·시간 초과·5xx 만 다시 시도한다(4xx 는 바로 실패).
        resp = await retry_detail_fetch_async(lambda: loop.run_in_executor(None, fetch))
        soup = BeautifulSoup(resp.text, "html.parser")

        doc_id = get_doc_id_from_url(url, spec.id_param)
        if not doc_id:
            logger.error(f"doc_id 추출 실패: {url}")
            return {"doc_id": None, "status": "failed"}

        h2 = soup.select_one("#contentBody h2")
        doc_title = clean_spaces(h2.get_text()) if h2 else ""
        if not doc_title:
            title_el = soup.select_one(".tit_doc, h2, h3")
            doc_title = clean_spaces(title_el.get_text()) if title_el else url

        doc_subtitle = extract_sub_title(soup)
        if not doc_subtitle and spec.missing_subtitle:
            log_message, error_message = spec.missing_subtitle
            logger.error(f"{log_message}: {url}")
            return {"doc_id": doc_id, "status": "failed", "error": error_message}

        con_scroll = soup.select_one("#conScroll")
        content_html = con_scroll.decode_contents() if con_scroll else ""
        chunks = parse(content_html, doc_id, url, doc_title)
        logger.info(f"✅ {doc_id}: {len(chunks)}개 청크 파싱")

        if save_to_db:
            success = await loop.run_in_executor(
                None,
                lambda: save(doc_id, doc_title, doc_subtitle, url, chunks)
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
