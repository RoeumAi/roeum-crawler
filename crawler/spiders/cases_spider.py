import re
from urllib.parse import urljoin

import scrapy
from scrapy_playwright.page import PageMethod

# ==============================================================================
# 상수 정의 (Constants Definition)
# ==============================================================================
# 메인페이지 목록 테이블의 CSS 선택자
LIST_SEL = "#resultTableDiv"
# 판례 상세 페이지에서 내용이 준비되었는지 확인하는 CSS 선택자
CASE_READY_SEL = 'h2[data-brl-use="PH/H1"]'

# ==============================================================================
# 텍스트 클리닝 유틸리티 함수 (Text Cleaning Utility Functions)
# ==============================================================================

def _clean_inline(s: str) -> str:
    """
    한 줄 텍스트용: HTML 엔티티(NBSP, &nbsp;) 제거 및 공백 정돈.
    텍스트 내의 불필요한 공백을 단일 공백으로 줄이고 앞뒤 공백을 제거합니다.
    """
    s = (s or "").replace("\xa0", " ")  # 유니코드 NBSP 제거
    s = re.sub(r"(?i)\bNBSP\b", " ", s)       # 'NBSP' 문자열 제거 (대소문자 구분 없음)
    s = re.sub(r"(?i)&nbsp;", " ", s)         # HTML 엔티티 &nbsp; 제거 (대소문자 구분 없음)
    s = re.sub(r"\s+", " ", s).strip()      # 여러 공백을 단일 공백으로 줄이고 앞뒤 공백 제거
    return s

def _clean_block(s: str) -> str:
    """
    여러 줄 텍스트용: HTML 엔티티(NBSP, &nbsp;) 제거 및 줄바꿈 보존.
    텍스트 내의 불필요한 공백을 정리하되, 줄바꿈은 유지하고 여러 줄바꿈을 단일/이중 줄바꿈으로 줄입니다.
    """
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)\bNBSP\b", " ", s)
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)             # 줄 내부의 여러 공백을 단일 공백으로
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s) # 줄바꿈 앞뒤의 공백 제거
    s = re.sub(r"\n{3,}", "\n\n", s)        # 3개 이상의 연속된 줄바꿈을 2개로 줄임
    return s.strip()

def _node_text(sel) -> str:
    """
    Scrapy Selector 노드에서 텍스트를 추출하고 줄바꿈을 정리합니다.
    <br> 태그를 줄바꿈으로 변환하고, 모든 텍스트 노드를 결합하여 블록 텍스트를 생성합니다.
    """
    html = sel.get() or ""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html) # <br> 태그를 줄바꿈으로 변환
    parts = [t for t in sel.xpath(".//text()").getall()] # 모든 하위 텍스트 노드 추출
    txt = "\n".join([p for p in (p.strip() for p in parts) if p]) # 각 텍스트 노드를 줄바꿈으로 연결
    txt = _clean_block(txt) # 블록 텍스트 클리닝 적용
    return txt

def _collect_until(nodes, stop_if):
    """
    주어진 노드 리스트를 순회하며 특정 조건(`stop_if`)이 만족될 때까지 텍스트를 수집합니다.
    주로 섹션별 텍스트를 추출할 때 다음 섹션의 시작 태그를 만나면 중단하는 데 사용됩니다.
    """
    chunks = []
    for n in nodes:
        tag = getattr(n.root, "tag", "").lower()
        if stop_if(n, tag): # 중단 조건 확인
            break
        # 특정 HTML 태그(p, div, ul, ol, table, blockquote) 내의 텍스트만 수집
        if tag in {"p", "div", "ul", "ol", "table", "blockquote"}:
            t = _node_text(n)
            if t:
                chunks.append(t)
    return "\n\n".join(chunks).strip() # 수집된 텍스트 덩어리들을 두 개의 줄바꿈으로 연결

# ==============================================================================
# Scrapy 스파이더 클래스 (Scrapy Spider Class)
# ==============================================================================

class CasesSpider(scrapy.Spider):
    """
    대한민국 법원 판례 정보를 크롤링하는 Scrapy 스파이더.
    Playwright를 사용하여 동적 웹 페이지를 처리하고, 판례 목록 및 상세 내용을 추출합니다.
    """
    # 스파이더의 고유 이름. 'scrapy crawl cases' 명령어로 실행됩니다.
    name = "cases"

    # Scrapy의 사용자 정의 설정.
    # AUTOTHROTTLE_ENABLED: 요청 간 지연 시간을 자동으로 조절하여 서버 부하를 줄임.
    # CONCURRENT_REQUESTS_PER_DOMAIN: 도메인당 동시 요청 수.
    # DOWNLOAD_DELAY: 요청 간 최소 지연 시간.
    # FEED_EXPORT_ENCODING: 피드(Feed) 내보내기 시 사용할 인코딩.
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, dept: str = "1492000", max_pages: int = 3, *args, **kwargs):
        """
        스파이더 초기화 메서드.
        dept: 크롤링할 부서 ID (기본값: 1492000).
        max_pages: 크롤링할 최대 페이지 수 (기본값: 3).
        """
        super().__init__(*args, **kwargs)
        self.dept_id = str(dept)
        self.max_pages = int(max_pages)

    # 크롤링을 시작할 초기 URL 목록.
    # 이 URL은 Playwright를 통해 동적으로 로드될 페이지의 시작점입니다.
    start_urls = [
        "https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&query=#AJAX"
    ]

    # ==============================================================================
    # Playwright 관련 헬퍼 메서드 (Playwright Helper Methods)
    # ==============================================================================

    async def _route(self, route, request):
        """
        Playwright 요청 라우팅: 불필요한 리소스(이미지, 폰트, 미디어, 광고/분석 스크립트) 로드를 차단합니다.
        이를 통해 크롤링 속도를 높이고 리소스 사용량을 줄입니다.
        """
        rt, url = request.resource_type, request.url
        # 이미지, 폰트, 미디어 파일 차단
        if rt in ("image", "font", "media"):
            return await route.abort()
        # 특정 도메인의 스크립트 차단 (광고, 분석 등)
        if any(h in url for h in (
                "googletagmanager.com", "google-analytics.com", "gstatic.com",
                "doubleclick.net", "stats.g.doubleclick.net"
        )):
            return await route.abort()
        return await route.continue_() # 나머지 요청은 계속 진행

    def _wait_mask_clear_js(self) -> str:
        """
        페이지 로딩 마스크(loadmask)가 사라질 때까지 기다리는 JavaScript 함수를 반환합니다.
        동적 콘텐츠 로딩 시 화면을 가리는 마스크가 사라진 후 다음 작업을 진행하기 위해 사용됩니다.
        """
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
        """
        판례 목록이 로드되어 준비될 때까지 기다리는 JavaScript 함수를 반환합니다.
        목록 테이블 내에 특정 링크(precInfoP.do)가 나타나는지 확인하여 목록 로딩 완료를 판단합니다.
        """
        return f"""
            () => {{
              const box = document.querySelector('{LIST_SEL}');
              if (!box) return false;
              return box.querySelectorAll('a[href*="precInfoP.do"]').length > 0
                  || box.querySelectorAll('a[onclick*="precInfoP.do"]').length > 0;
            }}
        """

    # ==============================================================================
    # 요청 시작 및 처리 (Request Initiation and Processing)
    # ==============================================================================

    def start_requests(self):
        """
        스파이더의 초기 요청을 생성합니다.
        Playwright를 사용하여 초기 URL에 접속하고, 특정 부서(dept_id)를 선택하는 등
        페이지와 상호작용하여 판례 목록을 로드합니다.
        """
        yield scrapy.Request(
            self.start_urls[0],
            meta={
                "playwright": True, # Playwright 사용 활성화
                "playwright_page_methods": [
                    # 요청 라우팅 설정 (불필요한 리소스 차단)
                    PageMethod("route", "**/*", self._route),
                    # DOM 콘텐츠가 로드될 때까지 대기
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                    # 로딩 마스크가 사라질 때까지 대기
                    PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
                    # 특정 부서 선택 요소가 보일 때까지 대기
                    PageMethod("wait_for_selector",
                               f"#cptOfi{self.dept_id}, a[onclick*=\"clickCptOfi({self.dept_id})\"]",
                               state="visible", timeout=60000),
                    # JavaScript를 실행하여 해당 부서 요소를 스크롤하고 클릭
                    PageMethod("evaluate", f"""
                        () => {{
                          const el = document.querySelector('#cptOfi{self.dept_id}')
                                   || document.querySelector('a[onclick*="clickCptOfi({self.dept_id})"]');
                          el?.scrollIntoView?.({{block:'center'}});
                          el?.click();
                        }}
                    """
                    ),
                    # 부서 선택 후 로딩 마스크가 다시 사라질 때까지 대기
                    PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
                    # 판례 목록이 로드되어 준비될 때까지 대기
                    PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                    # 목록 테이블 요소가 보일 때까지 대기
                    PageMethod("wait_for_selector", LIST_SEL, state="visible", timeout=30000),
                ],
            },
            callback=self.parse_list, # 페이지 로드 후 parse_list 메서드 호출
        )

    def parse_list(self, resp: scrapy.http.Response):
        """
        판례 목록 페이지를 파싱하여 각 판례의 상세 페이지 링크를 추출하고,
        추가 페이지가 있다면 다음 페이지로 이동하는 요청을 생성합니다.
        """
        links = set() # 중복 링크 방지를 위한 set

        # 1. href 속성을 통해 링크 추출
        for h in resp.css(f'{LIST_SEL} a[href*="/LSW/precInfoP.do"]::attr(href)').getall():
            links.add(urljoin(resp.url, h)) # 절대 URL로 변환하여 추가

        # 2. onclick 속성을 통해 링크 추출 (JavaScript로 이동하는 경우)
        for a in resp.css(f"{LIST_SEL} a[onclick]"):
            oc = a.attrib.get("onclick", "")
            m = re.search(r"(precInfoP\.do\?[^'\"()]+)", oc) # onclick 내의 URL 패턴 검색
            if m:
                links.add(urljoin(resp.url, "/LSW/" + m.group(1))) # 절대 URL로 변환하여 추가

        # 추출된 각 상세 페이지 링크에 대해 Request 생성
        for url in links:
            yield scrapy.Request(
                url,
                dont_filter=True, # 중복 URL 필터링 비활성화 (필요에 따라)
                meta={
                    "playwright": True, # Playwright 사용 활성화
                    "playwright_page_methods": [
                        PageMethod("route", "**/*", self._route), # 리소스 라우팅
                        PageMethod("wait_for_load_state", "domcontentloaded"), # DOM 로드 대기
                        PageMethod("wait_for_selector", CASE_READY_SEL, timeout=60000), # 상세 내용 준비 대기
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
                callback=self.parse_detail, # 상세 페이지 로드 후 parse_detail 메서드 호출
            )

        # 페이지네이션 처리: 다음 페이지로 이동하는 요청 생성
        # 현재 페이지 번호와 총 페이지 번호 추출
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", resp.text)
        cur, last = (int(m.group(1)), int(m.group(2))) if m else (1, 1)

        # 현재 페이지가 첫 페이지이고, 총 페이지가 1보다 많으며, 최대 페이지 수 제한 내에 있을 경우
        if cur == 1 and last > 1:
            # 2페이지부터 max_pages까지 반복하여 다음 페이지 요청 생성
            for p in range(2, min(last, self.max_pages) + 1):
                yield scrapy.Request(
                    resp.url,
                    dont_filter=True,
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod("route", "**/*", self._route),
                            PageMethod("wait_for_selector", LIST_SEL, timeout=30000),
                            # JavaScript 함수 pageSearch를 호출하여 특정 페이지로 이동
                            PageMethod("evaluate", f"pageSearch('lsListDiv','{p}')"),
                            PageMethod("wait_for_function", self._wait_mask_clear_js(), timeout=60000),
                            PageMethod("wait_for_load_state", "networkidle"), # 네트워크 활동이 없을 때까지 대기
                            PageMethod("wait_for_function", self._wait_list_ready_js(), timeout=60000),
                        ],
                    },
                    callback=self.parse_list, # 다음 페이지 로드 후 다시 parse_list 호출 (재귀)
                )

    def parse_detail(self, resp: scrapy.http.Response):
        """
        개별 판례 상세 페이지를 파싱하여 필요한 정보를 추출하고 Item 객체로 반환합니다.
        """
        sel = resp.selector # 응답의 Scrapy Selector 객체

        # 제목 및 부제목 추출
        title = _clean_inline(sel.css('h2[data-brl-use="PH/H1"]::text').get("") or "")
        subtitle1 = _clean_inline(" ".join(sel.css("div.subtit1 ::text").getall()))

        def section_text(h4_label: str) -> str:
            """
            특정 h4 제목(예: '판시사항') 아래의 텍스트 섹션을 추출합니다.
            다음 h4 제목이 나타날 때까지의 모든 관련 텍스트를 수집합니다.
            """
            # h4 태그를 찾아 해당 섹션의 시작점 식별
            h = sel.xpath(f'//h4[contains(@data-brl-use,"PH/H2")][contains(normalize-space(.),"{h4_label}")]')
            if not h:
                return ""
            start = h[0]
            siblings = start.xpath("following-sibling::*") # 시작 h4 태그 이후의 모든 형제 노드

            def stopper(node, tag):
                # 다음 h4 태그(새로운 섹션의 시작)를 만나면 텍스트 수집 중단
                return tag == "h4" and "PH/H2" in "".join(node.xpath("@data-brl-use").getall())

            return _collect_until(siblings, stopper) # _collect_until 함수를 사용하여 텍스트 수집

        # 각 섹션별 텍스트 추출
        pansisahang = section_text("판시사항")
        pangyeolyoji = section_text("판결요지")
        chamjojoson = section_text("참조조문")

        jumun, iyu = "", ""
        # '전문' 섹션 추출 (주문 및 이유 포함)
        jun = sel.xpath('//h4[contains(@data-brl-use,"PH/H2")][contains(normalize-space(.),"전문")]')
        if jun:
            sibs = jun[0].xpath("following-sibling::*")

            # '주문' 섹션 추출
            jumun_h = None
            for n in sibs:
                tag = getattr(n.root, "tag", "").lower()
                text = n.xpath("normalize-space(string(.))" ).get("") or ""
                if tag == "h5" and "주문" in text: # '주문' h5 태그를 찾음
                    jumun_h = n
                    break
                if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                    break # 다음 h4 태그를 만나면 중단

            if jumun_h is not None:
                after = jumun_h.xpath("following-sibling::*")

                def stop_h5_or_h4(n, tag):
                    # 다음 h5 또는 h4 태그를 만나면 중단
                    if tag == "h5":
                        return True
                    if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                        return True
                    return False

                jumun = _collect_until(after, stop_h5_or_h4)

            # '이유' 섹션 추출
            iyu_h = sel.xpath('//h5[contains(normalize-space(.),"이유")]')
            if iyu_h:
                after2 = iyu_h[0].xpath("following-sibling::*")

                def stop_h5_or_h4_2(n, tag):
                    # 다음 h5 또는 h4 태그를 만나면 중단
                    if tag == "h5":
                        return True
                    if tag == "h4" and "PH/H2" in "".join(n.xpath("@data-brl-use").getall()):
                        return True
                    return False

                iyu = _collect_until(after2, stop_h5_or_h4_2)

        # 추출된 데이터를 딕셔너리 형태로 반환 (Scrapy Item으로 자동 변환됨)
        yield {
            "source_url": resp.url, # 원본 URL
            "title": title,         # 판례 제목
            "subtitle1": subtitle1, # 부제목
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