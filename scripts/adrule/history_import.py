"""행정규칙 과거 버전(연혁) 넣기 — `python3 manual.py import:adrule-history`.

해마다 새로 고시되는 행정규칙(예: "2025년 적용 최저임금 고시")은 law.go.kr 에서 같은 행정규칙ID(adrule_id)의 연혁으로
이어진다. 일일 크롤링은 현행·시행예정만 `adrule` 에 두므로, 지난 해의 고시를 근거로 인용하려면 과거 버전을 따로 넣어야 한다.

- 저장 위치는 `adrule_history`(별도 컬렉션)다. `adrule` 에 넣으면 매일 현행 판정(refresh_current_status_flow)이 같은
  adrule_id 의 옛 버전을 끄고, 시행예정 비교(upcoming_diff_sync)도 흔들린다.
- 본문은 일일 크롤링과 같은 경로(상세 페이지 + 첨부 원본 추출)로 읽는다. 추출 서버(DOCUMENT_EXTRACTION_URL)가 필요하므로
  운영에서는 크롤러 ECS 태스크로 돌린다(docs/manual-tasks.md).
- "…안 고시"(최저임금안 등 결정 전 공고)는 넣지 않는다. 인용할 근거는 확정 고시다.
- 각 버전에 적용 종료일(effective_to = 다음 버전 시행일 전날)과 다음 버전 doc_id(superseded_by)를 남긴다.

기본은 미리보기이고 `--apply` 로 저장한다.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
from datetime import date, timedelta

import requests

from scripts.core.config import LAWGO_OC
from scripts.core.run_report import call_with_retry
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type="adrule_history")

SEARCH_API = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/admRulInfoP.do?admRulSeq={doc_id}"
HISTORY_COLLECTION = "adrule_history"
PAGE_SIZE = 100
TIMEOUT_SECONDS = 15
# 결정 전 공고("2026년 적용 최저임금안 고시")는 확정 고시가 아니다.
DRAFT_TITLE = re.compile(r"안\s*고시\s*$")


def _iso(yyyymmdd: str | None) -> str | None:
    text = str(yyyymmdd or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def search_versions(query: str, page_size: int = PAGE_SIZE) -> list[dict]:
    """law.go.kr 행정규칙 연혁 검색(nw=2) 결과 전체. 현행(nw=1)도 함께 받아 버전 줄을 끊기지 않게 잇는다."""
    rows: list[dict] = []
    for nw in (1, 2):
        page = 1
        while True:
            params = {"OC": LAWGO_OC, "target": "admrul", "type": "JSON", "query": query,
                      "display": page_size, "page": page, "nw": nw}

            def fetch():
                resp = requests.get(SEARCH_API, params=params, timeout=TIMEOUT_SECONDS)
                resp.raise_for_status()
                return resp.json()

            body = call_with_retry(fetch).get("AdmRulSearch") or {}
            items = body.get("admrul") or []
            items = items if isinstance(items, list) else [items]
            rows.extend(items)
            total = int(body.get("totalCnt") or 0)
            if page * page_size >= total or not items:
                break
            page += 1
    return rows


def plan_history(rows: list[dict], adrule_id: str, existing_doc_ids: set[str],
                 since_year: int | None = None) -> list[dict]:
    """넣을 과거 버전 계획. 같은 adrule_id 의 확정 고시만 시행일 순으로 세워 적용 종료일·다음 버전을 정한다.

    existing_doc_ids(이미 adrule·adrule_history 에 있는 doc_id)는 다시 넣지 않지만, 앞 버전의 종료일 계산에는 쓴다.
    """
    by_doc: dict[str, dict] = {}
    for row in rows:
        if str(row.get("행정규칙ID") or "") != str(adrule_id):
            continue
        title = str(row.get("행정규칙명") or "").strip()
        effective = _iso(row.get("시행일자"))
        doc_id = str(row.get("행정규칙일련번호") or "").strip()
        if not doc_id or not effective or DRAFT_TITLE.search(title):
            continue
        by_doc[doc_id] = {
            "doc_id": doc_id,
            "title": title,
            "effective": effective,
            "promulgation_date": _iso(row.get("발령일자")),
            "promulgation_number": str(row.get("발령번호") or "").strip() or None,
        }
    versions = sorted(by_doc.values(), key=lambda v: (v["effective"], v["promulgation_date"] or "", v["doc_id"]))
    planned = []
    for i, version in enumerate(versions):
        nxt = versions[i + 1] if i + 1 < len(versions) else None
        if nxt is None:
            continue  # 가장 최근 버전은 현행·시행예정(adrule)이 맡는다
        if version["doc_id"] in existing_doc_ids:
            continue
        if since_year and int(version["effective"][:4]) < since_year:
            continue
        effective_to = (date.fromisoformat(nxt["effective"]) - timedelta(days=1)).isoformat()
        planned.append({**version, "effective_to": effective_to, "superseded_by": nxt["doc_id"],
                        "url": DETAIL_URL.format(doc_id=version["doc_id"])})
    return planned


def history_meta(version: dict) -> dict:
    """adrule_history 행 metadata 에 더할 값."""
    return {
        "history": True,
        "is_upcoming": False,
        "effective_to": version["effective_to"],
        "superseded_by": version["superseded_by"],
    }


ACTIVE = {"metadata.is_active": {"$ne": False}}
# 본문을 다 못 읽은 행 — 공개(내보내기는 활성 행만 읽는다)·완료 판정에서 뺀다.
INCOMPLETE = {"$or": [{"metadata.save_incomplete": True}, {"metadata.pdf_extraction_status": "partial"}]}


def existing_doc_ids(db, adrule_id: str) -> set[str]:
    """다시 넣지 않을 doc_id. adrule(현행·시행예정)은 일일 크롤링이 맡으니 상태와 관계없이 빼고,
    adrule_history 는 온전히 넣은(활성) 버전만 뺀다 — 추출 실패로 꺼 둔 버전은 다음 실행에서 다시 시도한다."""
    key = {"metadata.adrule_id": str(adrule_id)}
    ids = {str(d) for d in db["adrule"].distinct("doc_id", key)}
    ids.update(str(d) for d in db[HISTORY_COLLECTION].distinct("doc_id", {**key, **ACTIVE}))
    return ids


def settle_version(db, doc_id: str, status: str) -> str:
    """저장 뒤 그 버전을 확정한다. 실패했거나 본문 일부를 못 읽었으면 그 doc_id 의 활성 행을 꺼서
    공개되지 않게 하고(다음 실행에서 다시 시도) "incomplete" 를 돌려준다."""
    collection = db[HISTORY_COLLECTION]
    if status == "success" and collection.count_documents({"doc_id": doc_id, **ACTIVE, **INCOMPLETE}) == 0:
        return "success"
    collection.update_many({"doc_id": doc_id, **ACTIVE}, {"$set": {"metadata.is_active": False}})
    return "incomplete" if status == "success" else status


async def _import(db, planned: list[dict], dept_code: str | None) -> list[dict]:
    from scripts.adrule.logic.scraper import scrape_and_save

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for version in planned:
            result = await scrape_and_save(
                {"url": version["url"]}, tmp, f"history_{version['doc_id']}",
                save_to_db=True, save_jsonl=False, dept_code=dept_code,
                collection_name=HISTORY_COLLECTION, extra_meta=history_meta(version),
            )
            status = settle_version(db, version["doc_id"], (result or {}).get("status", "failed"))
            logger.info(f"{version['title']} ({version['doc_id']}): {status}")
            results.append({**version, "status": status, "error": (result or {}).get("error")})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adrule-id", required=True, help="행정규칙ID(예: 최저임금 고시 2002374)")
    parser.add_argument("--query", required=True,
                        help="law.go.kr 검색어 — 버전마다 이름이 달라도 모두 잡히는 말(예: '최저임금 고시')")
    parser.add_argument("--since-year", type=int, default=None, help="이 해 이후 시행 버전만(예: 2016)")
    parser.add_argument("--dept-code", default="1492000", help="소관부처 코드(기본 고용노동부)")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 계획만 출력)")
    args = parser.parse_args(argv)

    rows = search_versions(args.query)
    from scripts.core.database.mongo_client import get_mongo_db

    db = get_mongo_db()
    planned = plan_history(rows, args.adrule_id, existing_doc_ids(db, args.adrule_id), args.since_year)
    print(f"넣을 과거 버전 {len(planned)}건 (adrule_id={args.adrule_id}, 검색 결과 {len(rows)}건)")
    for v in planned:
        print(f"  {v['doc_id']}  {v['effective']}~{v['effective_to']}  제{v['promulgation_number']}호  {v['title']}")
    if not args.apply:
        print("미리보기만 했다. 저장하려면 --apply")
        return 0
    results = asyncio.run(_import(db, planned, args.dept_code))
    failed = [r for r in results if r["status"] != "success"]
    print(f"저장 {len(results) - len(failed)}건, 실패 {len(failed)}건")
    for r in failed:
        print(f"  실패 {r['doc_id']} {r['title']}: {r['error']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
