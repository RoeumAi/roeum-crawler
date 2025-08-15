import re
from urllib.parse import urljoin
import scrapy
from scrapy_playwright.page import PageMethod

# ===================== 공용 유틸 =====================

LIST_SEL = "#resultTableDiv"

def _clean_inline(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _clean_block(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _node_text(sel) -> str:
    # 링크는 클릭하지 않고 텍스트만 추출
    html = sel.get() or ""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    parts = [t for t in sel.xpath(".//text()").getall()]
    txt = "\n".join([p for p in (p.strip() for p in parts) if p])
    return _clean_block(txt)

CIRCLES = {chr(0x2460 + i): i+1 for i in range(20)}  # ①..⑳

def _circle_to_int(s: str) -> int | None:
    if not s:
        return None
    ch = s.strip()[0]
    if ch in CIRCLES:
        return CIRCLES[ch]
    m = re.match(r"제\s*(\d+)\s*항", s)  # "제1항"
    if m:
        return int(m.group(1))
    return None

def _split_hang(text: str):
    """
    본문에서 항(①/제n항) 기준으로 분해.
    반환: [(para_no or None, chunk_text), ...]
    """
    if not text:
        return []
    # 마커 위치 수집
    pat = re.compile(r"(?m)^(?:\s*(제\s*\d+\s*항)|\s*([\u2460-\u2473]))\s*")
    idxs = []
    for m in pat.finditer(text):
        para_no = _circle_to_int(m.group(1) or m.group(2))
        idxs.append((m.start(), para_no))
    if not idxs:
        return [(None, text.strip())]

    res = []
    for i, (start, pno) in enumerate(idxs):
        end = idxs[i+1][0] if i+1 < len(idxs) else len(text)
        chunk = text[start:end].strip()
        # 선두의 마커 텍스트는 제거
        chunk = re.sub(r"^(?:\s*제\s*\d+\s*항|\s*[\u2460-\u2473])\s*", "", chunk)
        if chunk:
            res.append((pno, chunk))
    return res

def _wait_mask_clear_js() -> str:
    return """
    () => {
      const nodes = Array.from(document.querySelectorAll('.loadmask, .loadmask-msg'));
      return !nodes.some(n => {
        const s = window.getComputedStyle(n);
        return s && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
      });
    }
    """

# ===================== 스파이더 =====================

class LawsChunksSpider(scrapy.Spider):
    """
    국가법령정보센터 > 법령 고급검색
    - 부처 클릭(기본: 1492000 고용노동부)
    - 목록의 onclick(lsReturnSearch)에서 lsiSeq/efYd 추출 → /lsInfoP.do 직접 접근
    - 상세에서 장/조/항 단위로 JSONL 라인 생성 (RAG 최적화)
    """
    name = "laws_chunks"
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, dept: str = "1492000", max_pages: int = 3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dept_id = str(dept)
        self.max_pages = int(max_pages)

    start_urls = [
        "https://www.law.go.kr/LSW/lsAstSc.do?menuId=391&subMenuId=397&tabMenuId=437&query=#AJAX"
    ]

    async def _route(self, route, request):
        rt, url = request.resource_type, request.url
        if rt in ("image", "font", "media"):
            return await route.abort()
        if any(h in url for h in (
                "googletagmanager.com", "google-analytics.com", "gstatic.com",
                "doubleclick.net", "stats.g.doubleclick.net"
        )):
            return await route.abort()
        return await route.continue_()

    def _wait_list_ready_js(self) -> str:
        return f"""
        () => {{
          const box = document.querySelector('{LIST_SEL}');
          if (!box) return false;
          return box.querySelectorAll('a[onclick*="lsReturnSearch"]').length > 0;
        }}
        """

    def start_requests(self):
        yield scrapy.Request(
            self.start_urls[0],
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("route", "**/*", self._route),
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                    PageMethod("wait_for_function", _wait_mask_clear_js(), timeout=60000),
                    PageMethod("wait_for_selector",
                               f"#cptOfi{self.dept_id}, a[onclick*=\"clickCptOfi({self.dept_id})\"]",
                               state="visible", timeout=60000),
                    PageMethod("evaluate", f"""
                        () => {{
                          const el = document.querySelector('#cptOfi{self.dept_id}')
                                   || document.querySelector('a[onclick*="clickCptOfi({self.dept_id})"]');
                          el?.scrollIntoView?.({{block:'center'}});
                          el?.click();
                        }}
                    """),
                    PageMethod("wait_for_function", _wait_mask_clear_js(), timeout=60000),
                    PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                    PageMethod("wait_for_selector", LIST_SEL, state="visible", timeout=30000),
                ],
            },
            callback=self.parse_list,
        )

    # -------- 목록 파싱 --------
    def parse_list(self, resp: scrapy.http.Response):
        def build_detail_url(oc: str, base: str):
            m = re.search(r"lsReturnSearch\((.*?)\)", oc or "")
            if not m:
                return None, None
            args = re.findall(r"'([^']*)'", m.group(1))
            efYd = next((x for x in args if re.fullmatch(r"\d{8}", x)), None)
            nums = [x for x in args if re.fullmatch(r"\d{5,}", x)]
            nums = [x for x in nums if x != efYd]
            lsiSeq = nums[-1] if nums else None
            if not lsiSeq:
                return None, None
            url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}"
            if efYd:
                url += f"&efYd={efYd}"
            return url, (lsiSeq, efYd)

        for a in resp.css(f"{LIST_SEL} a[onclick*='lsReturnSearch']"):
            url, keys = build_detail_url(a.attrib.get("onclick", ""), resp.url)
            if url and keys:
                yield scrapy.Request(
                    url,
                    dont_filter=True,
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("route", "**/*", self._route),
                            PageMethod("wait_for_load_state", "domcontentloaded"),
                            PageMethod(
                                "wait_for_function",
                                """
                                () => {
                                  const h2 = document.querySelector('#contentBody h2, #conScroll h2, h2');
                                  const b  = document.querySelector('#contentBody, #conScroll, #content');
                                  return h2 && (h2.textContent||'').trim().length >= 2
                                         && b && (b.innerText||'').trim().length > 50;
                                }
                                """,
                                timeout=60000
                            ),
                        ],
                        "keys": keys,  # (lsiSeq, efYd)
                    },
                    callback=self.parse_detail,
                )

        # 페이지네이션
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", resp.text)
        cur, last = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        if cur == 1 and last > 1:
            for p in range(2, min(last, self.max_pages) + 1):
                yield scrapy.Request(
                    resp.url,
                    dont_filter=True,
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("route", "**/*", self._route),
                            PageMethod("wait_for_selector", LIST_SEL, timeout=30000),
                            PageMethod("evaluate", f"pageSearch('lsListDiv','{p}')"),
                            PageMethod("wait_for_function", _wait_mask_clear_js(), timeout=60000),
                            PageMethod("wait_for_load_state", "networkidle"),
                            PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                        ],
                    },
                    callback=self.parse_list,
                )

    # -------- 상세 파싱 → JSONL 라인(조/항 단위) --------
    def parse_detail(self, resp: scrapy.http.Response):
        sel = resp.selector
        lsiSeq, efYd = resp.meta.get("keys", (None, None))

        title = _clean_inline(sel.css("#contentBody h2::text, #conScroll h2::text, h2::text").get("") or "")
        subtitle = _clean_inline(" ".join(sel.css(".ct_sub ::text").getall()))

        # 본문 블록 순회
        body = sel.css("#contentBody, #conScroll.scr_area, #content")
        blocks = []
        for n in body.xpath(".//p|.//div|.//li|.//table|.//blockquote"):
            t = _node_text(n)
            if t:
                blocks.append(t)

        re_chapter = re.compile(r"^제\s*(\d+)\s*장\b\s*(.*)")
        re_article = re.compile(r"^제\s*(\d+)\s*조\b\s*[（(]?([^)）]*)[)）]?\s*$")

        cur_ch_no, cur_ch_title = None, None
        cur_art_no, cur_art_title = None, None
        cur_art_buf = []

        def flush_article():
            nonlocal cur_art_no, cur_art_title, cur_art_buf
            if cur_art_no is None:
                return
            full_text = _clean_block("\n\n".join(cur_art_buf).strip())
            # 항 분해
            for para_no, chunk in _split_hang(full_text):
                section_path = []
                if cur_ch_no is not None:
                    section_path.append(f"제{cur_ch_no}장 {cur_ch_title or ''}".strip())
                section_path.append(f"제{cur_art_no}조({cur_art_title})" if cur_art_title else f"제{cur_art_no}조")
                if para_no:
                    section_path.append(f"{para_no if para_no<=20 else para_no}항")  # 표시용
                # ID 조립
                ch_key = f"ch{cur_ch_no}" if cur_ch_no is not None else "ch0"
                para_key = f"para{para_no}" if para_no is not None else "para0"
                doc_id = f"law:{lsiSeq or 'NA'}:{efYd or 'NA'}:{ch_key}:art{cur_art_no}:{para_key}"

                yield {
                    "id": doc_id,
                    "text": chunk,
                    "law_title": title,
                    "chapter_no": cur_ch_no,
                    "chapter_title": cur_ch_title,
                    "article_no": cur_art_no,
                    "article_title": cur_art_title,
                    "paragraph_no": para_no,
                    "item_no": None,
                    "section_path": " > ".join(section_path),
                    "lsiSeq": lsiSeq,
                    "efYd": efYd,
                    "subtitle_raw": subtitle,
                    "source_url": resp.url,
                    "dept_id": self.dept_id,
                    "lang": "ko",
                    "source": "국가법령정보센터",
                }

            # 다음 조문을 위해 초기화
            cur_art_no, cur_art_title, cur_art_buf = None, None, []

        for line in blocks:
            # 장?
            mch = re_chapter.match(line)
            if mch:
                # 이전 조 flush
                for item in flush_article():
                    yield item
                cur_ch_no = int(mch.group(1))
                cur_ch_title = _clean_inline(mch.group(2) or "")
                continue

            # 조?
            mar = re_article.match(line)
            if mar:
                # 이전 조 flush
                for item in flush_article():
                    yield item
                cur_art_no = int(mar.group(1))
                cur_art_title = _clean_inline(mar.group(2) or "")
                cur_art_buf = []
                continue

            # 본문 누적
            if cur_art_no is not None:
                cur_art_buf.append(line)
            else:
                # 조문 시작 전 프롤로그 텍스트 → ch0/art0로 저장(선택)
                # 필요 없으면 주석 처리
                if line.strip():
                    yield {
                        "id": f"law:{lsiSeq or 'NA'}:{efYd or 'NA'}:ch0:art0:para0",
                        "text": line.strip(),
                        "law_title": title,
                        "chapter_no": None,
                        "chapter_title": None,
                        "article_no": 0,
                        "article_title": "서문/총설",
                        "paragraph_no": None,
                        "item_no": None,
                        "section_path": "서문",
                        "lsiSeq": lsiSeq,
                        "efYd": efYd,
                        "subtitle_raw": subtitle,
                        "source_url": resp.url,
                        "dept_id": self.dept_id,
                        "lang": "ko",
                        "source": "국가법령정보센터",
                    }

        # 마지막 조 flush
        for item in flush_article():
            yield item
