"""행정해석 CSV → interpretation 컬렉션 행.

크롤러가 law.go.kr 에서 수집한 문서(interpretation/logic/scraper.save_to_mongodb)와 같은 모양:
소제목(질의요지·회답 …)마다 한 행, chunk_id·content_hash 는 같은 규칙.
CSV 에는 요약 제목이 없어 직접 넣은 PDF 문서처럼 문서번호를 제목으로 쓴다.
"""
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from scripts.core.identifiers import article_chunk_id
from scripts.core.database.source_versioning import enrich_source_document
from scripts.csv_import.common import _DATE, convert_tables, normalize_key, parse_date, tidy

COLLECTION = "interpretation"
DOC_ID_PREFIX = "csv_interp_"

# 줄 하나가 소제목이면 그 이름을 돌려준다. 괄호(【】·[])로 감싼 줄은 이름과 상관없이 소제목,
# 괄호 없는 줄은 아래 이름일 때만 소제목으로 본다(본문 첫 줄이 우연히 짧은 경우를 막는다).
_BARE_HEADERS = re.compile(
    r"^(질의(요지|내용|사항|배경)?|회시|회답|회신|답변(내용)?|이유|요지|해석변경|"
    r"갑설|을설|병설|정설|참고|사실관계|사례|예시|개요|사건개요|상황개요|주문)\d*$"
)
_BRACKET_LINE = re.compile(r"^[【\[]\s*([^】\]]{1,20}?)\s*[】\]]$")
# '【회시】1. 산업안전보건법 …' 처럼 소제목 뒤에 같은 줄로 본문이 이어지는 꼴(7천여 건)
_BRACKET_INLINE = re.compile(r"^\s*【\s*([^】]{1,20}?)\s*】\s*(\S.*)$")
_NUMBERING = re.compile(r"^(\d{1,2}|[가-하])[.)]\s*")


def split_sub_title(sub_title: str) -> tuple:
    """'근로기준정책과-42, 2026. 4. 14' → ('근로기준정책과-42', '2026-04-14')."""
    text = (sub_title or "").strip()
    m = None
    for m in _DATE.finditer(text):
        pass
    if not m:
        return text.strip(" ,"), None
    number = (text[: m.start()] + text[m.end():]).strip(" ,.")
    return number, parse_date(m.group(0))


def _header_name(line: str) -> Optional[str]:
    stripped = line.strip()
    bracket = _BRACKET_LINE.match(stripped)
    if bracket:
        return re.sub(r"\s", "", bracket.group(1)) or None
    bare = re.sub(r"\s", "", _NUMBERING.sub("", stripped))
    return bare if _BARE_HEADERS.match(bare) else None


def _section_title(name: str) -> str:
    """기존 수집 문서의 소제목 이름(질의요지·회답)에 맞춘다. 번호가 있으면 뒤에 붙인다."""
    m = re.match(r"^(.*?)(\d*)$", name)
    base, num = m.group(1), m.group(2)
    if base.startswith("질의") and base != "질의배경":
        base = "질의요지"
    elif base in ("회시", "회답", "회신", "답변", "답변내용"):
        base = "회답"
    return f"{base} {num}" if num else base


def split_sections(content: str) -> list:
    """본문을 소제목 단위로 나눈다. 소제목이 없으면 '본문' 한 덩어리. 글자 표는 마크다운 표로."""
    sections, title, buf = [], None, []

    def flush():
        text = tidy(convert_tables("\n".join(buf)))
        if text:
            sections.append({"article_title": title or "본문", "content": text})

    for line in (content or "").replace("\r\n", "\n").split("\n"):
        inline = _BRACKET_INLINE.match(line)
        name = re.sub(r"\s", "", inline.group(1)) if inline else _header_name(line)
        if name:
            flush()
            title, buf = _section_title(name), ([inline.group(2)] if inline else [])
        else:
            buf.append(line)
    flush()
    return sections


@dataclass
class ExistingIndex:
    """이미 있는 문서의 문서번호 → 날짜 집합(날짜를 모르면 None)."""

    dates_by_number: dict = field(default_factory=dict)

    def add(self, number: str, date: Optional[str]) -> None:
        key = normalize_key(number)
        if key and re.search(r"\d", key):
            self.dates_by_number.setdefault(key, set()).add(date)

    def contains(self, number: str, date: Optional[str]) -> bool:
        dates = self.dates_by_number.get(normalize_key(number))
        if not dates:
            return False
        return date is None or None in dates or date in dates


def numbers_from_existing(doc: dict) -> Iterable[str]:
    """기존 문서에서 문서번호 후보를 뽑는다: sub_title 조각, metadata.doc_number, PDF 제목."""
    meta = doc.get("metadata") or {}
    sub = doc.get("sub_title") or ""
    for part in sub.split(","):
        part = part.strip()
        if part.startswith("고용노동부"):
            part = part[len("고용노동부"):].strip()
        if part and not parse_date(part):
            yield part
    if meta.get("doc_number"):
        yield meta["doc_number"]
    if meta.get("source_type") == "pdf" and doc.get("title"):
        yield doc["title"]


def load_existing(col) -> tuple:
    """(크롤링·PDF 문서 번호 색인, 이전에 넣은 CSV doc_id 집합)."""
    index, imported = ExistingIndex(), set()
    projection = {"doc_id": 1, "sub_title": 1, "title": 1, "metadata.doc_number": 1,
                  "metadata.source_type": 1, "metadata.effective": 1}
    for doc in col.find({}, projection).batch_size(2000):
        doc_id = str(doc.get("doc_id") or "")
        if doc_id.startswith(DOC_ID_PREFIX):
            imported.add(doc_id)
            continue
        date = (doc.get("metadata") or {}).get("effective") or None
        for number in numbers_from_existing(doc):
            index.add(number, date)
    return index, imported


def agency_for(number: str) -> str:
    """CSV 는 대부분 고용노동부 회시지만 국세청 과세기준자문(…법규소득…)이 섞여 있다."""
    return "국세청" if "법규" in (number or "") else "고용노동부"


SKIP_EXISTING = "이미 수집된 문서(문서번호·날짜 일치)"
SKIP_LEGISLATION = "법제처 해석례(legislation_expc 수집 대상)"
SKIP_CSV_DUP = "CSV 안 중복(문서번호·날짜 일치)"
SKIP_EMPTY = "본문 없음"


class Planner:
    """행마다 ('insert'|'update'|'skip', 사유). 이전에 넣은 CSV 문서는 'update'(같은 doc_id 로 덮어씀)."""

    def __init__(self, existing: ExistingIndex, imported_doc_ids: set):
        self.existing, self.imported, self.seen = existing, imported_doc_ids, set()

    def decide(self, row: dict) -> tuple:
        number, date = split_sub_title(row["sub_title"])
        key = (normalize_key(number), date)
        if not (row.get("wm_content2") or "").strip():
            result = ("skip", SKIP_EMPTY)
        elif normalize_key(number).startswith("법제처"):
            result = ("skip", SKIP_LEGISLATION)
        elif key in self.seen:
            result = ("skip", SKIP_CSV_DUP)
        elif doc_id_for(row) in self.imported:
            result = ("update", None)
        elif self.existing.contains(number, date):
            result = ("skip", SKIP_EXISTING)
        else:
            result = ("insert", None)
        self.seen.add(key)
        return result


def doc_id_for(row: dict) -> str:
    return f"{DOC_ID_PREFIX}{row['wm_id'].strip()}"


def build_documents(row: dict, now_iso: str, source_file: str, import_batch: str) -> list:
    """CSV 한 행 → interpretation 컬렉션 행 목록(소제목마다 한 행)."""
    number, date = split_sub_title(row["sub_title"])
    sections = split_sections(row["wm_content2"])
    doc_id = doc_id_for(row)
    raw_date = date.replace("-0", "-").replace("-", ".") if date else ""
    agency = agency_for(number)
    sub_title = ", ".join(p for p in [agency, number, raw_date] if p)
    docs = []
    for idx, section in enumerate(sections, 1):
        article_number = str(idx)
        doc = {
            "chunk_id": article_chunk_id(COLLECTION, doc_id, article_number),
            "doc_id": doc_id,
            "article_number": article_number,
            "article_title": section["article_title"],
            "title": number,
            "sub_title": sub_title,
            "content": section["content"],
            "doc_type": "법령해석",
            "metadata": {
                "source_type": "csv",
                "source_file": source_file,
                "import_batch": import_batch,
                "csv_wm_id": row["wm_id"].strip(),
                "doc_number": number,
                "effective": date,
                "updated_at": now_iso,
                "is_active": True,
                "dept_name": agency,
                "cpt_ofi_name": agency,
                "article_index": idx,
                "total_articles": len(sections),
            },
        }
        if agency == "고용노동부":
            doc["metadata"]["cpt_ofi_code"] = "1492000"
        docs.append(enrich_source_document(doc, COLLECTION))
    return docs


def write(col, docs: list, created_at: str) -> int:
    """행을 upsert 하고, 이전 실행에서 넣었지만 이번에 없는 소제목 행은 지운다(같은 CSV doc_id 한정)."""
    for doc in docs:
        set_fields = {k: v for k, v in doc.items() if k != "metadata"}
        set_fields.update({f"metadata.{k}": v for k, v in doc["metadata"].items()})
        col.update_one(
            {"doc_id": doc["doc_id"], "article_number": doc["article_number"]},
            {"$set": set_fields, "$setOnInsert": {"metadata.created_at": created_at}},
            upsert=True,
        )
    keep = [d["article_number"] for d in docs]
    col.delete_many({"doc_id": docs[0]["doc_id"], "article_number": {"$nin": keep}})
    return len(docs)


def preview(docs: list) -> dict:
    head = docs[0]
    return {"doc_id": head["doc_id"], "title": head["title"], "sub_title": head["sub_title"],
            "effective": head["metadata"]["effective"],
            "sections": [{"name": d["article_title"], "chars": len(d["content"])} for d in docs]}
