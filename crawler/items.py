import scrapy

class LawItem(scrapy.Item):
    source_url  = scrapy.Field()
    title_line  = scrapy.Field()
    department  = scrapy.Field()
    articles    = scrapy.Field()  # [{"no": int|None, "heading": str, "text": str}, ...]
