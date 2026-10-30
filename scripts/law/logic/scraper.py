from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import urlparse, parse_qs
import sys
from datetime import datetime
from typing import Optional, Dict

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.text_cleanup import clean_text  # noqa: E402

from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import article_chunk_id
from scripts.core.lawgokr_document_saver import (  # noqa: E402,F401 - split_articles_by_number 는 기존 import 경로 유지
    run_in_executor_with_context, save_articles, save_to_file as save_json_lines, split_articles_by_number,
)
from scripts.core.lawgokr_page import dump_error_artifacts, load_rendered_page
from scripts.core.lawgokr_annex import annex_metadata, collect_annex_articles
from scripts.core.lawgokr_stable_id import fetch_record, stable_law_id as read_stable_law_id

logger = get_logger(__name__, scraper_type='law')

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
    # 못 읽으면 빈값으로 둔다. 오늘 날짜로 채우면 매일 시행일자가 바뀐 것으로 보여 조문 전체가 매일 새 버전이 된다.
    # (비면 scrape_and_save 가 목록의 시행일자로 채운다.)
    effective_date = ""
    try:
        # sub_title에서 먼저 시행일자 추출 시도
        if sub_title:
            # [시행 2026. 1. 1.] 패턴 매칭
            match = re.search(r'\[시행\s+(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})[.\]]*', sub_title)
            if match:
                effective_date = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        
        # sub_title에서 없으면 다른 메타데이터 태그 확인
        if not effective_date:
            effective_tag = soup.select_one('.law-info .effective-date, .ct_info')
            if effective_tag:
                effective_text = effective_tag.get_text(strip=True)
                # YYYY-MM-DD 형식 추출 시도
                match = re.search(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', effective_text)
                if match:
                    effective_date = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
    except Exception as e:
        logger.warning(f"효력일자 추출 중 오류: {e}")

    # 4. sub_title 정제
    if sub_title:
        # 대괄호 제거: [고용노동부령 ...] → 고용노동부령 ...
        sub_title = re.sub(r'\[([^\[\]]+)\]', r'\1', sub_title)
        
        # "시행 YYYY. MM. DD." 또는 "시행 YYYY. MM. DD" 패턴 제거
        # 예: "시행 2025. 12. 30. 고용노동부령 제458호" → "고용노동부령 제458호"
        sub_title = re.sub(r'시행\s+\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}[.]?\s*', '', sub_title)
        
        # 여러 공백을 하나로, 앞뒤 공백 제거
        sub_title = re.sub(r'\s+', ' ', sub_title).strip()
    
    # 5. 콘텐츠 (모든 청크를 통합) 추출
    content_div = soup.select_one('#contentBody, #conScroll, .law-view-content')
    if not content_div:
        logger.error("법률 내용이 담긴 메인 컨테이너를 찾지 못했습니다.")
        return None
    
    chunks = []
    
    for pgroup_div in content_div.select('div.pgroup'):
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
        # 본문 첫 줄이 "제N조(제목)   ①…" 로 붙어 나오므로 제목 뒤 공백을 줄바꿈으로 정규화
        final_text = clean_text(final_text, "law")
        
        # 청크 생성
        chunk_text = f"[{article_title}]\n{final_text}"
        chunks.append(chunk_text)
    
    # 6. 통합 문서 생성
    content = "\n\n".join(chunks) if chunks else ""

    if not content:
        # 조문 구조 없는 단문 법령 — contentBody 전체 텍스트를 그대로 사용
        raw_text = "\n".join(
            line.strip()
            for line in content_div.get_text(separator="\n").splitlines()
            if line.strip()
        )
        if raw_text:
            logger.info("조문 구조 없는 단문 법령 — 전체 텍스트로 저장합니다.")
            content = raw_text
        else:
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

def apply_list_effective(unified_doc: Dict, list_effective: Optional[str]) -> None:
    """본문에서 시행일자를 못 읽은 문서는 목록의 시행일자(YYYYMMDD)로 채운다.

    update 모드 필터가 비교하는 값과 같아서, 비워 두면 매 실행 '시행일자가 바뀜'으로 보고 다시 수집한다.
    99991231(시행일 미정)이나 형식이 다른 값은 쓰지 않고 빈값으로 둔다.
    """
    metadata = unified_doc.setdefault("metadata", {})
    if metadata.get("effective"):
        return
    digits = str(list_effective or "").replace("-", "")
    if re.fullmatch(r"\d{8}", digits) and digits != "99991231":
        metadata["effective"] = f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def save_to_file(data, filename):
    save_json_lines(data, filename, logger)


def _article_doc_builder(unified_doc: Dict, base_doc_id: str, law_id: str, dept_code: Optional[str]):
    def build_doc(idx: int, article: Dict, total: int) -> Dict:
        # doc_id는 법령 기본 ID (lsiSeq) 유지, article_number 필드로 조문 구분
        article_doc = {
            "chunk_id": article_chunk_id("law", base_doc_id, article['article_number']),
            "doc_id": base_doc_id,
            "doc_type": "법령",
            "article_number": article['article_number'],
            "article_number_numeric": article['article_number_numeric'],
            "article_title": article['article_title'],
            "title": unified_doc["title"],
            "sub_title": unified_doc["sub_title"],
            "content": article['content'],
            "metadata": {
                **unified_doc["metadata"],
                "article_index": idx,
                "total_articles": total,
            }
        }

        # 부처 코드 및 부처명
        if dept_code:
            article_doc["metadata"]["dept_code"] = dept_code
        if unified_doc.get("dept_name"):
            article_doc["metadata"]["dept_name"] = unified_doc["dept_name"]

        # 법령ID (같은 법령의 다른 버전 추적용, 안정 ID 우선)
        if law_id:
            article_doc["metadata"]["law_id"] = law_id

        # 시행예정 여부
        article_doc["metadata"]["is_upcoming"] = unified_doc.get("is_upcoming", False)
        article_doc["metadata"].update(annex_metadata(article))
        return article_doc
    return build_doc


def _log_action(doc_id: str, action_result: Dict, article_doc: Dict) -> None:
    if action_result["action"] == "insert":
        logger.info(
            f"💾 신규 저장: {doc_id} (v{action_result['version']})"
        )
    elif action_result["action"] == "new_version":
        logger.info(
            f"✅ 새 버전 생성: {doc_id} (v{action_result['version']}) "
            f"- 변경사항: {', '.join(action_result['changed_fields']) or 'metadata'}"
        )
    elif action_result["action"] == "update_existing":
        logger.info(
            f"🔄 기존 버전 유지: {doc_id} (v{action_result['version']}) "
            f"- 메타데이터만 업데이트"
        )


def save_to_mongodb(unified_doc: Dict, dept_code: Optional[str] = None) -> str:
    """
    통합 문서를 조별로 분리한 후 MongoDB에 저장 (변경 감지 기능 포함)

    처리 흐름:
    1. 통합 content를 정규표현식으로 조별 분리
    2. 각 조마다 독립 문서 생성 (doc_id + 조번호로 구분)
    3. 변경 감지 포함 upsert로 각 조 저장 (공통 루프: lawgokr_document_saver.save_articles)

    첫 저장: 신규 문서로 저장 (is_active=true)
    두 번째부터: 내용 변경 감지
      - 변경됨: 새 버전 생성 (기존 버전 비활성화)
      - 변경 없음: 메타데이터만 업데이트 (같은 버전 유지)

    Args:
        unified_doc: 통합 문서 딕셔너리 (parse_law_html 반환값)
        dept_code: 부처 코드 (옵션)

    Returns:
        "new_version" | "update_existing", 조문 일부라도 실패하면 "", 오류면 False
    """
    try:
        if not unified_doc:
            logger.error("저장할 문서가 없습니다.")
            return False

        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db)

        # 구 현행 버전 비활성화/승격은 refresh_current_status_flow(일일 배치)가 전담한다.
        # 여기서는 법령ID(안정 ID)만 조회해서 저장해둔다.
        base_doc_id = unified_doc["doc_id"]
        law_id = unified_doc.get("law_id", "")
        record = fetch_record("law", base_doc_id)  # 안정 ID·별표를 한 번에
        stable_law_id = read_stable_law_id(record)
        if stable_law_id:
            law_id = stable_law_id
        elif law_id:
            logger.warning(f"법령ID API 조회 실패, title 기반 law_id로 대체: doc_id={base_doc_id}")

        # 1. 조별로 분리
        articles = split_articles_by_number(unified_doc["content"])

        if not articles:
            logger.warning("분리된 조문이 없습니다. 통합 문서 그대로 저장합니다.")
            articles = [{
                "article_number": None,
                "article_number_numeric": None,
                "article_title": unified_doc.get("title", ""),
                "content": unified_doc["content"]
            }]

        # 별표(표가 대부분)는 본문 HTML 에 없어 Open API 의 원본 파일로 따로 읽는다.
        annex_rows, annex_failed = collect_annex_articles("law", record, logger)
        articles = articles + annex_rows

        # 2. 각 조마다 독립 문서 생성 및 저장
        return save_articles(
            repo, articles,
            build_doc=_article_doc_builder(unified_doc, base_doc_id, law_id, dept_code),
            log_action=_log_action,
            source_url=unified_doc.get("metadata", {}).get("source_url"),
            annex_failed=annex_failed,
            logger=logger,
        )

    except Exception as e:
        logger.error(f"MongoDB 저장 중 오류 발생: {str(e)}")
        return False


async def save_to_mongodb_async(unified_doc: Dict, dept_code: Optional[str] = None, executor=None) -> str:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    try:
        return await run_in_executor_with_context(executor, save_to_mongodb, unified_doc, dept_code)
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return ""


async def scrape_and_save(
    url: str,
    output_dir: str,
    output_name: str,
    dept_code: Optional[str] = None,
    save_to_db: bool = True,
    save_jsonl: bool = True
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
    dept_name = None
    law_id = None
    is_upcoming = False
    list_effective = None
    if isinstance(url, dict):
        list_effective = url.get("effective")
        dept_name = url.get("dept_name", "")
        law_id = url.get("law_id", "")
        is_upcoming = bool(url.get("is_upcoming", False))
        url = url.get("url", "")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        unified_doc = None
        try:
            html = await load_rendered_page(page, url, logger, goto_timeout=60000,
                                            mask_visible_timeout=10000, mask_hidden_timeout=30000)
            logger.info("데이터 파싱 중...")
            unified_doc = parse_law_html(html, url)
            if unified_doc:
                apply_list_effective(unified_doc, list_effective)
                if dept_name:
                    unified_doc["dept_name"] = dept_name
                if law_id:
                    unified_doc["law_id"] = law_id
                unified_doc["is_upcoming"] = is_upcoming

            # JSONL 파일로 저장
            if save_jsonl and unified_doc:
                doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
                save_to_file(unified_doc, doc_filename)
                logger.info(f"✅ JSONL 파일 저장: {doc_filename}")
            elif not unified_doc:
                logger.warning("문서 파싱 실패: JSONL 파일이 저장되지 않았습니다.")

            # MongoDB에 저장 (async 처리)
            if save_to_db and unified_doc:
                _law_action = await save_to_mongodb_async(unified_doc, dept_code)

        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            screenshot_path, html_path = await dump_error_artifacts(page, output_name, project_root)
            logger.info(f"에러 발생 시점의 스크린샷: {screenshot_path}")
            logger.info(f"에러 발생 시점의 HTML: {html_path}")
            return {"doc_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
        finally:
            await browser.close()

        # 결과 반환
        if not unified_doc:
            return {"doc_id": None, "status": "failed", "error": "문서 파싱 실패"}
        if save_to_db and not _law_action:
            # 저장 실패(조문 일부 포함)를 성공으로 세지 않는다 → failedTargets·Discord 요약에 드러난다.
            return {"doc_id": unified_doc.get("doc_id"), "status": "failed", "error": "MongoDB 저장 실패"}
        return {"doc_id": unified_doc.get("doc_id"), "status": "success", "action": _law_action if save_to_db else "insert"}
