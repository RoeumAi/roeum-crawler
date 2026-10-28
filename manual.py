#!/usr/bin/env python3
"""수동 실행 도구 모음 — 일일 크롤링(run_daily.sh) 밖에서 사람이 따로 돌리는 작업.

    python3 manual.py                       # 목록
    python3 manual.py <작업> --help         # 작업별 인자
    python3 manual.py <작업> [인자...]      # 실행

안전 규칙
- `기본=쓰기` 작업(인자 없이 돌리면 운영 MongoDB 에 쓰거나 지우는 것)은 `--yes` 를 붙여야 돈다.
  먼저 그 작업의 미리 보기(`--dry-run`, `--no-db` 등)로 확인한다.
- `기본=미리보기` 작업은 작업 자체의 `--apply`/`--execute` 를 줘야 쓴다.
- `조회만` 작업은 DB 에 쓰지 않는다(사이트·DB 를 읽기만).

새 수동 도구를 만들면 TASKS 에 추가하고 docs/manual-tasks.md 에 한 줄 적는다.
"""
import runpy
import sys
from dataclasses import dataclass

READ_ONLY = "조회만"
DRY_RUN_DEFAULT = "기본=미리보기"
WRITES_DEFAULT = "기본=쓰기"


@dataclass(frozen=True)
class Task:
    name: str
    module: str
    group: str
    safety: str
    summary: str


TASKS = [
    # 목록·URL 점검 (사이트만 읽음)
    Task("list:adrule", "scripts.adrule.runners.run_list_scraper", "목록·URL 점검", READ_ONLY,
         "행정규칙 목록 URL 을 JSONL 로 뽑는다"),
    Task("list:case", "scripts.case.runners.run_list_scraper", "목록·URL 점검", READ_ONLY,
         "판례 목록 URL 을 JSONL 로 뽑는다"),
    Task("list:judgment", "scripts.judgment.runners.run_list_scraper", "목록·URL 점검", READ_ONLY,
         "주요판정사례 목록 URL 을 JSONL 로 뽑는다"),
    Task("check-url:law", "scripts.law.runners.run_url_checker", "목록·URL 점검", READ_ONLY,
         "법령 목록 페이지 URL 이 유효한지 확인"),
    Task("check-url:adrule", "scripts.adrule.runners.run_url_checker", "목록·URL 점검", READ_ONLY,
         "행정규칙 목록 페이지 URL 이 유효한지 확인"),
    Task("check-url:case", "scripts.case.runners.run_url_checker", "목록·URL 점검", READ_ONLY,
         "판례 목록 페이지 URL 이 유효한지 확인"),
    Task("check-url:interpretation", "scripts.interpretation.runners.run_url_checker", "목록·URL 점검", READ_ONLY,
         "해석례 상세 페이지가 정상으로 열리는지 확인"),

    # 문서 하나·지정 목록 다시 수집
    Task("scrape:law", "scripts.law.runners.run_scraper", "지정 문서 수집", WRITES_DEFAULT,
         "법령 상세 URL 하나를 수집해 저장(--no-db 로 미리 보기)"),
    Task("scrape:adrule", "scripts.adrule.runners.run_scraper", "지정 문서 수집", WRITES_DEFAULT,
         "행정규칙 상세 URL 하나를 수집해 저장(--no-db 로 미리 보기)"),
    Task("scrape:case", "scripts.case.runners.run_scraper", "지정 문서 수집", WRITES_DEFAULT,
         "판례 상세 URL 하나를 수집해 저장(--no-db 로 미리 보기)"),
    Task("scrape:judgment", "scripts.judgment.runners.run_scraper", "지정 문서 수집", WRITES_DEFAULT,
         "주요판정사례 상세 URL 하나를 수집해 저장(--no-db 로 미리 보기)"),
    Task("cherry-pick:law", "scripts.law.runners.run_cherry_pick", "지정 문서 수집", WRITES_DEFAULT,
         "지정 법령(TARGET_LAWS)을 찾아 수집·저장"),
    Task("cherry-pick:case", "scripts.case.runners.run_cherry_pick", "지정 문서 수집", WRITES_DEFAULT,
         "ALL_DB 크롤링 가능 판례 목록을 수집·저장(--resume 로 이어서)"),

    # 자료 넣기
    Task("pdf-ingest", "scripts.pdf_ingest.pdf_to_mongo", "자료 넣기", WRITES_DEFAULT,
         "맥미니 ~/loum/pdf_db 의 판례·행정해석·결정례 PDF 를 파싱해 넣기(--dry-run 로 미리 보기)"),
    Task("pdf-reparse", "scripts.pdf_ingest.reparse", "자료 넣기", DRY_RUN_DEFAULT,
         "PDF 로 넣은 판례·행정해석·결정례를 지금 파서로 다시 파싱(--apply 로 저장)"),
    Task("csv-import", "scripts.csv_import.run", "자료 넣기", DRY_RUN_DEFAULT,
         "판례·행정해석·판정사례 CSV 넣기(10-25 완료분, --apply 로 저장)"),

    # 데이터 정비(대부분 완료된 일회성 — 다시 필요할 때만)
    Task("fix:clean-pdf-artifacts", "scripts.migrations.backfill_clean_pdf_artifacts", "데이터 정비", DRY_RUN_DEFAULT,
         "PDF·인쇄 흔적(푸터·페이지 표시) 제거"),
    Task("fix:judgment-case-numbers", "scripts.migrations.backfill_judgment_case_numbers", "데이터 정비", DRY_RUN_DEFAULT,
         "주요판정사례 빈 사건번호(sub_title) 복구"),
    Task("fix:source-provenance", "scripts.migrations.backfill_source_provenance", "데이터 정비", DRY_RUN_DEFAULT,
         "인용 출처 필드 점검·보강"),
    Task("fix:stable-ids", "scripts.migrations.backfill_stable_ids", "데이터 정비", DRY_RUN_DEFAULT,
         "법령·행정규칙 안정 ID 정규화(--execute 로 저장)"),
    Task("fix:chunk-identifiers", "scripts.migrations.migrate_chunk_identifiers", "데이터 정비", WRITES_DEFAULT,
         "청크 식별자 보강(--dry-run 로 미리 보기)"),
    Task("fix:constitutional-sub-titles", "scripts.migrations.migrate_constitutional_sub_titles", "데이터 정비",
         DRY_RUN_DEFAULT, "헌재결정례 sub_title 복구"),
    Task("fix:reference-sub-titles", "scripts.migrations.migrate_reference_sub_titles", "데이터 정비", DRY_RUN_DEFAULT,
         "웹 참조용 sub_title 보강"),
    Task("fix:prune-interpretation", "scripts.migrations.prune_interpretation_to_ministry", "데이터 정비",
         WRITES_DEFAULT, "해석례를 고용노동부 관련만 남기고 삭제(--dry-run 로 미리 보기)"),
    Task("fix:prune-law", "scripts.migrations.prune_law_to_ministry", "데이터 정비", WRITES_DEFAULT,
         "법령을 고용노동부 소관만 남기고 삭제(--dry-run 로 미리 보기)"),
    Task("fix:rescrape-paragraphs", "scripts.migrations.rescrape_paragraphs", "데이터 정비", DRY_RUN_DEFAULT,
         "문단 없이 한 줄로 저장된 재결례·결정례·판례를 원문에서 다시 받아 문단 살리기"),
    Task("fix:retry-judgment-pdf", "scripts.migrations.retry_judgment_pdf_extraction", "데이터 정비", DRY_RUN_DEFAULT,
         "PDF 본문이 없는 주요판정사례 첨부 다시 추출"),
]

BY_NAME = {task.name: task for task in TASKS}
PREVIEW_FLAGS = {"--help", "-h", "--dry-run", "--no-db"}


def print_tasks() -> None:
    print(__doc__.split("\n\n")[0])
    group = None
    for task in TASKS:
        if task.group != group:
            group = task.group
            print(f"\n[{group}]")
        print(f"  {task.name:<32} {task.safety:<8} {task.summary}")
    print("\n자세한 설명: docs/manual-tasks.md")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "list"):
        print_tasks()
        return 0

    task = BY_NAME.get(argv[0])
    if task is None:
        print(f"없는 작업: {argv[0]} (python3 manual.py 로 목록 확인)", file=sys.stderr)
        return 2
    args = argv[1:]

    if task.safety == WRITES_DEFAULT and not PREVIEW_FLAGS.intersection(args):
        if "--yes" not in args:
            print(f"'{task.name}' 은 운영 MongoDB 에 쓰거나 지운다. 미리 보기로 확인한 뒤 --yes 를 붙여 다시 실행한다.",
                  file=sys.stderr)
            return 2
    args = [a for a in args if a != "--yes"]

    sys.argv = [task.module] + args
    runpy.run_module(task.module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
