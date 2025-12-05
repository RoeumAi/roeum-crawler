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

def clean_case_subtitle(input_path: str, output_path: str = None):
    """
    JSONL 파일의 subtitle 필드를 정리합니다:
    1. "본 컨텐츠는\\n 근로복지공단 산재판례 에서 수집한 데이터로..." 형태를 "근로복지공단 산재판례"로 변경
    2. subtitle이 비어있는 경우 "{title} 관련 세부 판례"로 생성
    3. subtitle의 대괄호 [] 제거
    
    Args:
        input_path: 입력 JSONL 파일 경로
        output_path: 출력 JSONL 파일 경로 (None일 경우 입력 파일명_cleaned.jsonl로 저장)
    """
    try:
        logger.info(f"'{input_path}' 파일 처리 시작...")
        
        # 출력 파일 경로 설정
        if output_path is None:
            base_name = os.path.splitext(input_path)[0]
            output_path = f"{base_name}_cleaned.jsonl"
        
        processed_count = 0
        skipped_count = 0
        
        # 매칭할 패턴들
        pattern1 = re.compile(r'본 컨텐츠는\s*\n\s*근로복지공단 산재판례\s*에서 수집한 데이터로.*', re.DOTALL)
        pattern2 = re.compile(r'본 컨텐츠는.*근로복지공단 산재판례.*수집한 데이터로.*', re.DOTALL)
        
        with open(input_path, 'r', encoding='utf-8') as f_in, \
             open(output_path, 'w', encoding='utf-8') as f_out:
            
            for line_num, line in enumerate(f_in, 1):
                try:
                    data = json.loads(line.strip())
                    subtitle = data.get('subtitle', '').strip()
                    title = data.get('title', '').strip()
                    
                    # subtitle이 비어있는 경우 title 기반으로 생성
                    if not subtitle:
                        if title:
                            data['subtitle'] = f'{title} 관련 세부 판례'
                            processed_count += 1
                            logger.debug(f"라인 {line_num}: 빈 subtitle을 title 기반으로 생성")
                        else:
                            skipped_count += 1
                        f_out.write(json.dumps(data, ensure_ascii=False) + '\n')
                        continue
                    
                    # 패턴 매칭 및 교체
                    original_subtitle = subtitle
                    modified = False
                    
                    # 패턴에 맞는 경우 교체
                    if pattern1.search(subtitle) or pattern2.search(subtitle):
                        data['subtitle'] = '근로복지공단 산재판례'
                        modified = True
                        logger.debug(f"라인 {line_num}: subtitle 변경 완료")
                    
                    # 대괄호 제거
                    if subtitle.startswith('[') and subtitle.endswith(']'):
                        data['subtitle'] = subtitle[1:-1]
                        modified = True
                        logger.debug(f"라인 {line_num}: 대괄호 제거")
                    
                    if modified:
                        processed_count += 1
                    else:
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
        logger.info(f"  - 변경/생성됨: {processed_count}건")
        logger.info(f"  - 변경 안 됨: {skipped_count}건")
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
        description="JSONL 파일의 subtitle을 정리합니다 (빈 값은 title 기반 생성, 특정 패턴 교체, 대괄호 제거)."
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
        help='출력 JSONL 파일 경로 (기본값: 입력파일명_cleaned.jsonl)'
    )
    
    args = parser.parse_args()
    
    clean_case_subtitle(args.input_path, args.output)
