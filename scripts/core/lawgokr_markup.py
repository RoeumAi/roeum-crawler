"""law.go.kr 상세 화면에서 부제·본문 자리를 찾는다(화면 개편에 흔들리지 않게 한 곳에 둔다).

2026-11-15 개편으로 두 가지가 바뀌었다.
- 부제: `div.subtit1`(·`div.subtit2`) → `div.ct_sub`
- 본문: `#conScroll` 바로 아래 섹션(【판시사항】 등)이 있던 것이 `div.pgroup.conScroll` 한 겹 안으로 들어감
섹션을 `#conScroll` 의 바로 아래 자식으로 읽는 파서(section_document_scraper·판례)는 감싼 칸을 벗겨야 섹션을 나눈다.
"""

from bs4 import BeautifulSoup, Comment

SUBTITLE_SELECTOR = "div.subtit1, div.subtit2, .subtit1, .subtit2, div.ct_sub"
# Playwright 로케이터용(쉼표로 이은 CSS 도 그대로 받는다)
SUBTITLE_LOCATOR = "div.subtit1, div.subtit2, div.ct_sub"


def subtitle_text(soup: BeautifulSoup) -> str:
    """부제 칸의 글. 없으면 빈 문자열."""
    el = soup.select_one(SUBTITLE_SELECTOR)
    return el.get_text().strip() if el else ""  # 옛 파서와 같이 구분자 없이 이어 붙인다(저장된 부제와 같게)


def _only_child_wrapper(el):
    """자식 요소가 div 하나뿐이고 그 div 가 여러 블록을 품은 감싼 칸이면 그 div, 아니면 None."""
    children = [c for c in el.children if getattr(c, "name", None)]
    texts = [str(c).strip() for c in el.children
             if not getattr(c, "name", None) and not isinstance(c, Comment) and str(c).strip()]
    if len(children) != 1 or texts:
        return None
    child = children[0]
    grandchildren = [c for c in child.children if getattr(c, "name", None)]
    if child.name != "div" or len(grandchildren) < 2:
        return None
    return child


def content_html(soup: BeautifulSoup) -> str:
    """본문(`#conScroll`) 안쪽 HTML. 감싼 칸만 한 겹 있으면 벗겨 섹션이 바로 아래 자식이 되게 한다."""
    root = soup.select_one("#conScroll")
    if root is None:
        return ""
    while True:
        inner = _only_child_wrapper(root)
        if inner is None:
            return root.decode_contents()
        root = inner
