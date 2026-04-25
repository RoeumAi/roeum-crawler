"""
nodong.kr 행정해석 상세 페이지 스크래퍼
- 기존 interpretation 컬렉션에 동일 스키마로 저장
- doc_id: nodong_{document_srl}  (law.go.kr doc_seq와 충돌 방지)

상세 페이지 HTML 구조:
  <article>
    <div class="xe_content">
      <h2>제목</h2>
      <p style="text-align: right;">(근기 68207-390, 1999.10.20.)</p>
      <h3>질의</h3>
      <p>...</p>
      <h3>회시답변</h3>
      <p>...</p>
      <hr>            ← 여기서 본문 끝
      <h3>관련 정보</h3>  ← 제외
    </div>
  </article>
"""
import re
import os
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup, NavigableString

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='interpretation')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.nodong.kr/interpretation",
}

# hr 또는 이 h3가 나오면 본문 수집 종료
STOP_SECTIONS = {"관련 정보", "관련정보", "관련법률", "관련 법률"}


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_detail(html: str, url: str) -> dict | None:
    """
    상세 페이지 HTML을 파싱해 저장 가능한 dict 반환

    Returns:
        {
            "doc_srl": str,
            "title": str,
            "sub_title": str,       # "근기 68207-390, 1999.10.20."
            "doc_number": str,      # "근기 68207-390"
            "doc_date": str,        # "1999.10.20."
            "effective_date": str,  # "1999-10-20"
            "sections": {"질의": "...", "회시답변": "..."},
            "source_url": str,
        }
        None이면 파싱 실패
    """
    soup = BeautifulSoup(html, "html.parser")

    content_div = soup.select_one("article .xe_content")
    if not content_div:
        logger.warning("xe_content 컨테이너를 찾지 못했습니다.")
        return None

    # 섹션 헤더로 쓰이는 이름 (제목 fallback 시 제외)
    SECTION_HEADER_NAMES = {"질의", "회시답변", "회시 답변", "답변", "회신", "검토"}

    # 제목: h2 → 첫 번째 h3(stop/section 제외) → 첫 번째 일반 p 순서로 fallback
    title = ""
    h2 = content_div.select_one("h2")
    if h2:
        title = _clean(h2.get_text())
    if not title:
        for h3 in content_div.select("h3"):
            candidate = _clean(h3.get_text())
            if candidate not in STOP_SECTIONS and candidate not in SECTION_HEADER_NAMES:
                title = candidate
                break
    if not title:
        for p in content_div.select("p"):
            style = p.get("style", "")
            if "text-align: right" not in style and "text-align:right" not in style:
                candidate = _clean(p.get_text())
                if candidate:
                    title = candidate
                    break
    if not title:
        logger.warning("제목을 찾지 못했습니다.")
        return None

    # 번호+일자: <p style="text-align: right;">(근기 68207-390, 1999.10.20.)</p>
    doc_number = ""
    doc_date = ""
    sub_title = ""
    for p in content_div.select('p[style*="text-align: right"]'):
        raw = _clean(p.get_text()).strip("()")
        # "근기 68207-390, 1999.10.20." 또는 "1999.10.20." 형태
        date_match = re.search(r"(\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?)", raw)
        if date_match:
            doc_date = date_match.group(1).rstrip(".")
            # 날짜 앞쪽이 번호
            before_date = raw[:date_match.start()].strip().rstrip(",").strip()
            if before_date:
                doc_number = before_date
            sub_title = raw
            break

    # effective_date: "1999.10.20" → "1999-10-20"
    effective_date = datetime.now().strftime("%Y-%m-%d")
    if doc_date:
        try:
            parts = re.split(r"\.", doc_date.replace(" ", ""))
            parts = [p for p in parts if p]
            if len(parts) >= 3:
                effective_date = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        except Exception:
            pass

    # doc_srl from URL
    m = re.search(r"document_srl=(\d+)", url)
    doc_srl = m.group(1) if m else "unknown"

    # 섹션 파싱: h3 기준으로 분리, hr 또는 STOP_SECTIONS에서 종료
    SKIP_TAGS = {"h2", "h3", "h4"}  # 제목 태그는 본문으로 수집하지 않음
    sections = {}
    current_section = None

    for el in content_div.children:
        if isinstance(el, NavigableString):
            continue

        tag = el.name

        if tag == "hr":
            break

        if tag in ("h3", "h4"):
            section_name = _clean(el.get_text())
            if section_name in STOP_SECTIONS:
                break
            current_section = section_name
            if current_section not in sections:
                sections[current_section] = ""
            continue

        if tag == "h2":
            continue  # 제목 태그는 건너뜀

        if current_section is not None:
            # p, ul, ol, table, div 등 모든 본문 태그 수집
            text = _clean(el.get_text(separator="\n"))
            if text:
                sections[current_section] += text + "\n"

    # h3/h4 섹션이 없는 문서: 전체 내용을 "회시답변"으로 수집 (관련정보 제외)
    if not sections:
        body_parts = []
        for el in content_div.children:
            if isinstance(el, NavigableString):
                continue
            if el.name == "hr":
                break
            if el.name in SKIP_TAGS:
                # h3/h4가 stop section이면 중단
                if _clean(el.get_text()) in STOP_SECTIONS:
                    break
                continue
            # 우측 정렬 p는 번호/날짜이므로 제외
            if el.name == "p" and "text-align: right" in el.get("style", ""):
                continue
            text = _clean(el.get_text(separator="\n"))
            if text:
                body_parts.append(text)
        if body_parts:
            sections["회시답변"] = "\n".join(body_parts)

    # 제목과 동일한 이름의 섹션 키가 있으면 제거 (h3가 제목 역할을 했던 경우)
    sections.pop(title, None)

    if not sections:
        logger.warning(f"{doc_srl}: 섹션 파싱 결과 없음")
        return None

    return {
        "doc_srl": doc_srl,
        "title": title,
        "sub_title": sub_title,
        "doc_number": doc_number,
        "doc_date": doc_date,
        "effective_date": effective_date,
        "sections": {k: v.strip() for k, v in sections.items()},
        "source_url": url,
    }


def _build_mongo_docs(parsed: dict) -> list[dict]:
    """
    파싱 결과를 기존 interpretation 스키마의 문서 리스트로 변환
    섹션 하나당 문서 1개 (article_number로 구분)
    """
    doc_id = f"nodong_{parsed['doc_srl']}"
    sections = parsed["sections"]
    total = len(sections)
    docs = []

    for idx, (section_title, content) in enumerate(sections.items(), 1):
        docs.append({
            "doc_id": doc_id,
            "article_number": float(idx),
            "article_title": section_title,
            "title": parsed["title"],
            "sub_title": parsed["sub_title"],
            "content": content,
            "doc_type": "법령해석",
            "metadata": {
                "source_url": parsed["source_url"],
                "source_type": "web",
                "effective": parsed["effective_date"],
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "updated_at": datetime.now().isoformat(),
                "is_active": True,
                "article_index": idx,
                "total_articles": total,
                "origin": "nodong.kr",
            },
        })
    return docs


def _save_to_mongodb(mongo_docs: list[dict]) -> bool:
    """interpretation 컬렉션에 upsert"""
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        collection = db["interpretation"]

        saved = 0
        for doc in mongo_docs:
            result = collection.update_one(
                {"doc_id": doc["doc_id"], "article_number": doc["article_number"]},
                {"$set": doc},
                upsert=True,
            )
            saved += 1
            action = "신규" if result.upserted_id else "업데이트"
            logger.debug(f"  {action}: {doc['doc_id']} article_number={doc['article_number']}")

        logger.info(f"✅ MongoDB 저장 완료: {saved}개 섹션")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB 저장 실패: {e}", exc_info=True)
        return False


def scrape_and_save(url: str) -> dict:
    """
    단일 URL 스크래핑 후 MongoDB 저장

    Args:
        url: 상세 페이지 URL (str 또는 {"url": str, ...} dict)

    Returns:
        {"status": "success"|"failed", "doc_id": str}
    """
    if isinstance(url, dict):
        url = url.get("url", "")

    try:
        logger.info(f"요청: {url}")
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"HTTP 요청 실패: {e}")
        return {"status": "failed", "doc_id": None}

    parsed = _parse_detail(resp.text, url)
    if not parsed:
        return {"status": "failed", "doc_id": None}

    doc_id = f"nodong_{parsed['doc_srl']}"
    mongo_docs = _build_mongo_docs(parsed)

    if not mongo_docs:
        logger.warning(f"{doc_id}: 저장할 문서 없음")
        return {"status": "failed", "doc_id": doc_id}

    logger.info(f"  제목: {parsed['title']}")
    logger.info(f"  번호: {parsed['doc_number']} | 일자: {parsed['doc_date']}")
    logger.info(f"  섹션: {list(parsed['sections'].keys())}")

    success = _save_to_mongodb(mongo_docs)
    return {"status": "success" if success else "failed", "doc_id": doc_id}
