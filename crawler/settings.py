BOT_NAME = "roeum_crawler"

SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"

ROBOTSTXT_OBEY = False
AUTOTHROTTLE_ENABLED = True
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_DELAY = 1.0

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": "RoeumCrawler/1.0 (+https://roeum.kr)"
}

# ★ JS 렌더
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 60000
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": False
}
DUPEFILTER_CLASS = "scrapy.dupefilters.RFPDupeFilter"

# ★ 출력 인코딩 (JSON이 한글 그대로 나오게)
FEED_EXPORT_ENCODING = "utf-8"


ITEM_PIPELINES = {
    "crawler.pipelines.EmbeddingQueuePipeline": 300,
}

# DB 접속
# export DB_DSN="dbname=roeum user=roeum password=roeum host=127.0.0.1"