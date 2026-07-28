# NLRC PDF Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store the full attached-PDF text for NLRC major judgment and mediation documents instead of only their short HTML summaries.

**Architecture:** The crawler extracts attachment identifiers from each NLRC detail page, downloads the official PDF, and posts it to the existing `chat_generation /api/extract-text` hybrid native/OCR endpoint. HTML summary text remains as a fallback, while successful PDF text and extraction provenance are saved in MongoDB.

**Tech Stack:** Python, BeautifulSoup, requests/curl_cffi, FastAPI multipart API, MongoDB, unittest

## Global Constraints

- Do not change `chat_generation`; consume its existing `POST /api/extract-text` contract.
- Default OCR service URL is `http://127.0.0.1:8000`, overridable with `CHAT_GENERATION_URL`.
- Only `judgment` and `mediation_case` use PDF attachments; `decision` remains list-text based.
- A PDF or OCR failure must preserve HTML content and expose fallback metadata.

---

### Task 1: NLRC Attachment Client

**Files:**
- Create: `scripts/utils/nlrc_pdf.py`
- Test: `tests/test_nlrc_pdf.py`

**Interfaces:**
- Produces: `extract_pdf_attachments(html) -> list[dict]`
- Produces: `extract_attachment_text(html, detail_url, base_url=None) -> dict`

- [ ] Write tests for attachment parsing, successful extraction, missing attachment, and API failure.
- [ ] Run the focused test and confirm it fails because the module is absent.
- [ ] Implement HTML parsing, official PDF download, multipart extraction request, and structured fallback results.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Judgment and Mediation Integration

**Files:**
- Modify: `scripts/judgment/logic/scraper.py`
- Modify: `scripts/mediation_case/logic/scraper.py`
- Test: `tests/test_nlrc_pdf_scrapers.py`

**Interfaces:**
- Consumes: `extract_attachment_text(...)`
- Produces MongoDB metadata: `attachment_name`, `attachment_url`,
  `attachment_file_id`, `content_source`, `pdf_page_count`,
  `pdf_is_searchable`, `pdf_cost_usd`

- [ ] Write failing tests showing PDF full text is included and HTML fallback is retained.
- [ ] Integrate PDF extraction into both scrapers.
- [ ] Propagate extraction metadata without changing each collection's stable `chunk_id`.
- [ ] Run focused and full crawler tests.

### Task 3: Mac Mini Deployment and Backfill

**Files:**
- Deploy only the modified crawler files and tests to `/Users/loum/loum/roeum-crawler`.

- [ ] Verify `http://127.0.0.1:8000/health`.
- [ ] Run remote unit tests.
- [ ] Re-crawl one searchable mediation PDF and one scanned judgment PDF.
- [ ] Confirm full text and attachment metadata in MongoDB.
- [ ] Backfill all `judgment` and `mediation_case` documents with resumable logging.
- [ ] Verify document counts, non-empty content, extraction-source distribution, and failures.
