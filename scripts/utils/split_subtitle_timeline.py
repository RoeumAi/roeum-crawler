import json
import re
import argparse
import os
import sys

# 프로젝트 루트 경로 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__)

def split_subtitle_and_timeline(input_path: str, output_path: str = None):
    """
    JSONL 파일의 subtitle 필드를 분리하여 timeline과 subtitle로 나눕니다.
    
    예시:
    - 입력: "[시행 2022. 7. 19.] [고용노동부고시 제2022-66호, 2022. 7. 19., 일부개정]"
    - 출력: timeline = "시행 2022. 7. 19.", subtitle = "고용노동부고시 제2022-66호, 2022. 7. 19., 일부개정"
    
    Args:
        input_path: 입력 JSONL 파일 경로
        output_path: 출력 JSONL 파일 경로 (None일 경우 입력 파일명_modified.jsonl로 저장)
    """
    try:
        logger.info(f"'{input_path}' 파일 처리 시작...")
        
        # 출력 파일 경로 설정
        if output_path is None:
            base_name = os.path.splitext(input_path)[0]
            output_path = f"{base_name}_modified.jsonl"
        
        processed_count = 0
        skipped_count = 0
        
        with open(input_path, 'r', encoding='utf-8') as f_in, \
             open(output_path, 'w', encoding='utf-8') as f_out:
            
            for line_num, line in enumerate(f_in, 1):
                try:
                    data = json.loads(line.strip())
                    subtitle = data.get('subtitle', '')
                    
                    if not subtitle:
                        logger.warning(f"라인 {line_num}: subtitle 필드가 비어있습니다.")
                        f_out.write(json.dumps(data, ensure_ascii=False) + '\n')
                        skipped_count += 1
                        continue
                    
                    # 정규식으로 대괄호 내용 추출
                    # \[([^\]]+)\] 패턴으로 대괄호 안의 내용을 모두 찾음
                    matches = re.findall(r'\[([^\]]+)\]', subtitle)
                    
                    if len(matches) >= 2:
                        # 첫 번째 대괄호 내용을 timeline으로
                        data['timeline'] = matches[0].strip()
                        
                        # 나머지 대괄호 내용들을 subtitle로 (줄바꿈으로 구분된 경우도 처리)
                        # 두 번째부터 모든 내용을 합침
                        data['subtitle'] = ' \n '.join([m.strip() for m in matches[1:]])
                        
                        processed_count += 1
                    else:
                        logger.warning(f"라인 {line_num}: subtitle에 대괄호가 2개 미만입니다. ({subtitle[:50]}...)")
                        skipped_count += 1
                    
                    # 수정된 데이터 저장
                    f_out.write(json.dumps(data, ensure_ascii=False) + '\n')
                    
                except json.JSONDecodeError as e:
                    logger.error(f"라인 {line_num}: JSON 파싱 오류 - {e}")
                    skipped_count += 1
                except Exception as e:
                    logger.error(f"라인 {line_num}: 처리 중 오류 - {e}")
                    skipped_count += 1
        
        logger.info(f"✅ 처리 완료!")
        logger.info(f"  - 성공: {processed_count}건")
        logger.info(f"  - 건너뜀: {skipped_count}건")
        logger.info(f"  - 출력 파일: '{output_path}'")
        
        return output_path
        
    except FileNotFoundError:
        logger.error(f"입력 파일 '{input_path}'을(를) 찾을 수 없습니다.")
        raise
    except Exception as e:
        logger.error(f"처리 중 에러 발생: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="JSONL 파일의 subtitle을 timeline과 subtitle로 분리합니다."
    )
    parser.add_argument(
        'input_path',
        type=str,
        help='입력 JSONL 파일 경로'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='출력 JSONL 파일 경로 (기본값: 입력파일명_modified.jsonl)'
    )
    
    args = parser.parse_args()
    
    split_subtitle_and_timeline(args.input_path, args.output)
