"""Prune interpretation collection to MOEL-related central ministry interpretations."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pymongo import UpdateMany
from pymongo.errors import PyMongoError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.core.database.mongo_client import get_mongo_db

LIST_URL = "https://www.law.go.kr/LSW/cgmExpcAstScListR.do"
DETAIL_BASE = "https://www.law.go.kr/LSW/"
ORG_CODE = "1492000"
PAGE_SIZE = 50


def fetch_page(page: int) -> str:
    resp = requests.post(
        LIST_URL,
        data={
            "lsNm": "",
            "lsFdCd": "",
            "lsClsCd": "",
            "cptOfi": ORG_CODE,
            "chrIdx": "0",
            "pageIndex": str(page),
            "sortBy": "LS_NM_KO",
            "sortType": "",
            "mode": "",
            "cptOfiDivCd": "",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def parse_total(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".tit2")
    text = title.get_text(" ", strip=True) if title else ""
    match = re.search(r"([0-9,]+)", text)
    return int(match.group(1).replace(",", "")) if match else 0


def parse_items(html: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: dict[str, dict[str, str]] = {}

    for anchor in soup.select("a[href*='cgmExpcInfoP.do']"):
        href = unescape(anchor.get("href") or "")
        match = re.search(r"cgmExpcDatSeq=(\d+).*?ofiClsCd=(\d+)", href)
        if not match:
            continue

        doc_id, ofi_cls_cd = match.groups()
        items.setdefault(doc_id, {
            "doc_id": doc_id,
            "ofi_cls_cd": ofi_cls_cd,
            "source_url": urljoin(DETAIL_BASE, href),
        })

    return items


def fetch_valid_interpretations() -> tuple[int, dict[str, dict[str, str]]]:
    first = fetch_page(1)
    total_rows = parse_total(first)
    total_pages = math.ceil(total_rows / PAGE_SIZE) if total_rows else 1
    valid = parse_items(first)

    for page in range(2, total_pages + 1):
        time.sleep(0.1)
        valid.update(parse_items(fetch_page(page)))
        if page % 25 == 0 or page == total_pages:
            print(f"fetched pages: {page:,}/{total_pages:,}, unique docs: {len(valid):,}")

    return total_rows, valid


def run(dry_run: bool, sample_limit: int) -> None:
    db = get_mongo_db()
    col = db["interpretation"]

    total_rows, valid = fetch_valid_interpretations()
    valid_ids = set(valid)
    all_ids = set(str(v) for v in col.distinct("doc_id") if v)
    delete_ids = sorted(all_ids - valid_ids)
    missing_ids = sorted(valid_ids - all_ids)

    print(f"official list rows: {total_rows:,}")
    print(f"official unique docs: {len(valid_ids):,}")
    print(f"db distinct doc_id before: {len(all_ids):,}")
    print(f"delete doc_id: {len(delete_ids):,}")
    print(f"missing doc_id: {len(missing_ids):,}")
    print(f"interpretation chunks before: {col.count_documents({}):,}")
    print(f"chunks to delete: {col.count_documents({'doc_id': {'$in': delete_ids}}):,}")

    print("delete samples:")
    for doc in col.find(
        {"doc_id": {"$in": delete_ids}},
        {"_id": 0, "doc_id": 1, "title": 1, "sub_title": 1, "metadata.source_url": 1},
    ).limit(sample_limit):
        print(doc)

    print("missing samples:")
    for doc_id in missing_ids[:sample_limit]:
        print(valid[doc_id])

    if dry_run:
        print("DRY-RUN: no documents changed")
        return

    deleted = 0
    batch_size = 100
    for i in range(0, len(delete_ids), batch_size):
        batch = delete_ids[i:i + batch_size]
        try:
            result = col.delete_many({"doc_id": {"$in": batch}})
        except PyMongoError as exc:
            print(f"delete batch failed at {i:,}/{len(delete_ids):,}: {exc}")
            time.sleep(3)
            result = col.delete_many({"doc_id": {"$in": batch}})
        deleted += result.deleted_count
        if i == 0 or i + batch_size >= len(delete_ids) or (i // batch_size) % 10 == 0:
            print(f"deleted progress: {min(i + batch_size, len(delete_ids)):,}/{len(delete_ids):,} ids, {deleted:,} chunks")
        time.sleep(0.05)

    operations = []
    for doc_id, item in valid.items():
        operations.append(UpdateMany(
            {"doc_id": doc_id},
            {
                "$set": {
                    "metadata.source_url": item["source_url"],
                    "metadata.cpt_ofi_code": ORG_CODE,
                    "metadata.cpt_ofi_name": "고용노동부",
                    "metadata.ofi_cls_cd": item["ofi_cls_cd"],
                }
            },
        ))

    modified = 0
    for i in range(0, len(operations), 500):
        result = col.bulk_write(operations[i:i + 500], ordered=False)
        modified += result.modified_count
        print(f"source_url update progress: {min(i + 500, len(operations)):,}/{len(operations):,}")

    print(f"deleted chunks: {deleted:,}")
    print(f"metadata modified chunks: {modified:,}")
    print(f"interpretation chunks after: {col.count_documents({}):,}")
    print(f"interpretation distinct doc_id after: {len(col.distinct('doc_id')):,}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    run(args.dry_run, args.sample_limit)


if __name__ == "__main__":
    main()
