"""PDF 로 넣은 문서(판례·행정해석·결정례)를 지금 파서로 다시 파싱한다.

    python3 manual.py pdf-reparse                         # 미리 보기: 다시 읽었을 때 무엇이 바뀌는지
    python3 manual.py pdf-reparse --type 행정해석 --limit 20
    python3 manual.py pdf-reparse --apply                 # 저장

대상: metadata.source_type=pdf 이고 지난 파싱이 완전하지 않은(pdf_retry_needed) 문서. `--include-complete`면 전부.
원본 파일은 pdf_to_mongo.PDF_DIRS(--pdf-dir 또는 환경변수 PDF_DB_DIR 아래)에서 metadata.source_file 로 찾는다.

안전 규칙(scripts/pdf_ingest/reparse_existing.replacement_document)
- 문서 ID·청크 ID·청크 수는 그대로 두고 본문만 바꾼다.
- 새로 읽은 글이 없거나 기존보다 35% 넘게 줄면 기존 문서를 그대로 둔다.
- 한 문서의 모든 청크는 트랜잭션 하나로 바꾼다.

임베딩: 기존 벡터는 지우지 않는다(교체 전까지 검색에서 빠지지 않게).
- 판례·행정해석(문서당 벡터 하나, title+whole): 대표 행의 embedding_hash 를 지워 맥미니 03:30
  임베딩 갱신(mongo_embed_refresh)이 새 본문으로 다시 임베딩하게 한다.
- 결정례(행마다 벡터, title+sub_title+content): content_hash 가 바뀌어 다음 갱신 때 다시 임베딩된다.
  단, 결정례는 지금 맥미니 갱신 대상 컬렉션이 아니라서 갱신 작업에 decision 을 넣어 돌려야 한다.
"""
import argparse
import sys
import unicodedata
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pdf_ingest.pdf_to_mongo import PDF_DIRS, PDF_TYPES, build_pdf_dirs, resolve_pdf_base  # noqa: E402
from scripts.pdf_ingest.reparse_existing import replacement_document  # noqa: E402
from scripts.utils.document_extraction import call_extract_text  # noqa: E402

TYPE_COLLECTION = {name: collection for name, (_, collection) in PDF_TYPES.items()}
WHOLE_DOCUMENT_RECIPE = "title+whole@4096"


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value or "")


def file_index(directory: Path) -> dict:
    """맥 파일명은 한글이 분해형(NFD)일 수 있어 정규화한 이름으로 찾는다."""
    return {nfc(path.name): path for path in directory.glob("*.pdf")} if directory.exists() else {}


def chunk_order(row: dict):
    meta = row.get("metadata") or {}
    for value in (row.get("chunk_seq"), row.get("article_number"), meta.get("chunk_index")):
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return (0, int(value), str(row.get("_id")))
    return (1, 0, str(row.get("_id")))


def target_doc_ids(collection, include_complete: bool, doc_ids: list, limit: int) -> list:
    query = {"metadata.source_type": "pdf", "metadata.is_active": True}
    if doc_ids:
        query["doc_id"] = {"$in": doc_ids}
    elif not include_complete:
        query["metadata.pdf_retry_needed"] = True
    ids = sorted(collection.distinct("doc_id", query))
    return ids[:limit] if limit else ids


def table_count(rows: list) -> int:
    blocks = [b for row in rows for b in ((row.get("metadata") or {}).get("document_blocks") or [])]
    return sum(1 for block in blocks if block.get("kind") == "table")


def keep_vectors(before: list, after: list) -> list:
    """replacement_document 가 비운 벡터를 되살린다(교체 전까지 기존 벡터로 검색되게).

    문서당 벡터(title+whole)는 embedding_hash 를 지워 갱신 작업이 다시 임베딩하게 하고,
    행마다 벡터는 해시를 남겨 content_hash 와 어긋난 것으로 다시 임베딩되게 한다.
    """
    rows = []
    for old, new in zip(before, after):
        new = deepcopy(new)
        if "embedding" in old:
            new["embedding"] = old["embedding"]
            recipe = (old.get("metadata") or {}).get("embed_recipe")
            if recipe != WHOLE_DOCUMENT_RECIPE and old.get("embedding_hash"):
                new["embedding_hash"] = old["embedding_hash"]
        rows.append(new)
    return rows


def write_document(client, collection, rows: list) -> None:
    def replace_all(session):
        for row in rows:
            collection.replace_one({"_id": row["_id"]}, row, session=session)
    with client.start_session() as session:
        session.with_transaction(replace_all)


def reparse(args, db, client, extract=call_extract_text, out=print) -> Counter:
    totals = Counter()
    provenance = {"version": f"reparse-{datetime.now():%Y%m%d}", "tool": "scripts/pdf_ingest/reparse.py"}
    types = list(TYPE_COLLECTION) if args.type == "all" else [args.type]
    for type_name in types:
        collection_name = TYPE_COLLECTION[type_name]
        collection = db[collection_name]
        files = file_index(PDF_DIRS[type_name][0])
        ids = target_doc_ids(collection, args.include_complete, args.doc_id, args.limit)
        out(f"\n[{type_name}] {collection_name}: 대상 {len(ids)}건")
        for doc_id in ids:
            before = sorted(collection.find({"doc_id": doc_id, "metadata.is_active": True}), key=chunk_order)
            source_file = nfc((before[0].get("metadata") or {}).get("source_file", "")) if before else ""
            path = files.get(source_file)
            if not path:
                out(f"  - {doc_id}: 원본 파일 없음 ({source_file or '?'})")
                totals["no_file"] += 1
                continue
            try:
                extracted = extract(path.read_bytes(), path.name)
                result = replacement_document(before, extracted, collection_name, provenance)
            except Exception as exc:  # noqa: BLE001 - 한 문서 실패는 기록하고 계속
                out(f"  ✗ {doc_id}: {type(exc).__name__}: {str(exc)[:120]}")
                totals["error"] += 1
                continue
            if result["action"] != "replace":
                out(f"  = {doc_id}: 유지 ({result['reason']}"
                    + (f", 남는 글자 {result['retention']:.0%})" if "retention" in result else ")"))
                totals["retained"] += 1
                continue
            after = keep_vectors(before, result["after"])
            old_status = (before[0].get("metadata") or {}).get("pdf_extraction_status")
            new_status = after[0]["metadata"].get("pdf_extraction_status")
            out(f"  {'✓' if args.apply else '·'} {doc_id}: {old_status} → {new_status}, 글자 {result['retention']:.0%}, "
                f"표 {table_count(before)} → {table_count(after)}, 청크 {len(after)}")
            totals[f"to_{new_status}"] += 1
            if args.apply:
                try:
                    write_document(client, collection, after)
                    totals["written"] += 1
                except Exception as exc:  # noqa: BLE001
                    out(f"    ✗ 저장 실패: {type(exc).__name__}: {str(exc)[:120]}")
                    totals["write_error"] += 1
    out(f"\n요약: {dict(totals)}" + ("" if args.apply else "  (미리 보기 — 저장하려면 --apply)"))
    return totals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="PDF 로 넣은 문서를 지금 파서로 다시 파싱(기본 미리 보기)")
    parser.add_argument("--type", choices=[*TYPE_COLLECTION, "all"], default="all")
    parser.add_argument("--doc-id", action="append", default=[], help="이 문서만(여러 번 가능)")
    parser.add_argument("--limit", type=int, default=0, help="유형별 최대 문서 수")
    parser.add_argument("--include-complete", action="store_true", help="지난 파싱이 complete 인 문서도 포함")
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 미리 보기)")
    parser.add_argument("--pdf-dir", help="PDF 상위 디렉터리(하위: 판례·행정해석·결정례). 없으면 환경변수 PDF_DB_DIR")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    PDF_DIRS.update(build_pdf_dirs(resolve_pdf_base(args.pdf_dir)))
    from scripts.core.database.mongo_client import MongoClientSingleton
    db = MongoClientSingleton.get_db()
    totals = reparse(args, db, db.client)
    return 1 if totals["error"] or totals["write_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
