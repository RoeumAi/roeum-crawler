"""
중앙 설정 파일

모든 scraper의 공통 설정을 정의합니다.
개별 scraper 설정은 각 폴더의 config.py에서 정의합니다.
"""

from dataclasses import dataclass
from typing import Optional

# ============================================================================
# SCRAPER 설정 정의
# ============================================================================

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
    ),
    'adrule': ScraperConfig(
        name='adrule',
        display_name='행정규칙',
        dept_code='1492000',
        collection_name='adrule',
        max_pages=None,
        max_concurrent=3,
    ),
    'case': ScraperConfig(
        name='case',
        display_name='판례',
        dept_code='1492000',
        collection_name='case',
        max_pages=None,
        max_concurrent=3,
    ),
    'decision': ScraperConfig(
        name='decision',
        display_name='심의결정례',
        dept_code='1492000',
        collection_name='decision',
        max_pages=None,
        max_concurrent=3,
    ),
    'interpretation': ScraperConfig(
        name='interpretation',
        display_name='해석례',
        dept_code='1492000',
        collection_name='interpretation',
        max_pages=None,
        max_concurrent=3,
    ),
    'mediation_case': ScraperConfig(
        name='mediation_case',
        display_name='조정사건례',
        dept_code='1492000',
        collection_name='mediation_case',
        max_pages=None,
        max_concurrent=3,
    ),
    'judgment': ScraperConfig(
        name='judgment',
        display_name='주요판정사례',
        dept_code='nlrc',
        collection_name='judgment',
        max_pages=None,
        max_concurrent=3,
    ),
    'constitutional_decc': ScraperConfig(
        name='constitutional_decc',
        display_name='헌재결정례',
        dept_code='1492000',
        collection_name='constitutional_decc',
        max_pages=None,
        max_concurrent=3,
    ),
    'legislation_expc': ScraperConfig(
        name='legislation_expc',
        display_name='법제처해석례',
        dept_code='1492000',
        collection_name='legislation_expc',
        max_pages=None,
        max_concurrent=3,
    ),
    'admin_decc': ScraperConfig(
        name='admin_decc',
        display_name='행정심판재결례',
        dept_code='1492000',
        collection_name='admin_decc',
        max_pages=None,
        max_concurrent=3,
    ),
}


# ============================================================================
# 환경별 설정
# ============================================================================

@dataclass
class EnvironmentConfig:
    """환경 설정"""
    
    # MongoDB
    mongodb_url: str = "mongodb+srv://loum_chanhee:1G2BLczQ1nipQvXC@loum.veydouo.mongodb.net/?retryWrites=true&w=majority&appName=loum"
    mongodb_database: str = "original_db"
    
    # Prefect
    prefect_api_url: str = ""  # 로컬 모드
    
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
