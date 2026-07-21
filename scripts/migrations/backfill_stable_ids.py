#!/usr/bin/env python3
"""
law/adrule의 metadata.law_id / metadata.adrule_id를 안정 ID로 정규화하는 1회성 backfill.

기존에는 list_scraper가 넘긴 title 텍스트를 law_id로 써왔기 때문에(같은 법령이라도
띄어쓰기·개정 등으로 값이 흔들릴 수 있음), law.go.kr 상세 API로 조회한 진짜 안정 ID로
전체 문서를 다시 채운다.

사용법:
    python3 scripts/migrations/backfill_stable_ids.py                # dry-run (기본값)
    python3 scripts/migrations/backfill_stable_ids.py --execute       # 실제 DB 갱신
    python3 scripts/migrations/backfill_stable_ids.py --collections law
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_stable_id import fetch_stable_law_id, fetch_stable_adrule_id

logger = get_logger(__name__, scraper_type='backfill_stable_ids')

RATE_LIMIT_DELAY_SECONDS = 0.3

COLLECTIONS = {
    "law": ("law_id", fetch_stable_law_id),
    "adrule": ("adrule_id", fetch_stable_adrule_id),
}


def plan_backfill(doc_ids: List[str], lookup_fn: Callable[[str], Optional[str]]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """doc_id별로 안정 ID를 조회해 (doc_id, stable_id) 갱신목록과 실패 doc_id 목록을 반환한다."""
    updates: List[Tuple[str, str]] = []
    failures: List[str] = []
    for doc_id in doc_ids:
        stable_id = lookup_fn(doc_id)
        if stable_id:
            updates.append((doc_id, stable_id))
        else:
            failures.append(doc_id)
    return updates, failures


def run_backfill(collection_name: str, dry_run: bool = True) -> Dict:
    from scripts.core.database.mongo_client import get_mongo_db

    id_field, lookup_fn = COLLECTIONS[collection_name]

    def rate_limited_lookup(doc_id: str) -> Optional[str]:
        result = lookup_fn(doc_id)
        time.sleep(RATE_LIMIT_DELAY_SECONDS)
        return result

    db = get_mongo_db()
    collection = db[collection_name]
    doc_ids = collection.distinct("doc_id")
    logger.info(f"[{collection_name}] distinct doc_id {len(doc_ids)}개 발견")

    updates, failures = plan_backfill(doc_ids, rate_limited_lookup)

    if dry_run:
        logger.info(f"[{collection_name}] --dry-run: {len(updates)}건 갱신 예정, {len(failures)}건 조회 실패")
        for doc_id, stable_id in updates[:20]:
            logger.info(f"  {doc_id} -> metadata.{id_field}={stable_id}")
    else:
        for doc_id, stable_id in updates:
            collection.update_many(
                {"doc_id": doc_id},
                {"$set": {f"metadata.{id_field}": stable_id}},
            )
        logger.info(f"[{collection_name}] {len(updates)}건 갱신 완료")

    if failures:
        logger.warning(f"[{collection_name}] 조회 실패 {len(failures)}건 (수동 확인 필요): {failures[:20]}")

    return {"collection": collection_name, "updated": len(updates), "failed": len(failures)}


def main():
    parser = argparse.ArgumentParser(description="law/adrule 안정 ID(law_id/adrule_id) backfill")
    parser.add_argument("--collections", nargs="+", choices=list(COLLECTIONS), default=list(COLLECTIONS))
    parser.add_argument("--execute", action="store_true", help="실제 DB 갱신 수행 (기본값: dry-run)")
    args = parser.parse_args()

    dry_run = not args.execute
    for collection_name in args.collections:
        run_backfill(collection_name, dry_run=dry_run)


if __name__ == "__main__":
    main()
