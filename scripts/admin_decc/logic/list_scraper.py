"""
admin_decc 목록 크롤러 (법제처 DRF Open API 기반)
구형 deccAstScListR.do 대신 target=decc DRF API 사용 — 2026년까지 최신 데이터 포함
"""
import asyncio
import requests
import xml.etree.ElementTree as ET
import os
import sys
import json
import time
from typing import Optional

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='admin_decc')

DRF_URL = 'https://www.law.go.kr/DRF/lawSearch.do'
OC = 'inwoong100'
DISPLAY = 100
BASE_DETAIL_URL = 'https://www.law.go.kr/LSW/deccInfoP.do'

# 고용노동부 소관 법령 영역 키워드 (사건명 + 재결구분명 대상)
LABOR_KEYWORDS = [
    '근로', '임금', '해고', '고용보험', '산재', '노동', '퇴직',
    '육아휴직', '출산휴가', '최저임금', '직업안정', '실업급여',
    '단체협약', '단체교섭', '파업', '쟁의', '노동조합', '노조',
    '파견근로', '기간제', '직장내괴롭힘', '직장 내 괴롭힘',
    '장애인고용', '고령자고용', '외국인근로자', '직업훈련', '직업능력',
    '산업재해', '업무상재해', '근재', '고용촉진', '고용안정',
    '근로복지', '노사', '채용', '해직', '정직', '강등', '징계해고',
    '부당노동행위', '부당해고', '퇴직금', '모성보호', '취업',
]


def _is_labor_related(case_name: str, category: str) -> bool:
    text = (case_name or '') + ' ' + (category or '')
    return any(kw in text for kw in LABOR_KEYWORDS)


def _fetch_page(page: int) -> tuple[list, int]:
    """DRF API 한 페이지 조회 → (항목 리스트, 총건수)"""
    resp = requests.get(
        DRF_URL,
        params={
            'OC': OC,
            'target': 'decc',
            'type': 'XML',
            'query': '',
            'display': DISPLAY,
            'page': page,
        },
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.law.go.kr/'},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    total = int(root.findtext('totalCnt') or 0)
    items = []
    for decc in root.findall('decc'):
        seq = decc.findtext('행정심판재결례일련번호', '')
        if not seq:
            continue
        items.append({
            'seq': seq,
            'name': decc.findtext('사건명', ''),
            'case_num': decc.findtext('사건번호', ''),
            'date': decc.findtext('의결일자', ''),
            'authority': decc.findtext('재결청', ''),
            'category': decc.findtext('재결구분명', ''),
            'url': f'{BASE_DETAIL_URL}?deccSeq={seq}&mode=3',
        })
    return items, total


async def fetch_urls(start_url: str = '', max_pages_arg: Optional[int] = None) -> list:
    """
    DRF API로 고용노동부 관련 행정심판재결례 URL 목록 수집
    노동 관련 키워드 필터링만 수행; 기존 DB 중복 제거는 crawl.py Step 1.5에서 처리
    """
    loop = asyncio.get_event_loop()

    # 1페이지로 총 건수 파악
    first_items, total = await loop.run_in_executor(None, _fetch_page, 1)
    total_pages = (total + DISPLAY - 1) // DISPLAY
    if max_pages_arg:
        total_pages = min(total_pages, max_pages_arg)

    logger.info(f'DRF 행정심판재결례 총 {total}건 / {total_pages}페이지 — 노동 관련 필터링 시작')

    result = []

    def _process_items(items: list) -> list:
        return [item for item in items if _is_labor_related(item['name'], item['category'])]

    result.extend(_process_items(first_items))

    for page in range(2, total_pages + 1):
        try:
            items, _ = await loop.run_in_executor(None, _fetch_page, page)
            result.extend(_process_items(items))
        except Exception as e:
            logger.error(f'페이지 {page} 수집 실패: {e}')
            await asyncio.sleep(5)
            continue

        if page % 50 == 0:
            logger.info(f'  {page}/{total_pages}페이지 완료, 노동 관련 누적: {len(result)}건')
        # DRF API 부하 방지 (50ms)
        await asyncio.sleep(0.05)

    logger.info(f'✅ {len(result)}건 노동 관련 URL 수집 완료 (기존 DB 중복은 crawl.py Step 1.5에서 제거)')
    return result


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', default='data/output/admin_decc_urls.jsonl')
    parser.add_argument('--max_pages', type=int, default=None)
    args = parser.parse_args()

    urls = asyncio.run(fetch_urls(max_pages_arg=args.max_pages))
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        for item in urls:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(f'저장 완료: {len(urls)}건 → {args.output}')
