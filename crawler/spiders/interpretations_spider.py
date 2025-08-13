import scrapy

# 중앙부처 1차 해석 크롤링을 위한 스파이더
class InterpretationsSpider(scrapy.Spider):
    # 1. 스파이더의 고유 이름입니다.
    # 'scrapy crawl interpretations' 명령어로 이 스파이더를 실행합니다.
    name = "interpretations"

    # 2. 크롤링을 시작할 URL 목록입니다.
    # 여기에 중앙부처 법령해석 사이트의 시작 URL을 추가합니다.
    start_urls = ["https://www.moleg.go.kr/lawinfo/lawAnalysis/list.do"] # 예시 URL입니다. 실제 사이트에 맞게 변경해야 합니다.

    # 3. start_urls의 각 URL에 대한 요청(request)이 완료된 후 호출되는 메서드입니다.
    # 응답(response)을 파싱하여 데이터를 추출하거나 추가적인 요청을 생성합니다.
    def parse(self, response):
        # 예시: 페이지의 제목을 로그로 출력
        self.log(f"Visited {response.url}")
        self.log(f"Page title: {response.css('title::text').get()}")

        # 여기에 실제 데이터 추출 로직을 구현합니다.
        # 예: 법령해석 목록을 찾아서 각 항목의 상세 페이지로 넘어가는 링크를 추출
        # for link in response.css('a.interpretation_link::attr(href)').getall():
        #     yield response.follow(link, self.parse_interpretation_detail)
        pass

    # 4. (선택사항) 상세 페이지를 파싱하는 별도의 콜백 메서드를 만들 수 있습니다.
    # def parse_interpretation_detail(self, response):
    #     # 법령해석의 상세 정보를 추출하는 로직
    #     item = {}
    #     item['title'] = response.css('h1.title::text').get()
    #     item['content'] = response.css('div.content').get()
    #     yield item
