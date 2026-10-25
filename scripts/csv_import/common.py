"""CSV 넣기 공통: 스트리밍 읽기, 날짜, 표 변환.

CSV 는 모두 `wm_id, sub_title, wm_content2` 세 열이다. 판례 CSV 는 1GB 를 넘어서
한 번에 읽지 않고 한 행씩 흘려 읽는다.
"""
import csv
import io
import re
import sys
import urllib.request
from typing import Iterator, Optional

REQUIRED_COLUMNS = {"wm_id", "sub_title", "wm_content2"}

_DATE = re.compile(r"(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})\.?")


def iter_rows(path: str = None, url: str = None) -> Iterator[dict]:
    csv.field_size_limit(sys.maxsize)
    if url:
        resp = urllib.request.urlopen(url, timeout=300)
        stream = io.TextIOWrapper(resp, encoding="utf-8-sig", newline="")
    else:
        stream = open(path, encoding="utf-8-sig", newline="")
    with stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV 열이 없습니다: {sorted(missing)}")
        yield from reader


def parse_date(text: str) -> Optional[str]:
    """'2026. 4. 14.' / '2019-04-17' → '2026-04-14'. 연도가 말이 안 되면 None."""
    m = _DATE.search(text or "")
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if not (1950 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_key(value: str) -> str:
    """번호 비교용: 공백·대시 변형을 없앤다."""
    return re.sub(r"\s", "", value or "").replace("－", "-").replace("–", "-")


def tidy(text: str) -> str:
    """줄 끝 공백을 지우고 빈 줄 여러 개를 하나로."""
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ── 표 ───────────────────────────────────────────────────────────────
# CSV 원문의 표는 `+----+----+` 테두리와 `| a | b |` 행으로 된 글자 표다. 화면은 마크다운
# 표(GFM)를 그리므로 그 꼴로 바꿔 저장한다. 테두리 사이의 여러 `|` 줄은 셀 안의 줄바꿈이라
# 한 행으로 합친다. 칸 수가 행마다 다르면 표가 아니라 글자 그림이라 코드 블록으로 감싼다.

_BORDER = re.compile(r"^\s*\+[-=+:\s]*\+\s*$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip()[1:-1].split("|")]


def _to_markdown(block: list) -> Optional[str]:
    rows, current = [], None
    bordered = any(_BORDER.match(l) for l in block)
    for line in block:
        if _BORDER.match(line) or _SEPARATOR.match(line):
            if current is not None:
                rows.append(current)
            current = None
            continue
        cells = _cells(line)
        if bordered:
            if current is None:
                current = [[c] for c in cells]
            elif len(cells) == len(current):
                for col, c in zip(current, cells):
                    col.append(c)
            else:
                return None
        else:
            rows.append([[c] for c in cells])
    if current is not None:
        rows.append(current)
    if len(rows) < 2 or len({len(r) for r in rows}) != 1:
        return None
    merged = [[" ".join(p for p in col if p).replace("|", "\\|") for col in r] for r in rows]
    out = ["| " + " | ".join(merged[0]) + " |", "|" + "---|" * len(merged[0])]
    out += ["| " + " | ".join(r) + " |" for r in merged[1:]]
    return "\n".join(out)


def convert_tables(text: str) -> str:
    """글자 표 묶음을 찾아 마크다운 표로 바꾼다. 바꿀 수 없는 묶음은 그대로 둔다."""
    lines = (text or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if _BORDER.match(lines[i]) or _ROW.match(lines[i]):
            j = i
            while j < len(lines) and (_BORDER.match(lines[j]) or _ROW.match(lines[j])):
                j += 1
            block = lines[i:j]
            table = _to_markdown(block) if any(_ROW.match(l) for l in block) else None
            if table:
                out += ["", table, ""]
            elif len(block) >= 2 and any(_BORDER.match(l) for l in block):
                # 표로 못 바꾸는 글자 그림(조직도 등)은 고정폭으로 보이게 코드 블록으로 감싼다
                out += ["", "```text", *block, "```", ""]
            else:
                out += block
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def has_table(text: str) -> bool:
    return bool(re.search(r"(?m)^\|.*\|\n\|(?:---\|)+$", text or ""))
