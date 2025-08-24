import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import os
from urllib.parse import urlparse, parse_qs, urljoin
import sys
import requests
from PIL import Image, ImageEnhance
import pytesseract
import io
import cv2
import numpy as np
import tempfile


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger
from scripts.utils.ocr import call_clova_ocr

logger = get_logger(__name__, scraper_type='adrule')

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = s.replace('"', '')
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _image_bytes_to_text_final_opencv(image_bytes: bytes) -> str:
    """[최종] OpenCV로 표의 윤곽선을 제거하고 설정을 최적화하여 OCR 인식률을 극대화합니다."""
    if not image_bytes: return ""
    try:
        logger.info("최종 OpenCV OCR 시작 (윤곽선 제거 포함)...")

        # 1. 이미지 로드
        np_arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # 2. 이미지 흑백 반전 및 윤곽선 찾기 (테이블 선을 잘 찾기 위함)
        inverted_image = cv2.bitwise_not(gray_image)
        contours, _ = cv2.findContours(inverted_image, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        # 3. 찾은 윤곽선(테이블 선)을 흰색으로 덮어쓰기
        mask = np.zeros(image.shape[:2], dtype="uint8")
        for contour in contours:
            # 특정 크기 이상의 윤곽선만 표의 선으로 간주하여 제거
            if cv2.contourArea(contour) > 1000:
                cv2.drawContours(mask, [contour], -1, (255), thickness=cv2.FILLED)

        # 원본 이미지에 마스크를 적용하여 선 제거
        line_removed_image = cv2.bitwise_or(gray_image, mask)

        # 4. 최종적으로 적응형 스레시홀딩 적용
        processed_image = cv2.adaptiveThreshold(
            line_removed_image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # 5. Tesseract 설정 최적화
        # --psm 4: 텍스트가 한 개의 열(column)이라고 가정
        # tessedit_char_whitelist: 인식할 문자를 미리 지정하여 정확도 향상 (주로 숫자 필드에 유용)
        custom_config = r'--oem 3 --psm 4 -l kor+eng'
        text = pytesseract.image_to_string(processed_image, config=custom_config)

        processed_text = re.sub(r'\n\s*\n', '\n', text).strip()
        logger.info("최종 OpenCV OCR 성공")
        return processed_text

    except Exception as e:
        logger.error(f"최종 OpenCV OCR 처리 중 오류 발생: {e}")
        return "\n[이미지 처리 오류]\n"

# --- 유형 1 파서 ---
def _parse_structured_content(html: str, doc_id: str, url: str, doc_title: str):
    """'제1조' 등 정형화된 법령 구조를 파싱합니다."""
    soup = BeautifulSoup(html, 'html.parser')
    chunks = []
    current_chapter = ""
    for pgroup_div in soup.select('div.pgroup'):
        chapter_tag = pgroup_div.select_one('p.gtit')
        if chapter_tag: current_chapter = clean_spaces(chapter_tag.get_text())
        title_tag = pgroup_div.select_one('span.bl label')
        if not title_tag or not title_tag.get_text(strip=True): continue
        article_title = title_tag.get_text(strip=True)
        article_id_num = "".join(filter(str.isdigit, article_title.split('(')[0]))
        if not article_id_num: continue

        lawcon_div = pgroup_div.select_one('.lawcon')
        if not lawcon_div: continue

        # 텍스트를 추출하기 전에 모든 a 태그를 제거하여 내용만 남깁니다.
        for a_tag in lawcon_div.find_all('a'):
            a_tag.unwrap()

        # separator 인수를 제거하여 불필요한 줄바꿈을 방지합니다.
        text_content = lawcon_div.get_text()
        final_text = "\n".join(line.strip() for line in text_content.splitlines() if line.strip())

        chunks.append({
            "chunk_id": f"doc:{doc_id}:article_{article_id_num}", "doc_id": doc_id,
            "title": clean_spaces(article_title), "text": clean_spaces(final_text),
            "metadata": {"chapter": current_chapter or doc_title}, "source_url": url
        })
    return chunks

# --- 유형 2 파서 ---
def _parse_unstructured_content(html: str, doc_id: str, url: str, doc_title: str):
    """비정형 텍스트 고시 구조를 파싱합니다."""
    soup = BeautifulSoup(html, 'html.parser') # <-- 수정된 부분: 함수 내부에서 BeautifulSoup 객체 생성
    content_div = soup.select_one('.lawcon') or soup
    chunks = []
    sections, current_section_content = [], []
    for el in content_div.find_all(True, recursive=False):
        el_text = el.get_text(strip=True)
        if not el_text: continue
        if re.match(r'^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s', el_text):
            if current_section_content: sections.append(current_section_content)
            current_section_content = [el]
        else:
            current_section_content.append(el)
    if current_section_content: sections.append(current_section_content)
    chunk_index = 1
    for section_elements in sections:
        first_el_text = section_elements[0].get_text(strip=True)
        is_main_titled = re.match(r'^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s', first_el_text)
        title = first_el_text if is_main_titled else doc_title or "서문"
        text_elements = section_elements[1:] if is_main_titled else section_elements
        text = "\n".join(el.get_text(strip=True) for el in text_elements)
        chunk_id = f"doc:{doc_id}:section_{chunk_index}" if is_main_titled else f"doc:{doc_id}:preamble"
        if is_main_titled: chunk_index += 1
        if not text.strip() and not title.strip() == doc_title: continue
        chunks.append({
            "chunk_id": chunk_id, "doc_id": doc_id,
            "title": clean_spaces(title), "text": clean_spaces(text),
            "metadata": {"chapter": doc_title}, "source_url": url
        })
    return chunks

def _format_ocr_text_to_chunks(full_text: str, doc_id: str, doc_title: str, url: str) -> list:
    """[최종] '1.'부터 시작하는 '정확한 순서'의 섹션만 찾아 Chunk로 분할하는 함수"""

    processed_chunks = []
    clean_text = re.sub(r'^[xX]\s*', '', full_text.strip()).strip()

    # 1. 텍스트 전체에서 모든 잠재적 섹션 마커를 찾음
    potential_matches = []
    for match in re.finditer(r'\s(\d+)\.\s', clean_text):
        potential_matches.append({'num': int(match.group(1)), 'start': match.start()})

    # 2. '1'로 시작하고 순서가 정확히 이어지는 유효한 섹션 그룹만 필터링
    valid_matches_info = []
    # 먼저 '1'번 섹션을 찾음
    first_one_index = -1
    for i, match_info in enumerate(potential_matches):
        if match_info['num'] == 1:
            first_one_index = i
            break

    # '1'번을 찾았으면, 그 이후부터 2, 3, 4... 순서대로 이어지는지 확인
    if first_one_index != -1:
        expected_num = 1
        for match_info in potential_matches[first_one_index:]:
            if match_info['num'] == expected_num:
                valid_matches_info.append(match_info)
                expected_num += 1
            # 순서가 끊기면 (예: 1, 2 다음에 4가 나오면) 더 이상 유효한 그룹으로 보지 않음
            elif match_info['num'] > expected_num:
                break

    # 3. 서문(Preamble) 추출
    preamble_end_index = valid_matches_info[0]['start'] if valid_matches_info else len(clean_text)
    preamble_content = clean_text[:preamble_end_index].strip()

    if preamble_content:
        processed_chunks.append({
            "chunk_id": f"doc:{doc_id}:preamble_ocr", "doc_id": doc_id, "title": "서문",
            "text": clean_spaces(preamble_content),
            "metadata": {"chapter": doc_title, "source_type": "pdf_ocr"}, "source_url": url
        })

    # 4. 유효한 섹션 그룹을 순회하며 Chunk 생성
    for i, match_info in enumerate(valid_matches_info):
        start_index = match_info['start']
        end_index = valid_matches_info[i+1]['start'] if i + 1 < len(valid_matches_info) else len(clean_text)

        section_full_text = clean_text[start_index:end_index].strip()

        # 제목과 내용 분리
        title = section_full_text
        text_content = ""
        # '여부', '기간:' 등 키워드를 기준으로 제목과 내용 분리
        title_match = re.match(r'([^:]*?(?:여부|기간:|기준|방법|금액))', section_full_text)
        if title_match:
            title = title_match.group(1).strip()
            text_content = section_full_text[len(title):].strip()

        processed_chunks.append({
            "chunk_id": f"doc:{doc_id}:section_{match_info['num']}_ocr", "doc_id": doc_id,
            "title": clean_spaces(title), "text": clean_spaces(text_content),
            "metadata": {"chapter": doc_title, "source_type": "pdf_ocr"}, "source_url": url
        })

    return processed_chunks

# --- 유형 3 파서 ---
async def _parse_pdf_viewer_content(page, doc_id: str, url: str, doc_title: str, output_name: str, debug: bool = False):
    """PDF 뷰어(iframe)가 포함된 구조를 파싱합니다 (OCR 사용)."""
    chunks = []
    original_viewport_size = page.viewport_size
    try:
        A4_VIEWPORT_WIDTH = 2480
        A4_VIEWPORT_HEIGHT = 3508
        logger.info(f"Viewport 크기를 A4 비율로 변경: {A4_VIEWPORT_WIDTH}x{A4_VIEWPORT_HEIGHT}")
        await page.set_viewport_size({"width": A4_VIEWPORT_WIDTH, "height": A4_VIEWPORT_HEIGHT})
        await page.wait_for_timeout(1000) # 리사이즈 적용 대기

        iframe = page.frame_locator('iframe.fancybox-iframe')

        try:
            # 1. '전체 화면' 또는 유사한 기능을 하는 버튼의 선택자(selector)를 찾습니다.
            fullscreen_button_selector = 'div#desktop-fullscreen-btn__img' # 예시 선택자입니다. 실제 페이지에 맞게 수정하세요.

            logger.info("전체 화면 버튼을 클릭합니다.")
            await iframe.locator(fullscreen_button_selector).click()

            # 2. 전체 화면으로 전환되고 콘텐츠가 모두 렌더링될 때까지 잠시 기다립니다.
            logger.info("전체 화면 로딩을 대기합니다.")
            await page.wait_for_timeout(3000) # 3초 대기 (네트워크나 PC 환경에 따라 조절)

        except Exception as e:
            logger.warning(f"전체 화면 버튼을 찾거나 클릭하는 데 실패했습니다. 기본 뷰로 계속 진행합니다. 오류: {e}")


        page_containers = iframe.locator('div[id^="page-area"]')
        page_count = await page_containers.count()
        logger.info(f"{page_count}개의 PDF 페이지 컨테이너 발견")

        # # OCR 결과를 간단히 전처리하는 내부 함수
        # def _preprocess_ocr_bytes(image_bytes: bytes) -> str:
        #     if not image_bytes: return ""
        #     try:
        #         image = Image.open(io.BytesIO(image_bytes))
        #         text = pytesseract.image_to_string(image, lang='kor+eng')
        #         return re.sub(r'\n\s*\n', '\n', text).strip()
        #     except Exception as e:
        #         logger.error(f"이미지 바이트 OCR 처리 중 오류 발생: {e}")
        #         return ""

        content_parts = []
        for i in range(page_count):
            page_container_locator = page_containers.nth(i)

            # 스크린샷 대상은 컨테이너 전체
            image_bytes = await page_container_locator.screenshot()

            ocr_text = call_clova_ocr(image_bytes)
            content_parts.append(ocr_text)

            # # --- 디버깅: 스크린샷 파일로 저장 ---
            # debug_dir = os.path.join(project_root, 'debug')
            # if not os.path.exists(debug_dir):
            #     os.makedirs(debug_dir)
            # screenshot_path = os.path.join(debug_dir, f'{output_name}_ocr_image_{i+1}.png')
            # with open(screenshot_path, 'wb') as f:
            #     f.write(image_bytes)
            # logger.info(f"OCR용 스크린샷 저장됨: {screenshot_path}")
            # # --- 디버깅 코드 끝 ---
            #
            # content_parts.append(_image_bytes_to_text_final_opencv(image_bytes))

        full_text = "\n".join(part for part in content_parts if part)

        if debug and full_text:
            debug_dir = os.path.join(project_root, 'debug', output_name)
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)

            # Chunk로 나누기 전의 OCR 원본 텍스트를 파일로 저장
            raw_text_path = os.path.join(debug_dir, f'{output_name}_raw_ocr.txt')
            with open(raw_text_path, 'w', encoding='utf-8') as f:
                f.write(full_text)
            logger.info(f"디버그용 OCR Raw 텍스트 저장됨: {raw_text_path}")

        # if full_text:
        #     chunks.append({
        #         "chunk_id": f"doc:{doc_id}:full_content_ocr", "doc_id": doc_id,
        #         "title": doc_title, "text": clean_spaces(full_text),
        #         "metadata": {"chapter": doc_title, "source_type": "pdf_ocr"}, "source_url": url
        #     })
        if full_text:
            chunks = _format_ocr_text_to_chunks(full_text, doc_id, doc_title, url)

    except Exception as e:
        logger.error(f"PDF 뷰어 처리 중 오류 발생: {e}")
    finally:
        if original_viewport_size:
            logger.info("원래 Viewport 크기로 복원")
            await page.set_viewport_size(original_viewport_size)
    return chunks

async def parse_law_html(page: 'Page', doc_id: str, doc_title: str, url: str, output_name: str, debug: bool = False):
    """페이지 객체를 받아 유형을 판별하고 적절한 파서를 호출하는 컨트롤 타워."""
    outer_html = await page.content()
    soup = BeautifulSoup(outer_html, 'html.parser')

    is_structured = any(
        label.get_text(strip=True).startswith('제')
        for label in soup.select('div.pgroup span.bl label')
    )
    contains_pdf_iframe = bool(soup.select_one('iframe.fancybox-iframe'))

    content_selector = '#conScroll'

    if is_structured:
        logger.info("유형 1: 정형화된 법령 구조 감지")
        content_html = await page.locator(content_selector).inner_html()
        return _parse_structured_content(content_html, doc_id, url, doc_title)
    elif contains_pdf_iframe:
        logger.info("유형 3: PDF 뷰어(iframe) 구조 감지 (OCR 실행)")
        return await _parse_pdf_viewer_content(page, doc_id, url, doc_title, output_name, debug)
    else:
        logger.info("유형 2: 텍스트 기반 비정형 구조 감지")
        content_html = await page.locator(content_selector).inner_html()
        return _parse_unstructured_content(content_html, doc_id, url, doc_title)

def save_to_file(data, filename):
    if not isinstance(data, list): data = [data]
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        logger.info(f"'{directory}' 폴더를 생성했습니다.")
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")

async def scrape_and_save(url: str, output_dir: str, output_name: str, debug: bool = False):
    """Playwright를 실행하여 웹페이지 컨텐츠를 가져오고 파일로 저장합니다."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            logger.info(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            try:
                logger.info("로딩 마스크가 나타나기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="visible", timeout=1000)
                logger.info("로딩 마스크가 사라지기를 기다리는 중...")
                await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=3000)
            except Exception:
                logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")

            logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
            await page.wait_for_timeout(1000)
            logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()
            logger.info("데이터 파싱 중...")

            # ▼▼▼ 디버그 코드 추가 ▼▼▼
            if debug:
                debug_dir = os.path.join(project_root, 'debug', output_name)
                if not os.path.exists(debug_dir):
                    os.makedirs(debug_dir)

                # 현재 페이지의 전체 HTML 저장
                html_path = os.path.join(debug_dir, f'{output_name}_page_source.html')
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(await page.content())
                logger.info(f"디버그용 HTML 소스 저장됨: {html_path}")
                # ▲▲▲ 디버그 코드 추가 ▲▲▲

            doc_title = clean_spaces(await page.locator('#conTop h2').text_content())
            doc_subtitle = clean_spaces(await page.locator('div.ct_sub, div.subtit1').text_content())

            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            doc_id_keys = ['admRulSeq', 'admRulId']
            doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
            if not doc_id:
                doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)

            document_data = { "doc_id": doc_id, "title": doc_title, "subtitle": doc_subtitle, "source_url": url }

            chunks = await parse_law_html(page, doc_id, doc_title, url, output_name, debug)

            doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
            chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')

            if document_data.get("title"):
                save_to_file(document_data, doc_filename)
            if chunks:
                save_to_file(chunks, chunk_filename)
            else:
                logger.warning("페이지에서 청크(조문) 데이터를 찾지 못했습니다.")


        except Exception as e:
            logger.error(f"스크레이핑 중 에러 발생: {e}", exc_info=True)
            error_dir = os.path.join(project_root, 'debug')
            if not os.path.exists(error_dir):
                os.makedirs(error_dir)

            screenshot_path = os.path.join(error_dir, f'{output_name}_error.png')
            html_path = os.path.join(error_dir, f'{output_name}_error.html')

            await page.screenshot(path=screenshot_path)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(await page.content())

            logger.info(f"에러 스크린샷: {screenshot_path}, HTML: {html_path}")

        finally:
            await browser.close()

if __name__ == '__main__':

    # --- 옵션 1: 전체 스크레이핑 실행 (기존 방식) ---
    async def main_scrape():
        target_url = "https://www.law.go.kr/admRulInfoP.do?admRulSeq=2100000245668"
        output_directory = "data/output"
        output_file_name = "최저임금고시_2025"

        # debug=True로 설정하여 중간 결과물을 ./debug/ 폴더에 저장
        await scrape_and_save(target_url, output_directory, output_file_name, debug=True)

    # --- 옵션 2: 저장된 OCR 텍스트 파일로 Chunk 분할 로직만 테스트 ---
    def test_chunk_parser():
        print("--- OCR Chunk 파서 테스트 시작 ---")
        # scrape_and_save(debug=True) 실행 후 생성된 debug 파일을 사용
        # 파일 경로와 doc 정보는 실제 테스트하려는 파일에 맞게 수정해야 합니다.
        debug_file_path = "debug/최저임금고시_2025/최저임금고시_2025_raw_ocr.txt"

        doc_info = {
            "doc_id": "2100000245668",
            "doc_title": "2025년 적용 최저임금 고시",
            "url": "https://www.law.go.kr/admRulInfoP.do?admRulSeq=2100000245668"
        }

        try:
            with open(debug_file_path, 'r', encoding='utf-8') as f:
                raw_text = f.read()

            # 디버깅하려는 핵심 함수를 직접 호출
            chunks = _format_ocr_text_to_chunks(
                full_text=raw_text,
                doc_id=doc_info["doc_id"],
                doc_title=doc_info["doc_title"],
                url=doc_info["url"]
            )

            print(f"총 {len(chunks)}개의 Chunk가 생성되었습니다.")
            # 생성된 Chunk 내용을 예쁘게 출력
            print(json.dumps(chunks, ensure_ascii=False, indent=2))

        except FileNotFoundError:
            print(f"[오류] 디버그 파일을 찾을 수 없습니다: {debug_file_path}")
            print("먼저 main_scrape()를 debug=True로 실행하여 파일을 생성하세요.")
        except Exception as e:
            print(f"테스트 중 오류 발생: {e}")

    # 실행하려는 작업을 선택하세요 (주석 처리로 선택)
    asyncio.run(main_scrape()) # 전체 스크레이핑
    #test_chunk_parser()       # 저장된 파일로 파서만 테스트