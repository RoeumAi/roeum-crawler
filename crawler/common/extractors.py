# -*- coding: utf-8 -*-
"""
섹션 추출기
- 판례 상세 페이지 공통 추출 로직(제목/부제/섹션들)
- DOM이 다르면 각 스파이더에서 이 모듈 함수를 조합하거나 오버라이드
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple

from parsel import Selector

from .utils import clean, strip_brackets, text_from_selector, rich_text, first_of


# ---------- 헤더(제목/부제 등) ----------

def extract_prec_header(resp: Selector) -> Tuple[str, str]:
    """
    제목(h2[data-brl-use="PH/H1"])과 subtitle1 추출
    - subtitle1은 대괄호 제거
    """
    title = first_of(
        text_from_selector(resp, 'h2[data-brl-use="PH/H1"]'),
        text_from_selector(resp, '#conTop h1'),
        text_from_selector(resp, '#conTop h2'),
    )

    # subtitle1: 클래스/속성 케이스가 섞여 있어 여러 후보를 순회
    subtitle = first_of(
        text_from_selector(resp, ".subtitle1"),
        text_from_selector(resp, ".subtit1"),
        text_from_selector(resp, ".subTit1"),
        text_from_selector(resp, "[data-subtitle1]"),
    )
    subtitle = strip_brackets(subtitle)
    return clean(title), clean(subtitle)


# ---------- 섹션 공통 헬퍼 ----------

def _by_label_next_block(root: Selector, label: str) -> str:
    """
    다양한 레이아웃에서 '라벨 다음의 컨텐트'를 최대한 찾아서 텍스트를 반환
    지원 패턴:
      - <dl><dt>라벨</dt><dd>내용</dd></dl>
      - <table><th>라벨</th><td>내용</td></table>
      - <h3|h4|div.tit>라벨</...><div class="cont">내용</div>
      - 기타: 라벨 노드의 다음 형제 블럭
    """
    # dt -> dd
    dd = root.xpath(f'.//dt[normalize-space()="{label}"]/following-sibling::dd[1]')
    if dd:
        return rich_text(dd, "*")

    # th -> td
    td = root.xpath(f'.//th[contains(normalize-space(.), "{label}")]/following-sibling::td[1]')
    if td:
        return rich_text(td, "*")

    # 제목 블럭 + 다음 블럭
    head = root.xpath(
        f'.//*[self::h3 or self::h4 or self::h5 or contains(@class,"tit")]'
        f'[contains(normalize-space(.), "{label}")]'
    )
    if head:
        nxt = head.xpath("./following-sibling::*[1]")
        if nxt:
            return rich_text(nxt, "*")

    # aria/role label 변형
    lab = root.xpath(
        f'.//*[@aria-label="{label}" or @title="{label}" or @data-title="{label}"]'
    )
    if lab:
        # 자신 내 텍스트 or 다음 형제 시도
        txt = rich_text(lab, "*")
        if txt:
            return txt
        nxt = lab.xpath("./following-sibling::*[1]")
        if nxt:
            return rich_text(nxt, "*")

    return ""


def extract_section_texts(resp: Selector) -> Dict[str, str]:
    """
    판시사항 / 판결요지 / 참조조문 의 텍스트 딕셔너리 반환
    """
    sections = {}
    for label in ("판시사항", "판결요지", "참조조문"):
        txt = _by_label_next_block(resp, label)
        sections[label] = clean(txt)
    return sections


def extract_jeonmun(resp: Selector) -> Dict[str, str]:
    """
    전문({주문, 이유}) 추출
    - 먼저 "전문" 블럭이 있으면 그 내부에서 '주문/이유'를 찾고
    - 없으면 페이지 전체에서 '주문/이유'를 개별 탐색
    """
    result = {"주문": "", "이유": ""}

    # 1) "전문" 컨테이너 내부
    jeon_container_html = _by_label_next_block(resp, "전문")
    if jeon_container_html:
        # container를 Selector로 다시 감싸고 내부 탐색
        sub = Selector(text=f"<div>{jeon_container_html}</div>")
        result["주문"] = clean(_by_label_next_block(sub, "주문")) or clean(rich_text(sub, ".order, .joomun, .judgment-order, *:contains('주문')"))
        result["이유"] = clean(_by_label_next_block(sub, "이유")) or clean(rich_text(sub, ".reason, .iyu, .grounds, *:contains('이유')"))

    # 2) 전체에서 보조 탐색(없을 때)
    if not result["주문"]:
        result["주문"] = clean(_by_label_next_block(resp, "주문"))
    if not result["이유"]:
        result["이유"] = clean(_by_label_next_block(resp, "이유"))

    return result


# ---------- 메인 추출기 (한 번에) ----------

def extract_prec_detail(resp: Selector) -> Dict[str, object]:
    """
    판례 상세페이지에서 필요한 모든 요소 추출
    반환 스키마:
    {
      "title": str,
      "subtitle1": str,
      "sections": {
         "판시사항": str,
         "판결요지": str,
         "참조조문": str,
         "전문": { "주문": str, "이유": str }
      }
    }
    """
    title, subtitle1 = extract_prec_header(resp)
    secs = extract_section_texts(resp)
    jm = extract_jeonmun(resp)

    return {
        "title": title,
        "subtitle1": subtitle1,
        "sections": {
            "판시사항": secs.get("판시사항", ""),
            "판결요지": secs.get("판결요지", ""),
            "참조조문": secs.get("참조조문", ""),
            "전문": jm,
        },
    }
