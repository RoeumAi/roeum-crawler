"""Backfill constitutional_decc sub_title with the dedicated case number.

Read-only by default. Pass --apply to update MongoDB.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from scripts.constitutional_decc.logic.scraper import _extract_case_number
from scripts.core.database.mongo_client import get_mongo_db


def fetch_case_number(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    return _extract_case_number(BeautifulSoup(response.text, "html.parser"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    collection = get_mongo_db()["constitutional_decc"]
    representatives = {}
    for document in collection.find(
        {"metadata.is_active": True},
        {"doc_id": 1, "metadata.source_url": 1},
    ):
        doc_id = str(document.get("doc_id") or "")
        url = (document.get("metadata") or {}).get("source_url")
        if doc_id and url:
            representatives.setdefault(doc_id, url)

    results = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_case_number, url): doc_id
            for doc_id, url in representatives.items()
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                case_number = future.result()
                if not case_number:
                    raise RuntimeError("case number not found")
                results[doc_id] = case_number
            except Exception as exc:
                errors[doc_id] = str(exc)

    matched_documents = 0
    changed_documents = 0
    for doc_id, case_number in results.items():
        query = {"doc_id": doc_id}
        matched_documents += collection.count_documents(query)
        changed_documents += collection.count_documents(
            {
                **query,
                "$or": [
                    {"sub_title": {"$ne": case_number}},
                    {"subtitle": {"$ne": case_number}},
                ],
            }
        )
        if args.apply:
            collection.update_many(
                query,
                {"$set": {"sub_title": case_number, "subtitle": case_number}},
            )

    print(
        {
            "mode": "apply" if args.apply else "dry-run",
            "case_count": len(representatives),
            "resolved_cases": len(results),
            "failed_cases": len(errors),
            "matched_documents": matched_documents,
            "would_change_documents": changed_documents,
            "errors": errors,
        }
    )


if __name__ == "__main__":
    main()
