# crawler/spiders/roeum_cases.py
import re
from urllib.parse import urljoin

import scrapy
from scrapy_playwright.page import PageMethod

LIST_SEL = "#resultTableDiv"
CASE_READY_SEL = 'h2[data-brl-use="PH/H1"]'

def _clean_inline(s: str) -> str:
    """한 줄 텍스트용: NBSP/nbsp 제거 + 공백 정돈"""
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)       # 'NBSP' 글자 제거
    s = re.sub(r"(?i)&nbsp;", " ", s)         # HTML 엔티티 제거
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _clean_block(s: str) -> str:
    """여러 줄 텍스트용: NBSP/nbsp 제거 + 줄바꿈 보존"""
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    # 줄바꿈은 살리고, 줄 내부 공백 정리
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _node_text(sel) -> str:
    """블록 내부 텍스트를 줄바꿈 정리해 수집"""
    html = sel.get() or ""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    parts = [t for t in sel.xpath(".//text()").getall()]
    txt = "\n".join([p for p in (p.strip() for p in parts) if p])
    txt = _clean_block(txt)
    return txt

def _collect_until(nodes, stop_if):
    chunks = []
    for n in nodes:
        tag = getattr(n.root, "tag", "").lower()
        if stop_if(n, tag):
            break
        if tag in {"p", "div", "ul", "ol", "table", "blockquote"}:
            t = _node_text(n)
            if t:
                chunks.append(t)
    return "\n\n".join(chunks).strip()


class RoeumCasesSpider(scrapy.Spider):
    name = "roeum_cases"
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
        "https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&query=#AJAX"
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

    def _wait_mask_clear_js(self) -> str:
        return """
            () => {
              const nodes = Array.from(document.querySelectorAll('.loadmask, .loadmask-msg'));
              return !nodes.some(n => {
                const s = window.getComputedStyle(n);
                return s && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
              });
            }
        """

    def _wait_list_ready_js(self) -> str:
        return f"""
            () => {{
              const box = document.querySelector('{LIST_SEL}');
              if (!box) return false;
              return box.querySelectorAll('a[href*="precInfoP.do"]').length > 0
                  || box.querySelectorAll('a[onclick*="precInfoP.do"]').length > 0;
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
                    PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
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
                    PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
                    PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                    PageMethod("wait_for_selector", LIST_SEL, state="visible", timeout=30000),
                ],
            },
            callback=self.parse_list,
        )

    def parse_list(self, resp: scrapy.http.Response):
        links = set()

        for h in resp.css(f'{LIST_SEL} a[href*="/LSW/precInfoP.do"]::attr(href)').getall():
            links.add(urljoin(resp.url, h))

        for a in resp.css(f"{LIST_SEL} a[onclick]"):
            oc = a.attrib.get("onclick", "")
            m = re.search(r"(precInfoP\.do\?[^'\"()]+)", oc)
            if m:
                links.add(urljoin(resp.url, "/LSW/" + m.group(1)))

        for url in links:
            yield scrapy.Request(
                url,
                dont_filter=True,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("route", "**/*", self._route),
                        PageMethod("wait_for_load_state", "domcontentloaded"),
                        PageMethod("wait_for_selector", CASE_READY_SEL, timeout=60000),
                        PageMethod("wait_for_function", """
                            () => {
                              const el = document.querySelector('#contentBody') || document.querySelector('#content');
                              if (!el) return false;
                              const t = (el.innerText || '').trim();
                              return t.length > 100;
                            }
                        """, timeout=60000),
                    ],
                },
                callback=self.parse_detail,
            )

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
                            PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
                            PageMethod("wait_for_load_state", "networkidle"),
                            PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                        ],
                    },
                    callback=self.parse_list,
                )

    def parse_detail(self, resp: scrapy.http.Response):
        sel = resp.selector

        title = _clean_inline(sel.css('h2[data-brl-use="PH/H1"]::text').get("") or "")
        subtitle1 = _clean_inline(" ".join(sel.css("div.subtit1 ::text").getall()))

        def section_text(h4_label: str) -> str:
            h = sel.xpath(f'//h4[contains(@data-brl-use,"PH/H2")][contains(normalize-space(.),"{h4_label}")]')
            if not h:
                return ""
            start = h[0]
            siblings = start.xpath("following-sibling::*")

            def stopper(node, tag):
                return tag == "h4" and "PH/H2" in "".join(node.xpath("@data-brl-use").getall())

            return _collect_until(siblings, stopper)

        pansisahang = section_text("판시사항")
        pangyeolyoji = section_text("판결요지")
        chamjojoson = section_text("참조조문")

        jumun, iyu = "", ""
        jun = sel.xpath('//h4[contains(@data-brl-use,"PH/H2")][contains(normalize-space(.),"전문")]')
        if jun:
            sibs = jun[0].xpath("following-sibling::*")

            # 주문
            jumun_h = None
            for n in sibs:
                tag = getattr(n.root, "tag", "").lower()
                text = n.xpath("normalize-space(string(.))").get("") or ""
                if tag == "h5" and "주문" in text:
                    jumun_h = n
                    break
                if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                    break
            if jumun_h is not None:
                after = jumun_h.xpath("following-sibling::*")

                def stop_h5_or_h4(n, tag):
                    if tag == "h5":
                        return True
                    if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                        return True
                    return False

                jumun = _collect_until(after, stop_h5_or_h4)

            # 이유
            iyu_h = sel.xpath('//h5[contains(normalize-space(.),"이유")]')
            if iyu_h:
                after2 = iyu_h[0].xpath("following-sibling::*")

                def stop_h5_or_h4_2(n, tag):
                    if tag == "h5":
                        return True
                    if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                        return True
                    return False

                iyu = _collect_until(after2, stop_h5_or_h4_2)

        yield {
            "source_url": resp.url,
            "title": title,
            "subtitle1": subtitle1,   # ← 필드명 교체
            "sections": {
                "판시사항": pansisahang,
                "판결요지": pangyeolyoji,
                "참조조문": chamjojoson,
                "전문": {
                    "주문": jumun,
                    "이유": iyu,
                },
            },
        }
