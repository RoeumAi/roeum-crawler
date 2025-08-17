# -*- coding: utf-8 -*-
"""
텍스트 정제/파싱 공통 유틸
- NBSP/공백 정리
- 대괄호 제거([ ... ] -> ... )
- CSS/XP로 텍스트 추출
- 간단 HTML->text 변환(br/p 개행 처리)
"""
from __future__ import annotations
import re
import html
from typing import Iterable, List, Optional
from parsel import Selector


_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_NBSP_TOKEN = re.compile(r"\bNBSP\b", flags=re.I)
_RE_BR = re.compile(r"<br\s*/?>", flags=re.I)
_RE_P_CLOSE = re.compile(r"</p\s*>", flags=re.I)
_RE_TAGS = re.compile(r"<[^>]+>")


def clean(s: Optional[str]) -> str:
    """NBSP/토큰 제거 + 공백 정리"""
    if not s:
        return ""
    s = s.replace("\xa0", " ")
    s = _RE_NBSP_TOKEN.sub(" ", s)
    s = _RE_MULTI_SPACE.sub(" ", s)
    return s.strip()


def strip_brackets(s: Optional[str]) -> str:
    """
    텍스트가 [ ... ]로 전체 감싸져 있으면 대괄호 제거
    예: "[대법원 2020다12345]" -> "대법원 2020다12345"
    """
    s = clean(s)
    if not s:
        return ""
    if s.startswith("[") and s.endswith("]"):
        return clean(s[1:-1])
    # 내부에 대괄호만 있는 경우도 정리: "[ ... ]" -> "..."
    if s.count("[") == 1 and s.count("]") == 1:
        s = s.replace("[", "").replace("]", "")
    return clean(s)


def html_to_text(html_str: str) -> str:
    """
    아주 가벼운 HTML -> text
    - <br> -> \n
    - </p> -> \n
    - 나머지 태그 제거
    - 엔티티 unescape
    """
    if not html_str:
        return ""
    x = _RE_BR.sub("\n", html_str)
    x = _RE_P_CLOSE.sub("\n", x)
    x = _RE_TAGS.sub(" ", x)
    x = html.unescape(x)
    return clean(x)


def text_from_selector(root: Selector, css: str, join_with: str = " ") -> str:
    """
    CSS 셀렉터에서 텍스트 추출(::text 기반)
    """
    parts = root.css(css + "::text").getall()
    return clean(join_with.join(parts))


def rich_text(root: Selector, css: str, join_with: str = "\n") -> str:
    """
    CSS 매칭 DOM들의 innerHTML을 가져와서 개행 보존 형태로 텍스트화
    """
    htmls = root.css(css).getall()
    if not htmls:
        return ""
    merged = join_with.join(htmls)
    return html_to_text(merged)


def first_of(*vals: Optional[str]) -> str:
    """여러 값 중 처음으로 비어있지 않은 텍스트 반환"""
    for v in vals:
        v = clean(v)
        if v:
            return v
    return ""


def exists(root: Selector, css: str) -> bool:
    return bool(root.css(css))
