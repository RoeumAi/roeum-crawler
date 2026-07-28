"""Restore constitutional_decc sub_title from the official full subtitle.

Read-only by default. Pass --apply to update MongoDB and remove the duplicate
legacy ``subtitle`` field from this collection.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from scripts.constitutional_decc.logic.scraper import _extract_sub_title
from scripts.core.database.mongo_client import get_mongo_db


def fetch_sub_title(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    return _extract_sub_title(BeautifulSoup(response.text, "html.parser"))


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
            executor.submit(fetch_sub_title, url): doc_id
            for doc_id, url in representatives.items()
        }
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                sub_title = future.result()
                if not sub_title:
                    raise RuntimeError("sub_title not found")
                results[doc_id] = sub_title
            except Exception as exc:
                errors[doc_id] = str(exc)

    matched_documents = 0
    changed_documents = 0
    for doc_id, sub_title in results.items():
        query = {"doc_id": doc_id}
        matched_documents += collection.count_documents(query)
        changed_documents += collection.count_documents(
            {
                **query,
                "$or": [
                    {"sub_title": {"$ne": sub_title}},
                    {"subtitle": {"$exists": True}},
                ],
            }
        )
        if args.apply:
            collection.update_many(
                query,
                {
                    "$set": {"sub_title": sub_title},
                    "$unset": {"subtitle": ""},
                },
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
