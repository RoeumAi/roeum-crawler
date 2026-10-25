"""행정해석 CSV(wm_id, sub_title, wm_content2)를 interpretation 컬렉션 문서로 바꾸는 순수 로직.

크롤러가 law.go.kr 에서 수집한 문서(scraper.save_to_mongodb)와 같은 모양으로 만든다:
소제목(질의요지·회답 …)마다 한 행, chunk_id·content_hash 는 같은 규칙.
DB 접근은 runners/import_csv.py 가 한다. 여기서는 변환과 중복 판정만 한다.
"""
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from scripts.core.identifiers import article_chunk_id
from scripts.core.database.source_versioning import enrich_source_document

DOC_ID_PREFIX = "csv_interp_"

# 줄 하나가 소제목이면 그 이름을 돌려준다. 괄호(【】·[])로 감싼 줄은 이름과 상관없이 소제목,
# 괄호 없는 줄은 아래 이름일 때만 소제목으로 본다(본문 첫 줄이 우연히 짧은 경우를 막는다).
_BARE_HEADERS = re.compile(
    r"^(질의(요지|내용|사항|배경)?|회시|회답|회신|답변(내용)?|이유|요지|해석변경|"
    r"갑설|을설|병설|정설|참고|사실관계|사례|예시|개요|사건개요|상황개요|주문)\d*$"
)
_BRACKET_LINE = re.compile(r"^[【\[]\s*([^】\]]{1,20}?)\s*[】\]]$")
_NUMBERING = re.compile(r"^(\d{1,2}|[가-하])[.)]\s*")
_DATE = re.compile(r"(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})\.?")


def normalize_key(value: str) -> str:
    """문서번호 비교용: 공백·대시 변형을 없앤다."""
    return re.sub(r"\s", "", value or "").replace("－", "-").replace("–", "-")


def parse_date(text: str) -> Optional[str]:
    """'2026. 4. 14.' / '2019-04-17' → '2026-04-14'. 연도가 말이 안 되면 None."""
    m = _DATE.search(text or "")
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if not (1950 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


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
    """본문을 소제목 단위로 나눈다. 소제목이 없으면 '본문' 한 덩어리."""
    sections, title, buf = [], None, []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append({"article_title": title or "본문", "content": text})

    for line in (content or "").replace("\r\n", "\n").split("\n"):
        name = _header_name(line)
        if name:
            flush()
            title, buf = _section_title(name), []
        else:
            buf.append(line.rstrip())
    flush()
    # 빈 줄 여러 개를 하나로
    for s in sections:
        s["content"] = re.sub(r"\n{3,}", "\n\n", s["content"])
    return sections


def _clean_line(line: str) -> str:
    text = re.sub(r"^[\s○◦•·❏■□▶\-–※]*", "", line)
    text = re.sub(r"^(\d{1,2}|[가-하])[.)]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def make_title(sections: list, limit: int = 80) -> str:
    """CSV 에는 제목이 없어 질의에서 만든다.

    수집 문서 제목은 대개 '…지 여부' 꼴의 요약이다. 질의 안에 '여부'로 끝나는 문장이 있으면
    그 문장을 쓰고, 없으면 첫 줄을 limit 자로 자른다.
    """
    source = next((s for s in sections if s["article_title"].startswith("질의요지")), None) or (
        sections[0] if sections else None
    )
    if not source:
        return ""
    lines = [t for t in (_clean_line(l) for l in source["content"].split("\n")) if len(t) >= 5]
    for line in lines:
        m = re.search(r"([^.?!]*?여부)", line)
        if m and 10 <= len(m.group(1).strip()) <= 150:
            return m.group(1).strip()
    if not lines:
        return ""
    first = lines[0]
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


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


def agency_for(number: str) -> str:
    """CSV 는 대부분 고용노동부 회시지만 국세청 과세기준자문(…법규소득…)이 섞여 있다."""
    return "국세청" if "법규" in (number or "") else "고용노동부"


def build_documents(row: dict, now_iso: str, source_file: str, import_batch: str) -> list:
    """CSV 한 행 → interpretation 컬렉션 행 목록(소제목마다 한 행)."""
    number, date = split_sub_title(row["sub_title"])
    sections = split_sections(row["wm_content2"])
    title = make_title(sections) or number
    doc_id = f"{DOC_ID_PREFIX}{row['wm_id'].strip()}"
    raw_date = date.replace("-0", "-").replace("-", ".") if date else ""
    agency = agency_for(number)
    sub_title = ", ".join(p for p in [agency, number, raw_date] if p)
    docs = []
    for idx, section in enumerate(sections, 1):
        article_number = str(idx)
        doc = {
            "chunk_id": article_chunk_id("interpretation", doc_id, article_number),
            "doc_id": doc_id,
            "article_number": article_number,
            "article_title": section["article_title"],
            "title": title,
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
        docs.append(enrich_source_document(doc, "interpretation"))
    return docs


SKIP_EXISTING = "이미 수집된 문서(문서번호·날짜 일치)"
SKIP_IMPORTED = "이전에 넣은 CSV 문서(doc_id 일치)"
SKIP_LEGISLATION = "법제처 해석례(legislation_expc 수집 대상)"
SKIP_CSV_DUP = "CSV 안 중복(문서번호·날짜 일치)"
SKIP_EMPTY = "본문 없음"


def plan_rows(rows: list, existing: ExistingIndex, imported_doc_ids: set) -> list:
    """각 행을 ('insert', None) 또는 ('skip', 사유)로 분류한다. 순서는 CSV 그대로."""
    seen, plan = set(), []
    for row in rows:
        number, date = split_sub_title(row["sub_title"])
        key = (normalize_key(number), date)
        if not (row.get("wm_content2") or "").strip():
            reason = SKIP_EMPTY
        elif normalize_key(number).startswith("법제처"):
            reason = SKIP_LEGISLATION
        elif f"{DOC_ID_PREFIX}{row['wm_id'].strip()}" in imported_doc_ids:
            reason = SKIP_IMPORTED
        elif existing.contains(number, date):
            reason = SKIP_EXISTING
        elif key in seen:
            reason = SKIP_CSV_DUP
        else:
            reason = None
        seen.add(key)
        plan.append((row, "skip" if reason else "insert", reason))
    return plan
