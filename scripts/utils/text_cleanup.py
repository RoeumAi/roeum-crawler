"""수집 원문의 깨진 글자·HTML 흔적·조문 제목 공백을 고치는 순수 함수(멱등)."""
import re

_BROKEN_DOT = re.compile(r"(?<=[가-힣])？(?=[가-힣])")  # 한글 사이의 전각 물음표는 ㆍ가 깨진 것
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ARTICLE_TITLE_GAP = re.compile(r"\A(제\d+조(?:의\d+)?\([^)\n]*\))[ \t\u00a0\u3000]{2,}")


def clean_text(text: str, collection: str) -> str:
    if not text:
        return text
    text = _BROKEN_DOT.sub("ㆍ", text)
    # 마크다운 표 행(`|` 로 시작)의 <br> 은 칸 안 줄바꿈 표기이므로 그대로 둔다
    text = "\n".join(line if line.lstrip(" \t").startswith("|") else _BR.sub("\n", line)
                     for line in text.split("\n"))
    text = text.replace("&nbsp;", " ")
    if collection == "law":
        text = _ARTICLE_TITLE_GAP.sub(r"\1\n", text)
    return text
