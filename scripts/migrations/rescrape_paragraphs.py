"""문단이 사라진 채 저장된 결정례·재결례·판례를 원문 페이지에서 다시 받아 문단을 살린다.

    python3 manual.py fix:rescrape-paragraphs --collection admin_decc --limit 20   # 미리 보기
    python3 manual.py fix:rescrape-paragraphs --collection admin_decc --apply

대상: metadata.source_type=web 이고, 800자 넘는 본문 행에 줄바꿈이 하나도 없는 문서.
예전 수집기가 원문의 `<p>` 문단을 공백으로 이어 붙였다(scripts/utils/html_text.py 참고).
저장은 일일 수집과 같은 scrape_and_save 경로(같은 섹션 키로 덮어씀)라 ID 는 그대로다.
문서당 벡터(title+whole) 임베딩은 공백을 접은 글로 해시를 만들어서 문단만 바뀌면 다시 임베딩되지 않는다.
"""
import argparse
import asyncio
import re
import sys
import time
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

COLLECTIONS = {
    "admin_decc": "parse_content",
    "constitutional_decc": "parse_content",
    "legislation_expc": "parse_content",
    "case": "parse_case_law_content",
}
FLAT_QUERY = {
    "metadata.is_active": True,
    "metadata.source_type": "web",
    "content": {"$not": re.compile("\n")},
    "$expr": {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 800]},
}


def target_docs(collection, limit: int) -> list[tuple[str, str]]:
    ids = sorted(collection.distinct("doc_id", FLAT_QUERY))
    if limit:
        ids = ids[:limit]
    docs = []
    for doc_id in ids:
        row = collection.find_one({"doc_id": doc_id, "metadata.is_active": True}, {"metadata.source_url": 1})
        url = ((row or {}).get("metadata") or {}).get("source_url")
        if url:
            docs.append((doc_id, url))
    return docs


def preview(module, parser_name: str, doc_id: str, url: str) -> int:
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    con = BeautifulSoup(html, "html.parser").select_one("#conScroll")
    chunks = getattr(module, parser_name)(con.decode_contents() if con else "", doc_id, url, "")
    return sum(chunk.get("text", "").count("\n\n") + 1 for chunk in chunks)


def run(args, db, out=print) -> dict:
    collection = db[args.collection]
    module = import_module(f"scripts.{args.collection}.logic.scraper")
    docs = target_docs(collection, args.limit)
    out(f"[{args.collection}] 대상 {len(docs)}건" + ("" if args.apply else " (미리 보기 — 저장하려면 --apply)"))
    totals = {"ok": 0, "failed": 0}
    for index, (doc_id, url) in enumerate(docs, 1):
        try:
            if args.apply:
                result = asyncio.run(module.scrape_and_save(url, "", doc_id, save_jsonl=False))
                ok = isinstance(result, dict) and result.get("status") == "success"
                out(f"  {'✓' if ok else '✗'} {doc_id}" + ("" if ok else f" {result.get('error', '') if isinstance(result, dict) else ''}"))
            else:
                paragraphs = preview(module, COLLECTIONS[args.collection], doc_id, url)
                ok = paragraphs > 1
                out(f"  · {doc_id}: 문단 1 → {paragraphs}")
        except Exception as exc:  # noqa: BLE001 - 한 건 실패는 기록하고 계속
            ok = False
            out(f"  ✗ {doc_id}: {type(exc).__name__}: {str(exc)[:120]}")
        totals["ok" if ok else "failed"] += 1
        if index < len(docs):
            time.sleep(args.delay)
    out(f"요약: {totals}")
    return totals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="문단이 사라진 웹 수집 문서를 다시 받아 문단을 살린다(기본 미리 보기)")
    parser.add_argument("--collection", choices=sorted(COLLECTIONS), required=True)
    parser.add_argument("--limit", type=int, default=0, help="최대 문서 수")
    parser.add_argument("--delay", type=float, default=0.5, help="요청 사이 대기(초)")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 미리 보기)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from scripts.core.database.mongo_client import get_mongo_db
    totals = run(args, get_mongo_db())
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
