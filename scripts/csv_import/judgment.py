"""주요판정사례 CSV → judgment 컬렉션 행.

크롤러(judgment/logic/scraper.save_judgment_to_mongodb)처럼 문서 하나를 한 행으로 저장한다.
CSV 에는 요약 제목이 없어 사건번호(예: 서울2026부해1628)를 제목으로 쓴다.
"""
import re
from typing import Optional

from scripts.core.identifiers import document_chunk_id
from scripts.core.database.source_versioning import enrich_source_document
from scripts.csv_import.common import convert_tables, normalize_key, parse_date, tidy

COLLECTION = "judgment"
DOC_ID_PREFIX = "csv_judgment_"
REGIONS = "중앙|서울|경기|인천|부산|경남|울산|대구|경북|광주|전남|전북|대전|충남|충북|강원|제주|세종"
_NUMBER = re.compile(rf"({REGIONS})?\s*(\d{{2,4}})\s*([가-힣]{{1,4}})\s*(\d{{1,6}})")


def case_number(sub_title: str, content: str = "") -> str:
    """'지방노동위원회(서울) 2026. 7. 14. 판정 2026부해1628' → '서울2026부해1628'."""
    text = re.sub(r"\s+", " ", sub_title or "").strip()
    region = ""
    m = re.search(r"\(([가-힣]+)\)", text)
    if m and re.fullmatch(REGIONS, m.group(1)):
        region = m.group(1)
    elif text.startswith("중앙노동위원회"):
        region = "중앙"
    tail = re.sub(r".*(판정|결정|재심)\s*", "", text) if re.search(r"판정|결정|재심", text) else text
    found = _NUMBER.search(tail)
    if not found:
        return text
    number = "".join(found.groups()[1:])
    if found.group(1):
        return found.group(1) + number
    if not region and content:
        m = _NUMBER.search(content)
        if m and m.group(1) and "".join(m.groups()[1:]) == number:
            region = m.group(1)
    return region + number


def keys_of(number: str) -> set:
    return {normalize_key(number)} if number else set()


def load_existing(col) -> tuple:
    """크롤링 문서는 사건번호 칸이 없어 본문 앞쪽(2,500자 안) 첫 사건번호를 그 문서 번호로 본다."""
    keys, imported = set(), set()
    for doc in col.find({}, {"doc_id": 1, "content": 1, "title": 1}).batch_size(500):
        doc_id = str(doc.get("doc_id") or "")
        if doc_id.startswith(DOC_ID_PREFIX):
            imported.add(doc_id)
            continue
        m = _NUMBER.search((doc.get("content") or "")[:2500])
        if m and m.group(1):
            keys.add(normalize_key("".join(m.groups())))
    return keys, imported


SKIP_EXISTING = "이미 수집된 판정사례(사건번호 일치)"
SKIP_CSV_DUP = "CSV 안 중복(사건번호 일치)"
SKIP_EMPTY = "본문 없음"


class Planner:
    def __init__(self, existing_keys: set, imported_doc_ids: set):
        self.existing, self.imported, self.seen = existing_keys, imported_doc_ids, set()

    def decide(self, row: dict) -> tuple:
        keys = keys_of(case_number(row["sub_title"], row.get("wm_content2") or ""))
        if not (row.get("wm_content2") or "").strip():
            result = ("skip", SKIP_EMPTY)
        elif keys & self.seen:
            result = ("skip", SKIP_CSV_DUP)
        elif doc_id_for(row) in self.imported:
            result = ("update", None)
        elif keys & self.existing:
            result = ("skip", SKIP_EXISTING)
        else:
            result = ("insert", None)
        self.seen |= keys
        return result


def doc_id_for(row: dict) -> str:
    return f"{DOC_ID_PREFIX}{row['wm_id'].strip()}"


def build_documents(row: dict, now_iso: str, source_file: str, import_batch: str) -> dict:
    sub_title = re.sub(r"\s+", " ", row["sub_title"]).strip()
    number = case_number(sub_title, row["wm_content2"])
    doc_id = doc_id_for(row)
    doc = {
        "doc_id": doc_id,
        "chunk_id": document_chunk_id(COLLECTION, doc_id),
        "article_number": "1",
        "doc_type": "주요판정사례",
        "title": number,
        "sub_title": sub_title,
        "content": tidy(convert_tables(row["wm_content2"])),
        "metadata": {
            "source_type": "csv",
            "source_file": source_file,
            "import_batch": import_batch,
            "csv_wm_id": row["wm_id"].strip(),
            "case_number": number,
            "committee": re.split(r"\s\d{4}\s*\.", sub_title)[0].strip(),
            "effective": parse_date(sub_title),
            "updated_at": now_iso,
            "is_active": True,
        },
    }
    return enrich_source_document(doc, COLLECTION)


def write(col, doc: dict, created_at: str) -> int:
    set_fields = {k: v for k, v in doc.items() if k != "metadata"}
    set_fields.update({f"metadata.{k}": v for k, v in doc["metadata"].items()})
    col.update_one({"doc_id": doc["doc_id"]},
                   {"$set": set_fields, "$setOnInsert": {"metadata.created_at": created_at}},
                   upsert=True)
    return 1


def preview(doc: dict) -> dict:
    return {"doc_id": doc["doc_id"], "title": doc["title"], "sub_title": doc["sub_title"],
            "effective": doc["metadata"]["effective"], "chars": len(doc["content"])}
