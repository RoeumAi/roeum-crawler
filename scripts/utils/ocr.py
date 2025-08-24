# scripts/utils/ocr.py

import requests
import json
import uuid
import time
import os
import hashlib

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
from scripts.utils.logger_config import get_logger

# --- 설정값 ---
CLOVA_API_URL = "https://1u0st2sne5.apigw.ntruss.com/custom/v1/45471/997d4f457b334034d6df8b416c0640ac0570ef693de9cc902bd04d49c825aae8/general"
CLOVA_SECRET_KEY = "UEZ1bnhuRkNqTXhGZFdkWFRzUWNZaE1QVkNPUUdNTVI="
OCR_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'cache', 'ocr_results')

# 로거 설정
logger = get_logger(__name__, scraper_type='adrule')

# --- 메인 함수 ---
def call_clova_ocr(image_bytes: bytes, image_format: str = 'png') -> str:
    """
    네이버 CLOVA OCR API를 호출하여 텍스트를 추출합니다. (캐싱 및 로깅 기능 추가)
    """
    os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    image_hash = hashlib.md5(image_bytes).hexdigest()
    cache_file_path = os.path.join(OCR_CACHE_DIR, f"{image_hash}.txt")

    # ▼▼▼ 핵심 변경 부분 (로깅 추가) ▼▼▼
    if os.path.exists(cache_file_path):
        # 캐시 파일이 존재할 경우
        logger.info(f"✅ [Cache HIT] OCR 결과를 로컬 파일에서 불러옵니다: {cache_file_path}")
        with open(cache_file_path, 'r', encoding='utf-8') as f:
            return f.read()

    # 캐시 파일이 없을 경우
    logger.info(f"💰 [API CALL] 캐시 파일이 없어 CLOVA OCR API를 호출합니다. (과금 발생)")
    # ▲▲▲ 핵심 변경 부분 (로깅 추가) ▲▲▲

    try:
        request_json = {
            'images': [{'format': image_format, 'name': 'cached_image'}],
            'requestId': str(uuid.uuid4()),
            'version': 'V2',
            'timestamp': int(round(time.time() * 1000))
        }
        payload = {'message': json.dumps(request_json).encode('UTF-8')}
        files = [('file', image_bytes)]
        headers = {'X-OCR-SECRET': CLOVA_SECRET_KEY}

        response = requests.post(CLOVA_API_URL, headers=headers, data=payload, files=files)
        response.raise_for_status()

        result = response.json()
        full_text_parts = [field['inferText'] for field in result['images'][0]['fields']]
        ocr_result = ' '.join(full_text_parts)

        # 성공적인 API 결과를 캐시 파일에 저장
        with open(cache_file_path, 'w', encoding='utf-8') as f:
            f.write(ocr_result)

        return ocr_result

    except Exception as e:
        logger.error(f"CLOVA OCR API 호출 중 오류 발생: {e}")
        return "[CLOVA OCR 처리 오류]"