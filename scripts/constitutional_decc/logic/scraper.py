"""
constitutional_decc 상세 페이지 크롤러
requests + BeautifulSoup (JS 렌더링 불필요)

수집·저장 구현은 `scripts.core.section_document_scraper` 에 있고, 이 모듈은 헌재결정례만의 설정만 둔다.
"""
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.core import section_document_scraper as shared
from scripts.utils.logger_config import get_logger
logger = get_logger(__name__, scraper_type='constitutional_decc')

# 공백 정리는 세 컬렉션이 같다(다른 모듈·도구가 이 이름으로 가져다 쓴다).
clean_spaces = shared.clean_spaces

SECTION_KEY_MAP = {
    "판시사항": "issue",
    "결정요지": "summary",
    "심판대상조문": "subject_provisions",
    "참조조문": "references",
    "참조판례": "ref_cases",
    "전문": "full_text",
    "당사자": "parties",
    "주문": "order",
    "이유": "reasoning",
    "청구취지": "claim_summary",
    "청구인": "claimant",
    "피청구인": "respondent",
}

SPEC = shared.SectionScraperSpec(
    collection="constitutional_decc",
    id_param="detcSeq",
    section_keys=SECTION_KEY_MAP,
    logger=logger,
    missing_subtitle=("헌재결정례 부제 추출 실패", "Constitutional decision subtitle extraction failed"),
)


_extract_sub_title = shared.extract_sub_title


def get_doc_id_from_url(url: str) -> str | None:
    return shared.get_doc_id_from_url(url, SPEC.id_param)


def parse_content(html: str, doc_id: str, url: str, doc_title: str) -> list:
    return shared.parse_content(SPEC, html, doc_id, url, doc_title)


def save_to_mongodb(doc_id: str, doc_title: str, doc_subtitle: str, url: str, chunks: list) -> bool:
    return shared.save_to_mongodb(SPEC, doc_id, doc_title, doc_subtitle, url, chunks)


async def scrape_and_save(url, output_dir: str = "", output_name: str = "",
                          process_chunks: bool = True, save_to_db: bool = True,
                          dept_code: str = None, save_jsonl: bool = True):
    # 모듈 안의 parse_content / save_to_mongodb 를 넘겨, 바꿔 끼운 함수(테스트 등)도 그대로 쓰이게 한다.
    return await shared.scrape_and_save(SPEC, url, save_to_db=save_to_db,
                                        parse=parse_content, save=save_to_mongodb)
