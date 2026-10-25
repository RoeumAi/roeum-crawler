"""행정해석 CSV 안의 법제처 법령해석례 → legislation_expc 컬렉션 행.

행정해석 CSV 에는 부제가 '법제처 26-0284, 2026. 7. 21.' 인 법제처 해석례가 섞여 있다. 이 문서는
interpretation 이 아니라 법제처 해석례 크롤러가 쓰는 legislation_expc 에 그 크롤러 저장 함수
(legislation_expc/logic/scraper.save_to_mongodb)로 넣는다. 이미 크롤링된 번호는 건너뛴다.
CSV 에는 요약 제목이 없어 문서번호(법제처 26-0284)를 제목으로 쓴다.
"""
import re

from scripts.csv_import.common import parse_date
from scripts.csv_import.interpretation import split_sections, split_sub_title

COLLECTION = "legislation_expc"
DOC_ID_PREFIX = "csv_legis_"
_NUMBER = re.compile(r"법제처\s*[-–]?\s*(\d{2}-\d{4})")


def number_of(sub_title: str):
    m = _NUMBER.search(sub_title or "")
    return m.group(1) if m else None


def load_existing(col) -> tuple:
    numbers, imported = set(), set()
    for doc in col.find({}, {"doc_id": 1, "sub_title": 1}).batch_size(2000):
        doc_id = str(doc.get("doc_id") or "")
        if doc_id.startswith(DOC_ID_PREFIX):
            imported.add(doc_id)
        elif number_of(doc.get("sub_title")):
            numbers.add(number_of(doc.get("sub_title")))
    return numbers, imported


SKIP_NOT_LEGISLATION = "법제처 해석례가 아님(행정해석으로 넣음)"
SKIP_EXISTING = "이미 수집된 법제처 해석례(번호 일치)"
SKIP_CSV_DUP = "CSV 안 중복(번호 일치)"
SKIP_EMPTY = "본문 없음"


class Planner:
    def __init__(self, existing_numbers: set, imported_doc_ids: set):
        self.existing, self.imported, self.seen = existing_numbers, imported_doc_ids, set()

    def decide(self, row: dict) -> tuple:
        if not row["sub_title"].strip().startswith("법제처"):
            return ("skip", SKIP_NOT_LEGISLATION)
        number = number_of(row["sub_title"])
        if not (row.get("wm_content2") or "").strip():
            result = ("skip", SKIP_EMPTY)
        elif number and number in self.seen:
            result = ("skip", SKIP_CSV_DUP)
        elif doc_id_for(row) in self.imported:
            result = ("update", None)
        elif number and number in self.existing:
            result = ("skip", SKIP_EXISTING)
        else:
            result = ("insert", None)
        if number:
            self.seen.add(number)
        return result


def doc_id_for(row: dict) -> str:
    return f"{DOC_ID_PREFIX}{row['wm_id'].strip()}"


def build_documents(row: dict, now_iso: str, source_file: str, import_batch: str) -> dict:
    sub_title = re.sub(r"\s+", " ", row["sub_title"]).strip()
    number = number_of(sub_title)
    # 최근 번호는 '법제처 26-0284', 옛 번호('법제처 기획 02102-23, 1988. 8. 26.')는 날짜를 뺀 앞부분
    title = f"법제처 {number}" if number else split_sub_title(sub_title)[0]
    meta = {
        "chapter": title,
        "dept_name": "법제처",
        "source_type": "csv",
        "source_file": source_file,
        "import_batch": import_batch,
        "csv_wm_id": row["wm_id"].strip(),
        "doc_number": title,
        "effective": parse_date(sub_title),
    }
    sections = split_sections(row["wm_content2"])
    return {
        "doc_id": doc_id_for(row),
        "title": title,
        "sub_title": sub_title,
        "chunks": [{"title": s["article_title"], "text": s["content"], "metadata": dict(meta)}
                   for s in sections],
    }


def write(col, doc: dict, created_at: str) -> int:
    from scripts.legislation_expc.logic.scraper import save_to_mongodb
    if not save_to_mongodb(doc["doc_id"], doc["title"], doc["sub_title"], "", doc["chunks"]):
        raise RuntimeError(f"법제처 해석례 저장 실패: {doc['doc_id']}")
    return len(doc["chunks"])


def preview(doc: dict) -> dict:
    return {"doc_id": doc["doc_id"], "title": doc["title"], "sub_title": doc["sub_title"],
            "sections": [{"name": c["title"], "chars": len(c["text"])} for c in doc["chunks"]]}
