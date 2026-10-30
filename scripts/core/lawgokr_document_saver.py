"""법령·행정규칙 공통 저장 로직 — 조문 분리, JSONL 저장, 조문별 저장 루프, 스레드 실행 래퍼.

두 스크래퍼가 같은 뼈대를 복사해 쓰던 부분만 모았다. 컬렉션마다 다른 것(파서, 조문 문서 스키마, 로그 문구,
저장소 생성, 별표 수집, 결과 dict 모양)은 스크래퍼에 남기고 콜백으로 주입한다.
"""
import asyncio
import contextvars
import functools
import json
import os
import re
from typing import Callable, Dict, List

# [제X조(제목)]\n내용 — 번호(1번 그룹), 의N(2번, 옵션), 제목(3번), 내용(4번). 다음 [제N 이 나오기 전까지가 내용.
ARTICLE_PATTERN = r'\[제(\d+)(?:조(?:의(\d+))?)\(([^\)]+)\)\]\n((?:(?!\[제\d+).)*)'


def split_articles_by_number(content: str) -> List[Dict[str, str]]:
    """
    통합 content를 조별로 분리

    예: "[제1조(목적)]\n내용..." → [{"article_number": "1", "article_number_numeric": 1.0, "article_title": "제1조(목적)", "content": "내용..."}]
    예: "[제5조의1(정의)]\n내용..." → [{"article_number": "5.1", "article_number_numeric": 5.1, "article_title": "제5조의1(정의)", "content": "내용..."}]

    Returns:
        조별로 분리된 딕셔너리 리스트 (article_number, article_number_numeric, article_title, content 포함)
    """
    articles = []
    for match in re.finditer(ARTICLE_PATTERN, content, re.DOTALL):
        article_num = match.group(1)
        sub_num = match.group(2)  # "의" 다음 숫자 또는 None
        article_topic = match.group(3)
        article_content = match.group(4).strip()

        if sub_num:
            article_number = f"{article_num}.{sub_num}"
            article_number_numeric = float(f"{article_num}.{sub_num}")
            article_title = f"제{article_num}조의{sub_num}({article_topic})"
        else:
            article_number = article_num
            article_number_numeric = float(article_num)
            article_title = f"제{article_num}조({article_topic})"

        articles.append({
            "article_number": article_number,
            "article_number_numeric": article_number_numeric,
            "article_title": article_title,
            "content": article_content
        })
    return articles


def save_to_file(data, filename, logger) -> None:
    """JSONL 로 덤프한다(덮어쓰기)."""
    if not isinstance(data, list):
        data = [data]
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        logger.info(f"'{directory}' 폴더를 생성했습니다.")
    with open(filename, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    logger.info(f"데이터 {len(data)}건을 '{filename}' 파일로 성공적으로 저장했습니다.")


def save_articles(repo, articles: list, *, build_doc: Callable[[int, dict, int], dict],
                  log_action: Callable[[str, dict, dict], None], source_url, annex_failed: int, logger) -> str:
    """조문(별표 포함)마다 변경 감지 upsert 를 하고 결과를 돌려준다.

    build_doc(idx, article, total) → 저장할 조문 문서(스키마 조립은 컬렉션 몫, 실패하면 그 조문만 실패로 센다)
    log_action(doc_id, action_result, article_doc) → 컬렉션별 저장 로그

    반환: 조문 하나라도 못 넣었거나 완료 표시에 실패하면 "" (save_incomplete 표시로 다음 실행이 다시 수집),
    아니면 새 버전·신규가 하나라도 있으면 "new_version", 없으면 "update_existing".
    """
    saved_count = 0
    failed_count = annex_failed  # 별표를 못 읽었으면 조문은 넣되 문서는 실패로
    any_changed = False

    for idx, article in enumerate(articles, 1):
        try:
            article_doc = build_doc(idx, article, len(articles))
            doc_id, action_result = repo.upsert_with_change_detection(article_doc)
            log_action(doc_id, action_result, article_doc)
            if action_result["action"] in ("insert", "new_version"):
                any_changed = True
            saved_count += 1
        except Exception as e:
            logger.error(f"❌ 조문 저장 실패 ({article['article_title']}): {str(e)}")
            failed_count += 1
            continue

    logger.info(f"📊 MongoDB 저장 완료: 성공 {saved_count}개, 실패 {failed_count}개")
    try:
        repo.mark_save_complete(source_url, complete=failed_count == 0)
    except Exception as e:  # noqa: BLE001 - 표시 실패는 저장 결과로 판단한다
        logger.error(f"저장 완료 표시 실패: {e}")
        failed_count += 1
    if failed_count > 0:
        return ""
    return "new_version" if any_changed else "update_existing"


async def run_in_executor_with_context(executor, fn, *args, **kwargs):
    """동기 저장 함수를 스레드에서 돌린다. 실행 보고(run_report)가 별표 실패 원인을 세도록 현재 컨텍스트를 넘긴다."""
    loop = asyncio.get_running_loop()
    if kwargs:
        fn = functools.partial(fn, **kwargs)
    return await loop.run_in_executor(executor, contextvars.copy_context().run, fn, *args)
