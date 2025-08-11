BOT_NAME = "roeum_crawler"

SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"

ROBOTSTXT_OBEY = True
AUTOTHROTTLE_ENABLED = True
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_TIMEOUT = 30

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": "RoeumCrawler/1.0 (+https://roeum.kr)"
}
