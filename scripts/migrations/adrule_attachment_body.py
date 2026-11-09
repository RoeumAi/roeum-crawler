"""본문이 첨부에 있는 행정규칙을 첨부 원문으로 다시 넣는다 — `python3 manual.py fix:adrule-attachment-body`.

law.go.kr 화면이 표를 그림으로 싣거나 "첨부를 보라"는 문구만 보여 주는 행정규칙은 예전 수집기가 화면 글만 저장했다
(금액·기준표가 빠진 채 성공으로 기록). 수집기는 이제 이런 문서를 첨부 원문으로 읽지만(scripts/adrule/logic/attachment_body),
일일 수집은 시행일이 같으면 다시 저장하지 않으므로 이미 저장된 문서는 이 도구로 한 번 다시 넣는다.

- 대상: 원본 파일에서 읽은(metadata.source_file_keys) 활성 행이 없는 문서 중 상세 API 가 본문이 첨부에 있다고 판정한 문서.
  현행(adrule)은 활성 문서만, 과거 버전(adrule_history)은 꺼 둔 문서까지 본다(일부만 읽어 꺼 둔 버전을 다시 시도).
- 저장은 일일 수집과 같은 scrape_and_save 경로이고, 성공하면 이번에 쓰지 않은 옛 행을 끈다(SaveTarget.replace_rows).
  과거 버전은 원래 행의 적용 종료일·다음 버전 값을 그대로 다시 싣고, import:adrule-history 와 같이 일부만 읽은 버전은
  꺼 둔다(settle_version). 저장 전에 실패한 문서는 옛 행을 그대로 둔다.
- 첨부 파싱에 추출 서버(DOCUMENT_EXTRACTION_URL)가 필요하므로 운영에서는 크롤러 ECS 태스크로 돌린다(docs/manual-tasks.md).

    python3 manual.py fix:adrule-attachment-body                      # 미리 보기(대상과 읽을 첨부)
    python3 manual.py fix:adrule-attachment-body --adrule-id 2002374  # 행정규칙ID 하나만
    python3 manual.py fix:adrule-attachment-body --apply              # 다시 넣기
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from scripts.adrule.logic.attachment_body import POINTER, attachment_need, notice_files
from scripts.core.lawgokr_stable_id import fetch_record

COLLECTIONS = ("adrule", "adrule_history")
HISTORY_KEYS = ("history", "is_upcoming", "effective_to", "superseded_by")
API_WORKERS = 6


ACTIVE = {"metadata.is_active": {"$ne": False}}
FILE_SOURCED = {**ACTIVE, "metadata.source_file_keys": {"$exists": True}}


def candidate_doc_ids(collection, name: str, adrule_id: str | None = None) -> list[str]:
    """원본 파일에서 읽은 활성 행이 없는 문서.

    현행(adrule)은 활성 문서만 본다(꺼진 행은 지난 버전이다). 과거 버전(adrule_history)은 꺼진 문서도 본다 —
    다시 넣다 일부만 읽어 꺼 둔(settle_version) 버전을 다음 실행에서 다시 시도하기 위해서다."""
    scope = {"metadata.adrule_id": str(adrule_id)} if adrule_id else {}
    seen = collection.distinct("doc_id", {**scope, **(ACTIVE if name == "adrule" else {})})
    done = {str(d) for d in collection.distinct("doc_id", {**scope, **FILE_SOURCED})}
    return sorted({str(d) for d in seen} - done)


def representative_row(collection, doc_id: str) -> dict:
    """제목·원본 주소·표시 값을 읽을 행. 활성 행이 있으면 그 행, 없으면(꺼 둔 과거 버전) 아무 행."""
    projection = {"title": 1, "metadata": 1}
    return (collection.find_one({"doc_id": doc_id, **ACTIVE}, projection)
            or collection.find_one({"doc_id": doc_id}, projection) or {})


@dataclass
class Plan:
    targets: list[dict] = field(default_factory=list)       # 다시 넣을 문서
    unreadable: list[tuple[str, str]] = field(default_factory=list)  # 상세 API 를 못 읽어 판정하지 못한 (컬렉션, doc_id)


def plan(db, args, fetch=fetch_record) -> Plan:
    """다시 넣을 문서 [{collection, doc_id, title, url, meta, files}]와 판정하지 못한 문서. 상세 API 는 문서마다 한 번,
    동시에 몇 개씩 부른다. 수집기와 같은 규칙으로 고른다 — 첨부 안내 문구뿐이거나, 그림이 있고 원문 첨부가 있는 문서
    (그림만 있고 원문 첨부가 없으면 수집기도 화면 글을 저장하므로 다시 넣지 않는다)."""
    candidates = [(name, doc_id) for name in COLLECTIONS
                  for doc_id in candidate_doc_ids(db[name], name, args.adrule_id)]
    with ThreadPoolExecutor(max_workers=API_WORKERS) as pool:
        records = list(pool.map(lambda c: fetch("admrul", c[1]), candidates))
    result = Plan()
    for (name, doc_id), record in zip(candidates, records):
        if record is None:
            result.unreadable.append((name, doc_id))
            continue
        need, files = attachment_need(record), notice_files(record)
        if not (need == POINTER or (need and files)):
            continue
        row = representative_row(db[name], doc_id)
        meta = row.get("metadata") or {}
        result.targets.append({"collection": name, "doc_id": doc_id, "title": row.get("title") or "",
                               "url": meta.get("source_url") or "", "meta": meta, "files": files})
        if args.limit and len(result.targets) >= args.limit:
            break
    return result


HISTORY = "adrule_history"


def quarantine_partial_write(collection, doc_id: str) -> int:
    """다시 넣기가 실패했는데 일부 행은 이미 썼을 때(별표·조문 저장 실패 → save_incomplete) 이번에 쓴 행만 끈다.
    끄면 원본 파일 활성 행이 없어져 다음 실행의 대상에 다시 들고, 저장 전에 실패한 경우처럼 옛 행은 그대로 남는다."""
    result = collection.update_many(
        {"doc_id": doc_id, **ACTIVE,
         "$or": [{"metadata.save_incomplete": True}, {"metadata.source_file_keys": {"$exists": True}}]},
        {"$set": {"metadata.is_active": False}})
    return result.modified_count


def save_target(target: dict):
    from scripts.adrule.logic.scraper import SaveTarget

    extra = None
    if target["collection"] == "adrule_history":
        extra = {key: target["meta"][key] for key in HISTORY_KEYS if key in target["meta"]}
    return SaveTarget(target["collection"], extra, replace_rows=True)


async def _rescrape(db, targets: list[dict], out) -> list[dict]:
    from scripts.adrule.history_import import settle_version
    from scripts.adrule.logic.scraper import scrape_and_save

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for target in targets:
            meta = target["meta"]
            url = {"url": target["url"], "dept_name": meta.get("dept_name", ""),
                   "is_upcoming": bool(meta.get("is_upcoming", False))}
            result = await scrape_and_save(url, tmp, f"attachment_{target['doc_id']}", save_to_db=True,
                                           save_jsonl=False, dept_code=meta.get("dept_code"),
                                           target=save_target(target))
            status = (result or {}).get("status", "failed")
            if target["collection"] == "adrule_history":
                if status == "success":
                    # 과거 버전 넣기와 같은 확정: 일부만 읽었으면 그 버전 행을 꺼 공개하지 않는다. 꺼 둔 버전은 원본 파일 활성
                    # 행이 없으므로 다음 실행의 대상에 다시 든다(candidate_doc_ids).
                    status = settle_version(db, target["doc_id"], status)
                else:
                    quarantine_partial_write(db[HISTORY], target["doc_id"])
            out(f"  {target['collection']} {target['doc_id']} {target['title']}: {status}")
            results.append({**target, "status": status, "error": (result or {}).get("error")})
    return results


def run(args, db, fetch=fetch_record, rescrape=_rescrape, out=print) -> int:
    planned = plan(db, args, fetch)
    targets = planned.targets
    out(f"다시 넣을 문서 {len(targets)}건")
    for t in targets:
        files = ", ".join(name for _, name in t["files"]) or "원문 첨부 없음(첨부 안내뿐이라 실패로 남는다)"
        out(f"  {t['collection']} {t['doc_id']} {t['title']} ← {files}")
    if planned.unreadable:
        # API 장애로 판정 못 한 문서를 대상 0건·성공으로 덮지 않는다.
        out(f"상세 API 를 읽지 못해 판정하지 못한 문서 {len(planned.unreadable)}건 — 다시 실행해야 한다")
        for name, doc_id in planned.unreadable:
            out(f"  판정 실패 {name} {doc_id}")
    if not args.apply:
        out("미리 보기만 했다. 다시 넣으려면 --apply")
        return 1 if planned.unreadable else 0
    results = asyncio.run(rescrape(db, targets, out))
    failed = [r for r in results if r["status"] != "success"]
    out(f"성공 {len(results) - len(failed)}건, 실패 {len(failed)}건, 판정 실패 {len(planned.unreadable)}건")
    for r in failed:
        out(f"  실패 {r['collection']} {r['doc_id']} {r['title']}: {r['error']}")
    return 1 if failed or planned.unreadable else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="다시 넣는다(없으면 미리 보기)")
    parser.add_argument("--adrule-id", default=None, help="이 행정규칙ID 의 문서만(예: 최저임금 고시 2002374)")
    parser.add_argument("--limit", type=int, default=0, help="처리할 문서 수 상한(0=전부)")
    args = parser.parse_args(argv)
    from scripts.core.database.mongo_client import get_mongo_db

    return run(args, get_mongo_db())


if __name__ == "__main__":
    sys.exit(main())
