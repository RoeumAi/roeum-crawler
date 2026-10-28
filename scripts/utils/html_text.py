"""HTML 본문을 문단이 살아 있는 글로 만든다.

국가법령정보센터 판례·재결례·결정례·해석례 페이지는 본문을 `<p>` 문단으로 준다(`pty4_dep1` 처럼
dep 숫자가 들여쓰기 깊이). 예전 수집기는 문단을 공백으로 이어 붙여 긴 본문이 한 줄이 됐다
(행정심판재결례 4,609행 등). 블록 요소마다 빈 줄로 나누고 `<br>` 은 줄바꿈으로 둔다.
"""
import re

from bs4 import NavigableString, Tag

BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "dl", "dd", "dt", "blockquote", "pre"}


def block_text(element) -> str:
    """요소 안의 글을 블록 경계는 빈 줄, `<br>` 는 줄바꿈으로 바꿔 돌려준다(원본 요소는 바꾸지 않는다)."""
    parts: list[str] = []

    def walk(node):
        if isinstance(node, NavigableString):
            parts.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "br":
            parts.append("\n")
            return
        block = node.name in BLOCK_TAGS
        if block:
            parts.append("\n\n")
        for child in node.children:
            walk(child)
        if block:
            parts.append("\n\n")

    walk(element)
    text = "".join(parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
