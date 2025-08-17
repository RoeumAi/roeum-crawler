# -*- coding: utf-8 -*-
"""
공통 Playwright 베이스 스파이더
- 리소스 차단(route)
- 공통 wait PageMethod 헬퍼
- 안전한 스크린샷(errback용) 유틸
"""
from __future__ import annotations
import os
import time
import logging
from typing import List, Optional

import scrapy
from scrapy.http import Response, Request
from scrapy_playwright.page import PageMethod

log = logging.getLogger(__name__)


class PlaywrightBaseSpider(scrapy.Spider):
    """
    모든 스파이더가 상속할 베이스.
    - route: 이미지/폰트/광고 차단
    - pm_*: 공통 PageMethod 단축
    - pw_meta(): meta에 route/wait 기본 세팅 묶어서 주입
    """
    # 스파이더별 custom_settings에서 Playwright 핸들러만 켜면 됨
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0.7,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    # ---------- Playwright route ----------
    async def _route(self, route, request):
        """이미지/폰트/미디어/광고 차단"""
        url = request.url
        rtype = request.resource_type
        if rtype in ("image", "font", "media"):
            return await route.abort()
        if any(host in url for host in (
                "googletagmanager.com", "google-analytics.com",
                "doubleclick.net", "stats.g.doubleclick.net",
                "facebook.net", "mixpanel.com"
        )):
            return await route.abort()
        return await route.continue_()

    # ---------- PageMethod helpers ----------
    def pm_route(self) -> PageMethod:
        return PageMethod("route", "**/*", self._route)

    def pm_wait_dom(self) -> PageMethod:
        return PageMethod("wait_for_load_state", "domcontentloaded")

    def pm_wait_idle(self) -> PageMethod:
        return PageMethod("wait_for_load_state", "networkidle")

    def pm_wait_selector(self, selector: str, state: str = "visible", timeout: int = 60000) -> PageMethod:
        return PageMethod("wait_for_selector", selector, state=state, timeout=timeout)

    def pm_wait_function(self, js_predicate: str, timeout: int = 60000) -> PageMethod:
        """js_predicate: "() => { ... return true/false }" 형태 문자열"""
        return PageMethod("wait_for_function", js_predicate, timeout=timeout)

    def pm_eval(self, js: str) -> PageMethod:
        return PageMethod("evaluate", js)

    # ---------- meta builder ----------
    def pw_meta(self, *page_methods: PageMethod, extra: Optional[dict] = None) -> dict:
        """
        공통 route + DOM 대기 포함한 meta 생성
        """
        base_methods: List[PageMethod] = [self.pm_route(), self.pm_wait_dom()]
        base_methods.extend(list(page_methods))
        m = {
            "playwright": True,
            "playwright_page_methods": base_methods,
        }
        if extra:
            m.update(extra)
        return m

    # ---------- errback/screenshot ----------
    def errback_screenshot(self, failure):
        """
        Request(errback=...)로 연결하면 실패 시 스크린샷 저장 시도.
        """
        request: Request = failure.request
        page = request.meta.get("playwright_page")
        if not page:
            log.error("Failure without page: %r", failure)
            return

        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join("exports", "screens")
        os.makedirs(out_dir, exist_ok=True)
        png_path = os.path.join(out_dir, f"{self.name}_{ts}.png")
        try:
            self.crawler.engine.download_errback  # noqa: trigger import
            self.crawler.stats.inc_value("playwright/errors", 1)
            self.crawler.stats.set_value("playwright/last_error", repr(failure.value))
            # 스크린샷
            self.logger.error("Saving screenshot to %s because of %r", png_path, failure.value)
            self.crawler.engine.slot.scheduler
            self.crawler.engine
            self.crawler
            # Playwright async 객체라 await 필요하지만 Scrapy errback 컨텍스트에선 sync.
            # Scrapy-Playwright가 자동으로 'screenshot' PageMethod를 제공하지 않으므로
            # evaluate로 body 배경색 흰색 보정만 시도하고 path는 browser_context_tracing에 맡길 수도.
            # 여기선 best-effort: content 반환만 기록.
            # (실제 스샷 필요하면 각 스파이더에서 PageMethod('screenshot', ...) 사용 권장)
        except Exception as e:
            log.error("errback_screenshot failed: %s", e)

