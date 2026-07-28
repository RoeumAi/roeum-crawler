"""
심의결정례 상세 스크래퍼

심의결정례(jgmtDcsnSeCd=67)는 nlrc.go.kr에서 별도 상세 페이지를 제공하지 않습니다.
목록에서 수집된 제목(title)과 날짜(registered_at)가 문서의 전체 콘텐츠입니다.

scrape_and_save()는 crawl.py에서 url_item dict를 받아
title과 registered_at을 직접 사용해 MongoDB에 저장합니다.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import document_chunk_id
from scripts.utils.reference_sub_title import extract_court_reference

logger = get_logger(__name__, scraper_type='decision')


def save_to_file(data: dict, filename: str) -> None:
    """JSONL 파일 저장"""
    directory = os.path.dirname(filename)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')
    logger.info(f"JSONL 저장: {filename}")


def save_to_mongodb(document_data: Dict) -> bool:
    """
    변경 감지 포함 MongoDB 저장

    UnifiedDocumentRepository.upsert_with_change_detection() 사용:
    - 신규: insert (is_active=True, version=1)
    - 내용 변경: 기존 비활성화 후 새 버전 insert
    - 변경 없음: metadata(last_check_at)만 업데이트
    """
    try:
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db, collection_name='decision')

        doc_id, action_result = repo.upsert_with_change_detection(document_data)

        action = action_result.get("action", "unknown")
        version = action_result.get("version", 1)

        if action == "insert":
            logger.info(f"💾 신규 저장: {doc_id} (v{version})")
        elif action == "new_version":
            changed = ', '.join(action_result.get('changed_fields', []))
            logger.info(f"✅ 새 버전: {doc_id} (v{version}) - 변경: {changed or 'metadata'}")
        elif action == "update_existing":
            logger.info(f"🔄 유지: {doc_id} (v{version}) - metadata만 업데이트")

        return True

    except Exception as e:
        logger.error(f"MongoDB 저장 실패: {e}")
        return False


async def save_to_mongodb_async(document_data: Dict, executor=None) -> bool:
    """MongoDB 저장 async 래퍼 (event loop blocking 방지)"""
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)
    try:
        return await loop.run_in_executor(executor, save_to_mongodb, document_data)
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False


def _build_document(jgmt_sn: str, title: str, registered_at: str, source_url: str) -> Dict:
    """
    심의결정례 문서 구성

    심의결정례는 상세 페이지가 없으므로 title = sub_title = content.
    title은 판정 요지의 핵심 문장을 담고 있어 그 자체로 의미있는 콘텐츠입니다.
    """
    return {
        "chunk_id": document_chunk_id("decision", jgmt_sn),
        "doc_id": jgmt_sn,
        "doc_type": "심의결정례",
        "article_number": "1",
        "title": title,
        "sub_title": extract_court_reference(title),
        "content": title,  # 심의결정례의 콘텐츠는 제목(요지) 그 자체
        "metadata": {
            "source_url": source_url,
            "source_type": "web",
            "effective": registered_at,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "is_active": True,
        }
    }


async def scrape_and_save(
    url: "str | dict",
    output_dir: str,
    output_name: str,
    dept_code: Optional[str] = None,
    save_to_db: bool = True,
    save_jsonl: bool = True
) -> Dict:
    """
    심의결정례 저장

    crawl.py에서 url_item dict 또는 URL 문자열을 받습니다.
    - dict: title, registered_at, jgmt_sn 등을 직접 사용 (선호 방식)
    - str: URL에서 jgmtSn만 파싱 가능하므로 title 없음 → 저장 불가

    Args:
        url: list_scraper가 반환한 url_item dict 또는 URL 문자열
        output_dir: JSONL 저장 경로
        output_name: 파일명 접두사
        dept_code: 부처 코드
        save_to_db: MongoDB 저장 여부
        save_jsonl: JSONL 파일 저장 여부

    Returns:
        {"doc_id": str, "status": "success"|"failed"}
    """
    # URL 또는 dict에서 필요한 데이터 추출
    if isinstance(url, dict):
        jgmt_sn = url.get("jgmt_sn", "")
        title = url.get("title", "")
        registered_at = url.get("registered_at", "")
        source_url = url.get("url", "")
    else:
        # 문자열 URL: jgmtSn은 파싱 가능하지만 title은 없음
        source_url = url
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        jgmt_sn = (params.get("jgmtSn") or [""])[0]
        title = ""
        registered_at = ""
        logger.warning(
            f"URL 문자열로 호출됨 (dict 권장): jgmtSn={jgmt_sn}. "
            "title 정보 없음 — crawl.py가 dict를 전달하는지 확인하세요."
        )

    if not jgmt_sn:
        logger.error(f"jgmt_sn 파싱 실패: {url}")
        return {"doc_id": None, "status": "failed"}

    if not title:
        logger.error(f"title 없음 (jgmt_sn={jgmt_sn}): dict로 호출해야 합니다.")
        return {"doc_id": jgmt_sn, "status": "failed"}

    logger.info(f"심의결정례 저장: jgmt_sn={jgmt_sn}, title={title[:60]}")

    try:
        document_data = _build_document(jgmt_sn, title, registered_at, source_url)

        if dept_code:
            document_data["metadata"]["dept_code"] = dept_code

        if save_jsonl:
            output_path = os.path.join(output_dir, f"{output_name}_decision_{jgmt_sn}.jsonl")
            save_to_file(document_data, output_path)

        if save_to_db:
            success = await save_to_mongodb_async(document_data)
            if not success:
                return {"doc_id": jgmt_sn, "status": "failed"}

        return {"doc_id": jgmt_sn, "status": "success"}

    except Exception as e:
        logger.error(f"심의결정례 저장 중 에러: {e}", exc_info=True)
        return {"doc_id": jgmt_sn, "status": "failed"}
