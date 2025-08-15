import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import json
import re
import argparse
import os
from urllib.parse import urlparse, parse_qs

def clean_spaces(s: str) -> str:
    """텍스트의 불필요한 공백과 줄바꿈을 정리합니다."""
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def parse_law_html(html: str, url: str):
    """
    [4차 수정된 최종 파싱 로직]
    - 법령의 '문서' 정보와 각 '조'에 해당하는 '청크' 정보를 분리하여 반환합니다.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # --- 1. 문서(Document) 정보 추출 ---
    doc_title_tag = soup.select_one('#conTop h2')
    doc_title = doc_title_tag.get_text(strip=True) if doc_title_tag else ""

    doc_subtitle_tag = soup.select_one('div.ct_sub')
    doc_subtitle = doc_subtitle_tag.get_text(strip=True) if doc_subtitle_tag else ""

    # URL에서 lsiSeq 값을 추출하여 doc_id로 사용
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    doc_id = query_params.get('lsiSeq', [None])[0]
    if not doc_id:
        print("ERROR : URL에서 문서 ID(lsiSeq)를 찾을 수 없습니다.")
        # 대체 ID 생성
        doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)

    document_data = {
        "doc_id": doc_id,
        "title": doc_title,
        "subtitle": doc_subtitle,
        "source_url": url
    }

    # --- 2. 청크(Chunk) 정보 추출 ---
    content_div = soup.select_one('#contentBody, #conScroll, .law-view-content')
    if not content_div:
        print("ERROR: 법률 내용이 담긴 메인 컨테이너를 찾지 못했습니다.")
        return document_data, []

    chunks = []
    current_chapter = ""

    for pgroup_div in content_div.select('div.pgroup'):
        chapter_tag = pgroup_div.select_one('p.gtit')
        if chapter_tag:
            chapter_text = chapter_tag.get_text(strip=True)
            if re.match(r'^제\s*\d+\s*장', chapter_text):
                current_chapter = chapter_text

        title_tag = pgroup_div.select_one('span.bl label')
        if not title_tag:
            continue
        article_title = title_tag.get_text(strip=True)

        article_id_num = "".join(filter(str.isdigit, article_title.split('(')[0]))
        if not article_id_num:
            continue

        chunk_data = {
            "chunk_id": f"doc:{doc_id}:article_{article_id_num}",
            "doc_id": doc_id,
            "title": article_title,
            "text": clean_spaces(pgroup_div.get_text(separator='\n', strip=True)),
            "metadata": {"chapter": current_chapter},
            "source_url": url
        }
        chunks.append(chunk_data)

    # 두 종류의 데이터를 튜플 형태로 반환
    return document_data, chunks

def save_to_file(data, filename):
    """데이터를 JSONL 형식으로 파일에 저장합니다."""
    # 데이터가 리스트가 아니면 리스트로 감싸서 처리
    if not isinstance(data, list):
        data = [data]

    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        print(f"'{directory}' 폴더를 생성했습니다.")

    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    print(f"SUCCESS: 데이터 {len(data)}건을 {filename} 파일로 성공적으로 저장했습니다.")

async def main(url: str, output_name: str):
    """Playwright를 실행하여 웹페이지 컨텐츠를 가져오는 메인 함수"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"페이지로 이동 중: {url}")
            await page.goto(url, wait_until='networkidle', timeout=60000)

            print("컨텐츠 로딩을 기다리는 중...")
            await page.wait_for_selector('div.pgroup', timeout=30000)

            print("페이지 HTML 컨텐츠를 가져오는 중...")
            html = await page.content()

            print("데이터 파싱 중...")
            # 반환값 2개로 변경됨
            doc_data, chunk_data = parse_law_html(html, url)

            doc_filename = os.path.join('export', f'{output_name}_document.jsonl')
            chunk_filename = os.path.join('export', f'{output_name}_chunks.jsonl')

            if doc_data:
                save_to_file(doc_data, doc_filename)

            if chunk_data:
                save_to_file(chunk_data, chunk_filename)

            if not doc_data and not chunk_data:
                print("ERROR: 스크레이핑된 데이터가 없어 파일을 생성하지 않았습니다.")

        except Exception as e:
            print(f"스크레이핑 중 에러 발생: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="국가법령정보센터 법령 페이지를 스크레이핑하여 documents.jsonl과 chunks.jsonl 파일로 저장합니다.")
    parser.add_argument("url", help="스크레이핑할 법령 페이지의 전체 URL")
    parser.add_argument("-o", "--output", required=True, help="출력 파일의 기본 이름 (예: gasa-law)")

    args = parser.parse_args()

    asyncio.run(main(args.url, args.output))