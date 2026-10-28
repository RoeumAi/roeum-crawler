"""한 줄로 이어진 판결문 본문을 번호 앞에서 문단으로 나눈다.

CSV 로 넣은 판례 일부(4,153문서)는 원본 CSV 에 줄바꿈이 없어 "1. 기초사실 가. 당사자의 지위 …" 처럼
한 줄이다. 판결문 번호 체계(1. 가. 1) 가) (1) (가) ① ○)는 남아 있으므로 번호 앞에서 문단을 나눈다.

나누는 자리(번호 앞 공백)
- 앞 문장이 끝났을 때: 한글 + 마침표("…하였다."), 닫는 괄호·인용부호 + 마침표
- 앞이 짧은 제목일 때: 바로 앞 번호 뒤 글이 40자 이하이고 마침표가 없으면("2. 당사자들의 주장 가. …")

글자는 더하거나 빼지 않는다. 번호 앞 공백 → 빈 줄만 바꾼다(임베딩 해시는 공백을 접어 만들므로 다시
임베딩되지 않는다). 날짜("2002. 1. 1.")를 자르지 않도록 숫자 번호는 1~2자리만 보고, 앞이 숫자+마침표면
문장 끝으로 보지 않는다.
"""
import re

MARKER = r"(?:\d{1,2}\.|[가-하]\.|\d{1,2}\)|[가-하]\)|\(\d{1,2}\)|\([가-하]\)|[①-⑳]|[㉮-㉻]|○|※)"
MARKER_AT = re.compile(MARKER + r"[ \t]")
CANDIDATE = re.compile(r"[ \t]+(?=" + MARKER + r"[ \t])")
SENTENCE_END = re.compile(r"(?:[가-힣]|[」’”\)\]])\.$")
HEADER = re.compile(r"(【[^】]{1,20}】)[ \t]+")
TITLE_MAX = 40


def _title_since(text: str, item_start: int, end: int) -> bool:
    """item_start 의 번호 뒤부터 end 까지가 짧은 제목인가(마침표 없음)."""
    marker = MARKER_AT.match(text, item_start)
    body = text[marker.end() if marker else item_start:end]
    return len(body) <= TITLE_MAX and "." not in body


def split_paragraphs(text: str) -> str:
    if not text or "\n" in text:
        return text  # 이미 줄이 나뉜 본문은 건드리지 않는다
    text = HEADER.sub(lambda m: m.group(1) + "\n\n", text)
    pieces, last = [], 0
    item_start = 0 if MARKER_AT.match(text) else None
    for match in CANDIDATE.finditer(text):
        ends_sentence = bool(SENTENCE_END.search(text[max(last, match.start() - 3):match.start()]))
        after_title = item_start is not None and _title_since(text, item_start, match.start())
        if ends_sentence or after_title:
            pieces += [text[last:match.start()], "\n\n"]
            last = item_start = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def same_text_ignoring_space(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")
