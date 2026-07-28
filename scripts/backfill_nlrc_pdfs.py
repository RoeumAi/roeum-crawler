#!/usr/bin/env python3
"""Backfill NLRC judgment/mediation PDF text with resumable JSONL logging."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.core.database.mongo_client import get_mongo_db


COLLECTIONS = {
    "judgment": {
        "start_url": "https://nlrc.go.kr/nlrc/mainCase/judgment/index.do",
    },
    "mediation_case": {
        "start_url": "https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do",
    },
}


def is_pdf_complete(collection_name: str, doc_id: str) -> bool:
    collection = get_mongo_db()[collection_name]
    return collection.count_documents(
        {
            "doc_id": str(doc_id),
            "metadata.is_active": True,
            "metadata.content_source": {
                "$in": [
                    "pdf_text",
                    "pdf_ocr",
                    "hwp_text",
                    "hwp_ocr",
                    "hwpx_text",
                    "hwpx_ocr",
                ]
            },
            "metadata.attachment_file_id": {"$nin": [None, ""]},
        },
        limit=1,
    ) > 0


def append_result(log_path: Path, payload: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def backfill_collection(
    collection_name: str,
    *,
    limit: int | None,
    retry_completed: bool,
    log_path: Path,
) -> dict:
    config = COLLECTIONS[collection_name]
    list_module = import_module(
        f"scripts.{collection_name}.logic.list_scraper"
    )
    scraper_module = import_module(
        f"scripts.{collection_name}.logic.scraper"
    )
    items = await list_module.fetch_urls(config["start_url"], None)

    stats = {"total": len(items), "skipped": 0, "success": 0, "failed": 0}
    processed = 0

    for index, item in enumerate(items, 1):
        url = item.get("url", "") if isinstance(item, dict) else str(item)
        doc_id = (
            item.get("jgmt_sn")
            if isinstance(item, dict)
            else None
        )
        if not doc_id:
            from urllib.parse import parse_qs, urlparse

            doc_id = parse_qs(urlparse(url).query).get("jgmtSn", [None])[0]

        if not retry_completed and doc_id and is_pdf_complete(
            collection_name,
            str(doc_id),
        ):
            stats["skipped"] += 1
            continue

        if limit is not None and processed >= limit:
            break
        processed += 1

        started = datetime.now()
        print(
            f"[{collection_name} {index}/{len(items)}] {doc_id} 시작",
            flush=True,
        )
        result = await scraper_module.scrape_and_save(
            url,
            output_dir=str(PROJECT_ROOT / "data" / "output"),
            output_name=f"nlrc_pdf_{collection_name}_{doc_id}",
            save_to_db=True,
            save_jsonl=False,
        )
        status = result.get("status", "failed")
        stats["success" if status == "success" else "failed"] += 1
        record = {
            "timestamp": datetime.now().isoformat(),
            "collection": collection_name,
            "doc_id": doc_id,
            "url": url,
            "status": status,
            "elapsed_seconds": round(
                (datetime.now() - started).total_seconds(),
                2,
            ),
            "error": result.get("error", ""),
        }
        append_result(log_path, record)
        print(
            f"[{collection_name}] {doc_id} {status} "
            f"({record['elapsed_seconds']}초)",
            flush=True,
        )

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--collection",
        choices=["judgment", "mediation_case", "all"],
        default="all",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-completed", action="store_true")
    parser.add_argument("--log-file")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path(
        args.log_file
        or PROJECT_ROOT / "logs" / f"nlrc_pdf_backfill_{timestamp}.jsonl"
    )
    names = (
        list(COLLECTIONS)
        if args.collection == "all"
        else [args.collection]
    )

    all_stats = {}
    for name in names:
        all_stats[name] = await backfill_collection(
            name,
            limit=args.limit,
            retry_completed=args.retry_completed,
            log_path=log_path,
        )

    print(
        json.dumps(
            {"log_file": str(log_path), "stats": all_stats},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    asyncio.run(main())
