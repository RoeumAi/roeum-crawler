"""chat_generation 의 document_blocks 를 표가 살아 있는 글로 만든다.

파서가 주는 structured_text 는 글 블록을 이어 붙이되 표를 탭으로 줄 맞춘 글로 쓴다.
공백을 접는 화면·검색 조각에서는 표가 한 덩어리 문장이 된다. 같은 블록을 쓰되 표만
Markdown 표(`| a | b |`)로 바꾼다. 스캔 PDF 의 OCR 결과는 이미 이 모양이라 결과가 한 가지로 맞춰진다.
병합 칸은 첫 칸에만 글을 두고 나머지는 비운다. 칸 안 줄바꿈은 `<br>`.
"""


def _cell_text(text: str) -> str:
    return " <br> ".join(part.strip() for part in str(text or "").splitlines() if part.strip()).replace("|", "\\|")


def table_markdown(cells: list) -> str:
    if not cells:
        return ""
    rows = max(c["row"] + max(int(c.get("row_span") or 1), 1) for c in cells)
    cols = max(c["column"] + max(int(c.get("column_span") or 1), 1) for c in cells)
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in cells:
        grid[cell["row"]][cell["column"]] = _cell_text(cell.get("text"))
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * cols]
    lines += ["| " + " | ".join(row) + " |" for row in grid[1:]]
    return "\n".join(lines)


def has_table(blocks) -> bool:
    return any(block.get("kind") == "table" and block.get("cells") for block in blocks or [])


def blocks_markdown(blocks: list) -> str:
    pieces = []
    for block in blocks or []:
        if block.get("kind") == "table":
            rendered = table_markdown(block.get("cells") or [])
        else:
            rendered = str(block.get("text") or "")
        if rendered.strip():
            pieces.append(rendered)
    return "\n\n".join(pieces)
