import json
import pandas as pd
import argparse
import os
import sys

# 프로젝트 루트 경로 추가 (다른 유틸리티를 임포트할 경우를 대비)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__)

def convert_jsonl_to_csv(input_path: str, output_path: str):
    """
    JSONL 파일을 CSV 파일로 변환합니다.
    """
    try:
        logger.info(f"'{input_path}' 파일을 CSV로 변환 시작...")

        # JSONL 파일을 한 줄씩 읽어 리스트에 추가
        data = []
        with open(input_path, 'r', encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line))

        if not data:
            logger.warning(f"입력 파일 '{input_path}'에 데이터가 없어 변환을 건너뜁니다.")
            return

        # Pandas DataFrame으로 변환
        # json_normalize는 {"metadata": {"chapter": "..."}} 같은 중첩된 JSON을
        # 'metadata.chapter' 와 같은 단일 컬럼으로 보기 좋게 펼쳐줍니다.
        df = pd.json_normalize(data)

        # CSV 파일로 저장
        # utf-8-sig 인코딩은 Excel에서 한글이 깨지지 않도록 보장합니다.
        df.to_csv(output_path, index=False, encoding='utf-8-sig')

        logger.info(f"✅ 변환 완료! '{output_path}' 파일이 생성되었습니다.")

    except FileNotFoundError:
        logger.error(f"입력 파일 '{input_path}'을(를) 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"CSV 변환 중 에러 발생: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JSONL 파일을 CSV 파일로 변환합니다.")
    parser.add_argument("-i", "--input", required=True, help="입력할 JSONL 파일의 경로")
    parser.add_argument("-o", "--output", required=True, help="출력할 CSV 파일의 경로")
    args = parser.parse_args()

    convert_jsonl_to_csv(args.input, args.output)
