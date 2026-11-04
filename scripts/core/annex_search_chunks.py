"""Keep the display document intact; index bounded, source-addressable passages."""
from copy import deepcopy
from collections import Counter
from scripts.utils.document_blocks import blocks_markdown

MAX_PASSAGE_CHARS = 3500  # Leaves title/subtitle room under the embedding input cap.
RECIPE = 'annex-passages-v1'

def split_text(text, limit=MAX_PASSAGE_CHARS):
    """Exact body slices, preferring line boundaries; repeat Markdown table headings."""
    start, header = 0, ''
    lines = text.splitlines()
    if len(lines) > 1 and '|' in lines[0] and set(lines[1].replace('|', '').replace(':', '').strip()) <= {'-', ' '}:
        candidate = '\n'.join(lines[:2]) + '\n'
        if len(candidate) < limit // 3:
            header = candidate
    while start < len(text):
        prefix = header if start else ''
        end = min(len(text), start + limit - len(prefix))
        if end < len(text):
            boundary = text.rfind('\n', start, end)
            if boundary > start + (end-start)//2:
                end = boundary + 1
        yield prefix + text[start:end], start, end
        start = end

def search_documents(document):
    """Return canonical display row followed by optional retrieval-only children."""
    original = deepcopy(document)
    meta = original.setdefault('metadata', {})
    content = original.get('content') or ''
    if not meta.get('is_annex'):
        return [original]
    if len(content) <= MAX_PASSAGE_CHARS:
        meta.update(search_parent=False, is_searchable=True, search_recipe=RECIPE)
        return [original]
    blocks = meta.get('document_blocks') or []
    chars = lambda value: Counter(c for c in value if not c.isspace())
    if chars(blocks_markdown(blocks)) != chars(content):
        blocks = [{'kind': 'text', 'text': content}]
    parts = []
    for block in blocks:
        rendered = blocks_markdown([block])
        if not rendered.strip():
            continue
        for text, start, end in split_text(rendered):
            # Whole tables retain typed cells. Oversized tables retain row Markdown.
            view = deepcopy(block) if start == 0 and end == len(rendered) else {
                'kind': 'text', 'format': 'markdown', 'text': text,
                'source_pages': block.get('source_pages', [])}
            parts.append((text, view, block.get('source_pages', [])))
    if not parts:
        meta.update(search_parent=False, is_searchable=True)
        return [original]
    packed = []
    for text, block, pages in parts:
        if packed and len(packed[-1][0]) + 2 + len(text) <= MAX_PASSAGE_CHARS:
            previous, views, source_pages = packed[-1]
            packed[-1] = (previous + '\n\n' + text, [*views, block], sorted(set(source_pages + pages)))
        else:
            packed.append((text, [block], pages))
    parts = packed
    parent_id = original['chunk_id']
    meta.update(search_parent=True, is_searchable=False, search_recipe=RECIPE, search_part_count=len(parts))
    children = []
    for index, (text, views, pages) in enumerate(parts, 1):
        child = deepcopy(original)
        child['chunk_id'] = f'{parent_id}:part:{index:04d}'
        child['content'] = text
        # Search representation contains only its own structure, never the entire annex.
        child['metadata'].update(search_parent=False, search_only=True, is_searchable=True,
            parent_chunk_id=parent_id, search_part_index=index, document_blocks=views, source_pages=pages)
        for key in ('document_text', 'document_blocks_scope'):
            child['metadata'].pop(key, None)
        children.append(child)
    return [original, *children]
