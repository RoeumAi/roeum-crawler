# scripts/utils/ocr.py
# Naver CLOVA OCR → OpenAI Vision OCR로 교체
# adrule 스크래퍼의 PDF 뷰어(iframe) 이미지 텍스트 추출에 사용

import base64
import hashlib
import os

from openai import OpenAI
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='adrule')

OCR_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'cache', 'ocr_results')
OCR_MODEL = os.getenv('OCR_MODEL', 'gpt-4o')

OCR_SYSTEM_PROMPT = (
    "You are an OCR engine for Korean legal administrative documents. "
    "Return only the text visible in the provided image. "
    "Preserve reading order and line breaks when possible. "
    "Do not summarize, translate, explain, or add any markdown formatting. "
    "Do not invent or guess missing text. "
    "If the page is blank or unreadable, return an empty string."
)


def call_clova_ocr(image_bytes: bytes, image_format: str = 'png') -> str:
    """
    OpenAI Vision으로 이미지에서 텍스트를 추출합니다.
    기존 Naver CLOVA OCR과 동일한 인터페이스를 유지합니다. (캐싱 포함)
    """
    os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    image_hash = hashlib.md5(image_bytes).hexdigest()
    cache_file_path = os.path.join(OCR_CACHE_DIR, f"{image_hash}.txt")

    if os.path.exists(cache_file_path):
        logger.info(f"✅ [Cache HIT] OCR 결과를 캐시에서 불러옵니다: {cache_file_path}")
        with open(cache_file_path, 'r', encoding='utf-8') as f:
            return f.read()

    logger.info(f"💰 [API CALL] OpenAI OCR API 호출 (model={OCR_MODEL})")

    api_key = os.getenv('OPENAI_API_KEY', '')
    if not api_key:
        logger.error("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        return "[OCR 오류: OPENAI_API_KEY 미설정]"

    try:
        client = OpenAI(api_key=api_key, timeout=120)
        mime_type = f"image/{image_format}"
        encoded = base64.b64encode(image_bytes).decode('ascii')
        data_url = f"data:{mime_type};base64,{encoded}"

        response = client.chat.completions.create(
            model=OCR_MODEL,
            messages=[
                {"role": "system", "content": OCR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "이미지의 텍스트를 그대로 추출해주세요."},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "high"},
                        },
                    ],
                },
            ],
            max_tokens=4096,
        )

        text = (response.choices[0].message.content or '').strip()

        with open(cache_file_path, 'w', encoding='utf-8') as f:
            f.write(text)

        logger.info(f"✅ OCR 완료 — {len(text)}자 추출, 캐시 저장: {cache_file_path}")
        return text

    except Exception as e:
        logger.error(f"OpenAI OCR 오류: {e}")
        return "[OCR 처리 오류]"
