"""판례 CSV → case 컬렉션 행.

크롤러(case/logic/scraper.save_case_chunks_to_mongodb)가 저장하는 모양 그대로: 소제목(판결요지·주문·이유 …)
마다 doc_type 하나, 8,000 토큰이 넘는 소제목은 토큰 단위로 나눠 chunk_seq 를 붙인다. 저장도 그 함수를 쓴다.

CSV 본문은 두 가지 꼴이다.
- 전문: '판결 / 사건 / 원고 / 피고 / … / 주문 / 청구취지 / 이유' 가 한 줄씩 소제목으로 나온다.
- 요약: '【요 지】 …' 다음 '◈ 대법원 … 판결 [사건명] ◈' 과 '* 사 건 : …' 줄, 그리고 '【주 문】' '【이 유】'.
첫 실질 소제목 앞의 당사자·사건 정보는 '사건정보' 한 덩어리로 둔다(크롤러도 당사자 소제목은 합친다).
"""
import re
from typing import Optional

from scripts.csv_import.common import convert_tables, normalize_key, parse_date, tidy

COLLECTION = "case"
DOC_ID_PREFIX = "csv_case_"

# 소제목 이름(공백 제거) → 저장 이름. 크롤러가 쓰는 이름에 맞춘다.
SECTION_NAMES = {
    "요지": "판결요지", "판결요지": "판결요지", "판시사항": "판시사항",
    "참조조문": "참조조문", "참조판례": "참조판례", "주문": "주문",
    "청구취지": "청구취지", "항소취지": "항소취지", "청구취지및항소취지": "청구취지 및 항소취지",
    "상고취지": "상고취지", "상고이유": "상고이유", "신청취지": "신청취지", "재심신청취지": "재심신청취지",
    "이유": "이유", "범죄사실": "범죄사실", "증거의요지": "증거의 요지", "법령의적용": "법령의 적용",
    "양형의이유": "양형의 이유", "범죄사실및증거의요지": "범죄사실 및 증거의 요지",
    "별지": "별지", "판단": "판단",
}
INFO = "사건정보"
_PARTY = re.compile(r"원고|피고|검사|변호인|원심|판결|결정|사건|참가인|신청인|상고인|항소인|선고|변론")
_BRACKET = re.compile(r"^\s*【\s*([^】]{1,20}?)\s*】\s*(.*)$")
_CASE_NO = re.compile(r"(\d{2,4})\s*([가-힣]{1,4})\s*(\d{1,7})")
_COURT = re.compile(r"[가-힣]+(?:법원|지원)")
_CASE_NO_PREFIX = re.compile(r"^\s*((\d{2,4}\s*[가-힣]{1,4}\s*\d{1,7})\s*(\([^)]*\))?[,\s]*)+")


def _key(name: str) -> str:
    return re.sub(r"\s", "", name)


def split_sections(content: str) -> list:
    """본문 → [{'title': 저장 이름, 'text': 본문}]. 같은 이름은 하나로 합친다. 글자 표는 마크다운 표로."""
    order, texts = [], {}
    current = INFO

    def add(name, line):
        if name not in texts:
            order.append(name)
            texts[name] = []
        texts[name].append(line)

    for line in (content or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        bracket = _BRACKET.match(line)
        if bracket:
            name = SECTION_NAMES.get(_key(bracket.group(1)))
            if name:
                current = name
                add(current, bracket.group(2))
            elif _PARTY.search(bracket.group(1)):
                add(INFO, stripped)
            else:
                current = re.sub(r"\s+", " ", bracket.group(1)).strip()
                add(current, bracket.group(2))
            continue
        if stripped.startswith("◈"):
            add(INFO, stripped)
            current = INFO
            continue
        bare = SECTION_NAMES.get(_key(stripped)) if len(stripped) <= 20 else None
        if bare:
            current = bare
            add(current, "")
            continue
        add(current, line)

    sections = []
    for name in order:
        text = tidy(convert_tables("\n".join(texts[name])))
        if text:
            sections.append({"title": name, "text": text})
    return sections


def case_title(content: str, fallback: str) -> str:
    """사건명(크롤링 판례의 title 과 같은 값). 못 찾으면 사건번호."""
    lines = [l.strip() for l in (content or "").split("\n")]
    candidates = []
    for i, line in enumerate(lines):
        m = re.match(r"^\*?\s*사\s*건\s*[:：]\s*(.+)$", line)
        if m:
            candidates.append(m.group(1))
        elif _key(line) == "사건":
            nxt = next((l for l in lines[i + 1:] if l), "")
            candidates.append(nxt)
        if candidates:
            break
    if not candidates:
        m = re.search(r"◈.*?\[([^\]]+)\]\s*◈", content or "")
        if m:
            candidates.append(m.group(1))
    for text in candidates:
        name = _CASE_NO_PREFIX.sub("", text).strip(" ,")
        if name and not _CASE_NO.fullmatch(name):
            return re.sub(r"\s+", " ", name)[:200]
    return fallback


def court_and_numbers(text: str) -> list:
    """'대구지방법원 포항지원 2026. 9. 15. 선고 2024가단380 판결' → [('포항지원', '2024가단380')].
    사건번호마다 바로 앞 법원 이름을 붙인다(같은 번호가 다른 법원에도 있어서)."""
    pairs, court = [], ""
    tokens = sorted([(m.start(), "court", m.group(0)) for m in _COURT.finditer(text or "")]
                    + [(m.start(), "no", "".join(m.groups())) for m in _CASE_NO.finditer(text or "")])
    for _, kind, value in tokens:
        if kind == "court":
            court = value
        else:
            pairs.append((court, value))
    return pairs


def case_keys(text: str) -> set:
    return {normalize_key(c + n) for c, n in court_and_numbers(text)}


def court_of(sub_title: str) -> str:
    return re.split(r"\s\d{4}\s*\.", (sub_title or "").strip())[0].strip()


def load_existing(col) -> tuple:
    keys, imported = set(), set()
    for doc in col.find({}, {"doc_id": 1, "sub_title": 1}).batch_size(5000):
        doc_id = str(doc.get("doc_id") or "")
        if doc_id.startswith(DOC_ID_PREFIX):
            imported.add(doc_id)
        else:
            keys |= case_keys(doc.get("sub_title") or "")
    return keys, imported


SKIP_EXISTING = "이미 수집된 판례(법원·사건번호 일치)"
SKIP_CSV_DUP = "CSV 안 중복(법원·사건번호 일치)"
SKIP_EMPTY = "본문 없음"
SKIP_COURT = "이번 단계 법원이 아님"


class Planner:
    def __init__(self, existing_keys: set, imported_doc_ids: set, court_pattern: Optional[str] = None):
        self.existing, self.imported, self.seen = existing_keys, imported_doc_ids, set()
        self.court = re.compile(court_pattern) if court_pattern else None

    def decide(self, row: dict) -> tuple:
        keys = case_keys(row["sub_title"])
        if self.court and not self.court.search(court_of(row["sub_title"])):
            return ("skip", SKIP_COURT)
        if not (row.get("wm_content2") or "").strip():
            result = ("skip", SKIP_EMPTY)
        elif keys and keys & self.seen:
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
    """저장 함수(save_case_chunks_to_mongodb) 인자 묶음."""
    sub_title = re.sub(r"\s+", " ", row["sub_title"]).strip()
    numbers = [n for _, n in court_and_numbers(sub_title)]
    number = numbers[-1] if numbers else sub_title
    title = case_title(row["wm_content2"], number)
    meta = {
        "chapter": title,
        "source_type": "csv",
        "source_file": source_file,
        "import_batch": import_batch,
        "csv_wm_id": row["wm_id"].strip(),
        "court": court_of(sub_title),
        "case_number": number,
    }
    sections = split_sections(row["wm_content2"])
    return {
        "doc_id": doc_id_for(row),
        "title": title,
        "sub_title": sub_title,
        "effective": parse_date(sub_title),
        "chunks": [{"title": s["title"], "text": s["text"], "metadata": dict(meta)} for s in sections],
    }


def write(col, doc: dict, created_at: str) -> int:
    from scripts.case.logic.scraper import save_case_chunks_to_mongodb
    ok = save_case_chunks_to_mongodb(doc["doc_id"], doc["title"], doc["sub_title"], "", doc["chunks"])
    if not ok:
        raise RuntimeError(f"판례 저장 실패: {doc['doc_id']}")
    return len(doc["chunks"])


def preview(doc: dict) -> dict:
    return {"doc_id": doc["doc_id"], "title": doc["title"], "sub_title": doc["sub_title"],
            "effective": doc["effective"],
            "sections": [{"name": c["title"], "chars": len(c["text"])} for c in doc["chunks"]]}
