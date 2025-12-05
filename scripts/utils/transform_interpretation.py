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

def transform_interpretation_data(input_path: str, output_path: str = None):
    """
    interpretation JSONL 파일을 변환합니다:
    1. doc_title을 title로 변경
    2. doc_number와 doc_date를 합쳐서 subtitle로 생성
    3. doc_number나 doc_date가 "정보 없음"인 경우 "{title} 세부 행정규칙"으로 생성
    4. department 필드 제거
    5. 필드 순서를 doc_id, title, subtitle, ... 순으로 정렬
    6. source_url을 새로운 형식으로 변환 (cgmExpcSeq → cgmExpcDatSeq)
    
    Args:
        input_path: 입력 JSONL 파일 경로
        output_path: 출력 JSONL 파일 경로 (None일 경우 입력 파일명_transformed.jsonl로 저장)
    """
    try:
        logger.info(f"'{input_path}' 파일 처리 시작...")
        
        # 출력 파일 경로 설정
        if output_path is None:
            base_name = os.path.splitext(input_path)[0]
            output_path = f"{base_name}_transformed.jsonl"
        
        processed_count = 0
        
        with open(input_path, 'r', encoding='utf-8') as f_in, \
             open(output_path, 'w', encoding='utf-8') as f_out:
            
            for line_num, line in enumerate(f_in, 1):
                try:
                    data = json.loads(line.strip())
                    
                    # doc_title을 title로 변경
                    if 'doc_title' in data:
                        data['title'] = data.pop('doc_title')
                    
                    title = data.get('title', '').strip()
                    doc_number = data.get('doc_number', '').strip()
                    doc_date = data.get('doc_date', '').strip()
                    
                    # subtitle 생성
                    if doc_number == "정보 없음" or doc_date == "정보 없음" or not doc_number or not doc_date:
                        # 정보 없음이거나 비어있는 경우
                        if title:
                            subtitle = f'{title} 세부 행정규칙'
                        else:
                            subtitle = '세부 행정규칙'
                        logger.debug(f"라인 {line_num}: 정보 없음으로 인해 title 기반 subtitle 생성")
                    else:
                        # doc_number와 doc_date를 합쳐서 subtitle 생성
                        subtitle = f'{doc_number} {doc_date}'
                        logger.debug(f"라인 {line_num}: doc_number와 doc_date를 합쳐 subtitle 생성")
                    
                    # department 제거
                    if 'department' in data:
                        del data['department']
                    
                    # source_url 변환
                    source_url = data.get('source_url', '')
                    if source_url:
                        # cgmExpcSeq= 패턴에서 docId 추출
                        match = re.search(r'cgmExpcSeq=(\d+)', source_url)
                        if match:
                            doc_id_from_url = match.group(1)
                            # 새로운 URL 형식으로 변환
                            data['source_url'] = f'https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcDatSeq={doc_id_from_url}&mode=2&ofiClsCd=350101'
                            logger.debug(f"라인 {line_num}: source_url 변환 완료")
                    
                    # 필드 순서를 재정렬 (doc_id, title, subtitle, ...)
                    ordered_data = {
                        'doc_id': data.get('doc_id', ''),
                        'title': title,
                        'subtitle': subtitle
                    }
                    
                    # 나머지 필드 추가 (doc_id, title, subtitle, department 제외)
                    for key, value in data.items():
                        if key not in ['doc_id', 'title', 'subtitle', 'department']:
                            ordered_data[key] = value
                    
                    processed_count += 1
                    
                    # 수정된 데이터 저장
                    f_out.write(json.dumps(ordered_data, ensure_ascii=False) + '\n')
                    
                except json.JSONDecodeError as e:
                    logger.error(f"라인 {line_num}: JSON 파싱 오류 - {e}")
                except Exception as e:
                    logger.error(f"라인 {line_num}: 처리 중 오류 - {e}")
        
        logger.info(f"✅ 처리 완료!")
        logger.info(f"  - 처리됨: {processed_count}건")
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
        description="interpretation JSONL 파일을 변환합니다 (doc_title→title, doc_number+doc_date→subtitle)."
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
        help='출력 JSONL 파일 경로 (기본값: 입력파일명_transformed.jsonl)'
    )
    
    args = parser.parse_args()
    
    transform_interpretation_data(args.input_path, args.output)
