import re

def clean_inline(s: str) -> str:
    """
    한 줄 텍스트용 클린업: NBSP/nbsp 제거 + 공백 정돈
    """
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_block(s: str) -> str:
    """
    여러 줄 텍스트용 클린업: NBSP/nbsp 제거 + 줄바꿈 유지
    """
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def strip_brackets(s: str) -> str:
    """
    문자열이 [내용] 형태면 대괄호 제거
    """
    if not s:
        return ""
    m = re.match(r"^\s*\[(.+?)\]\s*$", s)
    return m.group(1).strip() if m else s.strip()

def node_text(sel) -> str:
    """
    하나의 노드에서 텍스트만 모아 블록으로 변환
    (내부 <br> → 개행, 공백 정돈)
    """
    html = sel.get() or ""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    parts = sel.xpath(".//text()").getall()
    txt = "\n".join(p.strip() for p in parts if p and p.strip())
    return clean_block(txt)

def collect_until(nodes, stop_if):
    """
    nodes를 순회하며 stop_if(node, tag)가 True가 될 때까지
    p/div/ul/ol/table/blockquote 의 텍스트를 수집
    """
    chunks = []
    for n in nodes:
        tag = getattr(n.root, "tag", "").lower()
        if stop_if(n, tag):
            break
        if tag in {"p", "div", "ul", "ol", "table", "blockquote"}:
            t = node_text(n)
            if t:
                chunks.append(t)
    return "\n\n".join(chunks).strip()
