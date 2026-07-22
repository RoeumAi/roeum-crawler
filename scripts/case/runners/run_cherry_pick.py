"""
ALL_DB 크롤링가능 판례 321건 cherry-pick 크롤러
- DRF 스캔으로 얻은 precSeq 목록을 읽어 순서대로 크롤링 → MongoDB 저장
- 사용법: python3 scripts/case/runners/run_cherry_pick.py [--resume N]
"""
import asyncio, argparse, json, os, sys, re
from pathlib import Path

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(project_root)

from scripts.case.logic.scraper import scrape_and_save
from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='case')

SCAN_RESULTS = "/tmp/scan_results.json"
BASE_URL     = "https://www.law.go.kr/LSW/precInfoP.do?precSeq={precSeq}"
OUTPUT_DIR   = os.path.join(project_root, "data", "output", "case", "cherry_pick")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", type=int, default=0, help="이 인덱스부터 재개")
    parser.add_argument("--batch", type=int, default=5, help="동시 처리 수")
    args = parser.parse_args()

    with open(SCAN_RESULTS, encoding="utf-8") as f:
        data = json.load(f)

    found = data["found"]
    logger.info(f"총 {len(found)}건 크롤링 대상 (resume={args.resume}부터)")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = {"success": [], "fail": []}
    targets = found[args.resume:]

    for i, item in enumerate(targets, start=args.resume):
        prec_seq = item["precSeq"]
        case_num = item["case_num"]
        url      = BASE_URL.format(precSeq=prec_seq)
        safe_name = re.sub(r'[\\/*?:"<>| ]', "_", case_num)

        try:
            ok = await scrape_and_save(
                url, OUTPUT_DIR, safe_name,
                process_chunks=True, save_to_db=True, save_jsonl=False,
            )
            if ok:
                results["success"].append(prec_seq)
            else:
                results["fail"].append(prec_seq)
        except Exception as e:
            logger.error(f"[{i+1}] {case_num} ({prec_seq}) 오류: {e}")
            results["fail"].append(prec_seq)

        if (i + 1) % 20 == 0:
            logger.info(f"진행: {i+1}/{len(found)} | 성공 {len(results['success'])} / 실패 {len(results['fail'])}")

    logger.info(f"\n=== 완료 ===")
    logger.info(f"성공: {len(results['success'])}건")
    logger.info(f"실패: {len(results['fail'])}건")
    if results["fail"]:
        logger.info(f"실패 목록: {results['fail'][:20]}")


if __name__ == "__main__":
    asyncio.run(main())
