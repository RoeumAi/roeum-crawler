"""Strip PDF/print artifacts from crawled legal-document content.

These artifacts are introduced when a document body is extracted from a PDF
(directly via pymupdf in ``pdf_ingest``, or via the ``/api/extract-text``
service for NLRC attachments). They are meaningless to a reader and leak the
third-party source the PDF was printed from, so they must not appear in the
B2B citation panel or the public search DB.

Handled artifacts:
  A. hrdb.kr browser print header/footer block — a print timestamp line,
     one or two ``hrdb.kr/search/print?...`` URL lines, and a ``N/M`` page
     indicator, repeated on every printed page.
  B. NLRC page-break markers — a bare ``- N -`` line splitting a sentence.
  C. Attachment filename markers — ``[첨부: <filename>]`` lines inserted when
     concatenating multiple attachments.

Stub documents (only ``제목:``/``출처: <url>`` and no real body) are detected
separately via :func:`is_stub_content` so callers can flag rather than blank
them.

The cleaner is intentionally conservative: it only removes lines that match a
structural artifact pattern exactly, so legitimate body text — in-line dates
(``2017. 6. 22.``), ``오후경`` without a clock time, parenthetical citations
like ``(출처 : 화학용어사전)`` — is left untouched.
"""

from __future__ import annotations

import re

# A print timestamp emitted by the browser print dialog, e.g. "26. 7. 3. 오후 3:14".
# Requires a clock time (HH:MM) so in-body "2017. 6. 22. 오후경" is not matched.
_PRINT_TIMESTAMP = re.compile(
    r"^\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*오[전후]\s*\d{1,2}\s*:\s*\d{2}\s*$"
)

# An hrdb.kr print URL line (with or without scheme / www).
_HRDB_URL = re.compile(r"^\s*(?:https?://)?(?:www\.)?hrdb\.kr/\S*\s*$", re.IGNORECASE)

# A bare "N/M" page indicator line (only removed when part of a print footer).
_PAGE_FRACTION = re.compile(r"^\s*\d{1,3}\s*/\s*\d{1,3}\s*$")

# An NLRC page-break marker, e.g. "- 12 -" alone on a line.
_PAGE_DASH = re.compile(r"^\s*-\s*\d{1,3}\s*-\s*$")

# An attachment filename marker, e.g. "[첨부: 사례2.pdf]". The filename itself may
# contain brackets (NLRC embeds case numbers, e.g. "재심판정서[중앙2018부해1095].pdf"),
# so match greedily to the final "]" on the line. Requires the "첨부:" colon, so the
# "[첨부파일 전문]" section label (no colon) is left intact.
_ATTACHMENT = re.compile(r"^\s*\[첨부\s*[:：].*\]\s*$")

# Stub-document prefixes.
_TITLE_LINE = re.compile(r"^\s*제목\s*[:：]")
_SOURCE_URL_LINE = re.compile(r"^\s*출처\s*[:：]\s*https?://")

_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def clean_pdf_artifacts(text: str | None) -> str:
    """Remove PDF print/page artifacts from ``text``.

    Returns the cleaned text with excess blank lines collapsed and trimmed.
    Returns an empty string for ``None``/empty input.
    """
    if not text:
        return ""

    kept: list[str] = []
    prev_was_footer = False  # previous source line belonged to an hrdb print footer

    for line in text.split("\n"):
        if _PRINT_TIMESTAMP.match(line) or _HRDB_URL.match(line):
            prev_was_footer = True
            continue
        # A page fraction is only an artifact when it trails a print footer.
        if _PAGE_FRACTION.match(line) and prev_was_footer:
            continue
        if _PAGE_DASH.match(line) or _ATTACHMENT.match(line):
            prev_was_footer = False
            continue
        prev_was_footer = False
        kept.append(line)

    cleaned = "\n".join(kept)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def is_stub_content(text: str | None) -> bool:
    """Return True when ``text`` has no real body beyond a title/source stub.

    Documents whose entire content is ``제목: ...`` and ``출처: <url>`` (the
    scraper's fallback when the real body could not be fetched) are stubs.
    """
    if not text or not text.strip():
        return True

    remainder = "\n".join(
        line
        for line in text.split("\n")
        if not _TITLE_LINE.match(line) and not _SOURCE_URL_LINE.match(line)
    ).strip()
    return remainder == ""
