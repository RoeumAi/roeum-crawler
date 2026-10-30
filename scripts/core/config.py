"""
중앙 설정 파일

모든 scraper의 공통 설정을 정의합니다.
개별 scraper 설정은 각 폴더의 config.py에서 정의합니다.
"""

import os
from dataclasses import dataclass
from typing import Optional

# 국가법령 Open API 식별자(비밀 아님). 정의는 여기 한 곳이고 운영은 LAWGO_OC 환경변수로 덮어쓴다.
LAWGO_OC = os.getenv("LAWGO_OC", "inwoong100")

# ============================================================================
# SCRAPER 설정 정의
# ============================================================================

UPDATE_BY_EFFECTIVE_DATE = "effective_date"   # 시행일자가 바뀐 문서만 다시 수집 (법령·행정규칙)
UPDATE_BY_DOC_ID = "doc_id"                   # DB 에 doc_id 가 있으면 건너뜀

# 판단은 현재(active) 레코드로만 한다. 같은 doc_id 의 이전 버전(inactive)에는 재시도 표시가 없어서,
# 레코드 단위로 거르면 이전 버전 때문에 doc_id 전체가 "완료"로 보여 재시도가 멈춘다(조정사건례 34·41).
PDF_RETRY_FILTER = {"metadata.is_active": True, "metadata.pdf_retry_needed": True}
# 본문을 못 읽어 제목·출처만 남긴 문서(메타데이터 청크)는 매일 다시 확인한다.
# 원문에 정말 본문이 없는 문서(헌재 지정재판부 각하 등)도 있지만 요청 한 번이라 싸고,
# 그때 파싱만 실패했던 문서(판례 618235)는 다음 실행에서 채워진다.
METADATA_ONLY_FILTER = {"metadata.is_active": True, "metadata.is_metadata_only": True}


@dataclass
class ScraperConfig:
    """Scraper 기본 설정"""
    
    # 기본 정보
    name: str                    # 스크래퍼 이름 (law, adrule, case 등)
    display_name: str            # 표시 이름 (한국어)
    dept_code: str              # 부처코드
    collection_name: str        # MongoDB 컬렉션명
    
    # 기본값
    max_pages: Optional[int] = None    # None=모든 페이지
    max_concurrent: int = 3            # 동시 크롤링 수
    
    # 타임아웃 및 재시도
    timeout_seconds: int = 60          # 페이지 로딩 타임아웃
    retries: int = 2                   # 재시도 횟수
    retry_delay_seconds: int = 30      # 재시도 간격

    # crawl.py 가 컬렉션별로 갈라지던 부분 — 새 컬렉션은 이 항목만 채우면 된다.
    # 목록 수집기에 넘기는 참조 URL({dept_code} 자리에 부처코드). 판례 목록만 여기서 부처코드를 읽는다.
    list_page_url: str = ""
    # update 모드에서 기존 문서를 거르는 방식: UPDATE_BY_EFFECTIVE_DATE | UPDATE_BY_DOC_ID
    update_strategy: str = "doc_id"
    # 현재(active) 문서 중 이 필터에 걸리는 것은 doc_id 집합에서 빼서 다시 수집한다(첨부 추출 재시도·본문 없음)
    retry_filter: Optional[dict] = None
    # 일부 섹션만 저장된 문서(metadata.save_incomplete)도 다시 수집하는가. 법령·행정규칙은 시행일자 방식 안에서 같은 표시를 쓴다.
    retry_save_incomplete: bool = False

    @property
    def list_module(self) -> str:
        return f"scripts.{self.name}.logic.list_scraper"

    @property
    def scraper_module(self) -> str:
        return f"scripts.{self.name}.logic.scraper"

    def resolve_list_page_url(self) -> str:
        return self.list_page_url.format(dept_code=self.dept_code)


# ============================================================================
# 모든 SCRAPER 설정
# ============================================================================

SCRAPERS = {
    'law': ScraperConfig(
        name='law',
        display_name='법령',
        dept_code='1492000',
        collection_name='law',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/lsAstSc.do?tabMenuId=437&cptOfiCd={dept_code}',
        update_strategy=UPDATE_BY_EFFECTIVE_DATE,
    ),
    'adrule': ScraperConfig(
        name='adrule',
        display_name='행정규칙',
        dept_code='1492000',
        collection_name='adrule',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/admRulAstSc.do?menuId=391&subMenuId=397&tabMenuId=441&cptOfiCd={dept_code}',
        update_strategy=UPDATE_BY_EFFECTIVE_DATE,
    ),
    'case': ScraperConfig(
        name='case',
        display_name='판례',
        dept_code='1492000',
        collection_name='case',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/precAstSc.do?menuId=391&subMenuId=397&tabMenuId=443&cptOfiCd={dept_code}',
        retry_filter=METADATA_ONLY_FILTER,
        retry_save_incomplete=True,
    ),
    'decision': ScraperConfig(
        name='decision',
        display_name='심의결정례',
        dept_code='1492000',
        collection_name='decision',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://nlrc.go.kr/nlrc/mainCase/judgment/index.do',
    ),
    'interpretation': ScraperConfig(
        name='interpretation',
        display_name='해석례',
        dept_code='1492000',
        collection_name='interpretation',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/cgmExpcAstSc.do?menuId=391&subMenuId=397&tabMenuId=741&query=',
        retry_save_incomplete=True,
    ),
    'mediation_case': ScraperConfig(
        name='mediation_case',
        display_name='조정사건례',
        dept_code='1492000',
        collection_name='mediation_case',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://nlrc.go.kr/nlrc/mainCase/mediatioin/index.do',
        retry_filter=PDF_RETRY_FILTER,
    ),
    'judgment': ScraperConfig(
        name='judgment',
        display_name='주요판정사례',
        dept_code='nlrc',
        collection_name='judgment',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://nlrc.go.kr/nlrc/mainCase/judgment/index.do',
        retry_filter=PDF_RETRY_FILTER,
    ),
    'constitutional_decc': ScraperConfig(
        name='constitutional_decc',
        display_name='헌재결정례',
        dept_code='1492000',
        collection_name='constitutional_decc',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/detcAstScListR.do',
        retry_filter=METADATA_ONLY_FILTER,
        retry_save_incomplete=True,
    ),
    'legislation_expc': ScraperConfig(
        name='legislation_expc',
        display_name='법제처해석례',
        dept_code='1492000',
        collection_name='legislation_expc',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/expcAstScListR.do',
        retry_filter=METADATA_ONLY_FILTER,
        retry_save_incomplete=True,
    ),
    'admin_decc': ScraperConfig(
        name='admin_decc',
        display_name='행정심판재결례',
        dept_code='1492000',
        collection_name='admin_decc',
        max_pages=None,
        max_concurrent=3,
        list_page_url='https://www.law.go.kr/LSW/deccAstScListR.do',
        retry_filter=METADATA_ONLY_FILTER,
        retry_save_incomplete=True,
    ),
}


# ============================================================================
# 환경별 설정
# ============================================================================

@dataclass
class EnvironmentConfig:
    """환경 설정"""
    
    # MongoDB — 연결 문자열은 환경변수에서만 (하드코딩 자격증명 제거)
    mongodb_url: str = os.getenv("MONGODB_URI", os.getenv("MONGO_URI", ""))
    mongodb_database: str = os.getenv("MONGO_DB_NAME", "original_db")
    
    # 출력
    output_dir: str = "data/output"
    log_dir: str = "logs"


# 환경별 인스턴스
ENV = EnvironmentConfig()


# ============================================================================
# 유틸리티 함수
# ============================================================================

def get_scraper_config(scraper_name: str) -> ScraperConfig:
    """스크래퍼 설정 조회"""
    if scraper_name not in SCRAPERS:
        raise ValueError(f"Unknown scraper: {scraper_name}")
    return SCRAPERS[scraper_name]


def get_all_scrapers() -> dict:
    """모든 스크래퍼 설정 조회"""
    return SCRAPERS


def get_scraper_list() -> list:
    """스크래퍼 이름 목록"""
    return list(SCRAPERS.keys())
