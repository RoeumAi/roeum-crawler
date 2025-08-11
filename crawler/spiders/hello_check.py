import scrapy
class HelloCheckSpider(scrapy.Spider):
    name = "hello_check"
    start_urls = ["https://quotes.toscrape.com/"]
    def parse(self, resp):
        for q in resp.css(".quote"):
            yield {"text": q.css(".text::text").get(),
                   "author": q.css(".author::text").get()}
