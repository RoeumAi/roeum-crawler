# crawler/spiders/roeum_cases.py
import re
from urllib.parse import urljoin

import scrapy
from scrapy_playwright.page import PageMethod

# 목록/상세 컨테이너 셀렉터
LIST_SEL = "#resultTableDiv"
DETAIL_SEL = "#conScroll, #content"


def _clean(s: str) -> str:
    """공백 정리"""
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


class RoeumCasesSpider(scrapy.Spider):
    name = "roeum_cases"

    # 스파이더 전용 셋팅
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, dept: str = "1492000", max_pages: int = 3, *args, **kwargs):
        """
        :param dept: 좌측 트리 부처 ID (기본: 고용노동부 1492000)
        :param max_pages: 목록 페이지 최대 크롤 수
        """
        super().__init__(*args, **kwargs)
        self.dept_id = str(dept)
        self.max_pages = int(max_pages)

    start_urls = [
        "https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&query=#AJAX"
    ]

    # 불필요 리소스 차단(속도/차단 이슈 완화)
    async def _route(self, route, request):
        rt, url = request.resource_type, request.url
        if rt in ("image", "font", "media"):
            return await route.abort()
        if any(
                h in url
                for h in (
                        "googletagmanager.com",
                        "google-analytics.com",
                        "gstatic.com",
                        "doubleclick.net",
                        "stats.g.doubleclick.net",
                )
        ):
            return await route.abort()
        return await route.continue_()

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("route", "**/*", self._route),
                        PageMethod("wait_for_load_state", "domcontentloaded"),
                        # 1) '소관부처별' 탭을 DOM 클릭(직접 showDiv 호출 금지)
                        PageMethod(
                            "evaluate",
                            """
                            () => { try {
                              const tab = [...document.querySelectorAll('a[onclick*="showDiv"]')]
                                .find(a => a.getAttribute('onclick')?.includes("'cptOfi'"));
                              tab?.click();
                            } catch(e){} }
                            """,
                        ),
                        PageMethod("wait_for_load_state", "networkidle"),
                        # 2) 고용노동부(또는 지정 dept) 노드 나타날 때까지 대기
                        PageMethod(
                            "wait_for_function",
                            f"""
                            () => document.querySelector('#cptOfi{self.dept_id}')
                               || [...document.querySelectorAll('a[onclick*="clickCptOfi"]')]
                                    .some(a => a.getAttribute('onclick')?.includes("{self.dept_id}"))
                            """,
                            timeout=30000,
                        ),
                        # 3) 해당 부처 앵커 클릭(id 우선, 없으면 onclick 포함 a)
                        PageMethod(
                            "evaluate",
                            f"""
                            () => {{ try {{
                              const el = document.querySelector('#cptOfi{self.dept_id}')
                                || [...document.querySelectorAll('a[onclick*="clickCptOfi"]')]
                                    .find(a => a.getAttribute('onclick')?.includes("{self.dept_id}"));
                              el?.scrollIntoView?.();
                              el?.click();
                            }} catch(e){{}} }}
                            """,
                        ),
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_selector", LIST_SEL),
                    ],
                },
                callback=self.parse_list,
            )

    def parse_list(self, resp: scrapy.http.Response):
        """필터된 목록에서 상세 링크 수집 + 페이지네이션"""
        links = set()

        # 1) a href 직접 링크
        hrefs = resp.css(
            f'{LIST_SEL} a[href*="/LSW/lsInfoP.do?lsiSeq="]::attr(href)'
        ).getall()
        for h in hrefs:
            links.add(urljoin(resp.url, h))

        # 2) onclick에서 상세 링크 추출(openDetail('/LSW/lsInfoP.do?lsiSeq=xxxx'))
        for a in resp.css(f"{LIST_SEL} a[onclick]"):
            oc = a.attrib.get("onclick", "")
            m = re.search(r"(?:/LSW/)?lsInfoP\.do\?lsiSeq[=+]'?(\d+)", oc)
            if m:
                links.add(urljoin(resp.url, f"/LSW/lsInfoP.do?lsiSeq={m.group(1)}"))

        # 상세 요청
        for url in links:
            yield scrapy.Request(
                url,
                dont_filter=True,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("route", "**/*", self._route),
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_selector", DETAIL_SEL),
                    ],
                },
                callback=self.parse_detail,
            )

        # 페이지네이션(pageSearch 호출) — 현재/전체 형태 텍스트에서 숫자 추출
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
                            PageMethod("wait_for_selector", LIST_SEL),
                            PageMethod(
                                "evaluate", f"pageSearch('lsListDiv','{p}')"
                            ),
                            PageMethod("wait_for_load_state", "networkidle"),
                            PageMethod("wait_for_selector", LIST_SEL),
                        ],
                    },
                    callback=self.parse_list,
                )

    def parse_detail(self, resp: scrapy.http.Response):
        """상세 페이지에서 타이틀/부서/조문 본문 추출(HTML 잡음 제거)"""
        # (1) 상단 타이틀·부가정보(약칭/시행/공포 등)
        title_main = " ".join(
            resp.css("#conTop .cont_tit *::text, #conTop h1::text, #conTop h2::text").getall()
        )
        title_sub = " ".join(
            resp.css("#conTop .ct_sub *::text, #conTop .cont_sub *::text").getall()
        )
        title_line = _clean(f"{title_main} {title_sub}")

        # (2) 소관부서(있으면)
        sub_block = " ".join(
            resp.css("#conScroll .cont_subtit *::text, #content .cont_subtit *::text").getall()
        )
        department = _clean(sub_block[sub_block.find("소관부서") :]) if "소관부서" in sub_block else ""

        # (3) 조문: .pgroup(1개 = 제N조 1개) — p 단위로 안전 추출
        articles = []
        for grp in resp.css(".pgroup"):
            lawcon = grp.css(".lawcon")
            if not lawcon:
                continue

            # 머리(제N조 (제목))
            heading = _clean(
                " ".join(lawcon.css("span.bl > label::text, label::text").getall())
            )
            if not heading:
                continue

            # 본문: 라벨 p(pty1_p4) 제외하고 모든 p의 텍스트 합치기
            paras = []
            for p in lawcon.css("p"):
                cls = p.attrib.get("class", "")
                if "pty1_p4" in cls:  # 라벨/체크박스 줄
                    continue
                t = " ".join(p.css("::text, *::text").getall()).strip()
                if t:
                    paras.append(_clean(t))
            text = "\n".join(paras)

            m_no = re.search(r"제\s*(\d+)\s*조", heading)
            no = int(m_no.group(1)) if m_no else None
            articles.append({"no": no, "heading": heading, "text": text})

        # (4) 부칙
        bu = resp.css("#arDivArea .pgroup")
        if bu:
            bu_text = _clean(" ".join(bu.css(".pty3_dep1 ::text").getall()))
            if bu_text:
                articles.append({"no": None, "heading": "부칙", "text": bu_text})

        yield {
            "source_url": resp.url,
            "title_line": title_line,
            "department": department,
            "articles": articles,
        }
