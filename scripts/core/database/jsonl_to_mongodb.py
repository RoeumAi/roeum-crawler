"""
JSONL → MongoDB 변환 유틸리티

JSONL 파일을 읽어서 MongoDB에 자동으로 저장하는 도구
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional
import logging

from scripts.core.database.mongo_client import get_mongo_db
from scripts.core.database.repository import DocumentRepository, ChunkRepository

logger = logging.getLogger(__name__)


def jsonl_to_mongodb(
    jsonl_file_path: str,
    source_type: str,
    dept_code: Optional[str] = None,
    is_chunk: bool = False
) -> int:
    """
    JSONL 파일을 읽어서 MongoDB에 저장
    
    인자:
    - jsonl_file_path: JSONL 파일 경로
    - source_type: 소스 타입 (law, case, adrule, interpretation)
    - dept_code: 부처 코드 (선택사항)
    - is_chunk: 청크 데이터인지 여부
    
    반환:
    - 저장된 항목 수
    """
    try:
        db = get_mongo_db()
        
        if is_chunk:
            repo = ChunkRepository(db)
        else:
            repo = DocumentRepository(db)
        
        count = 0
        jsonl_path = Path(jsonl_file_path)
        
        if not jsonl_path.exists():
            logger.warning(f"파일 없음: {jsonl_file_path}")
            return 0
        
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 메타데이터 추가
                    data['source_type'] = source_type
                    if dept_code:
                        data['dept_code'] = dept_code
                    data['crawled_at'] = datetime.utcnow()
                    data['updated_at'] = datetime.utcnow()
                    
                    # MongoDB에 저장 (upsert)
                    repo.upsert(data)
                    count += 1
                    
                except json.JSONDecodeError as e:
                    logger.error(f"줄 {line_num} JSON 파싱 오류: {e}")
                except Exception as e:
                    logger.error(f"줄 {line_num} 저장 오류: {e}")
        
        logger.info(f"✅ {count}개 항목을 MongoDB에 저장했습니다. ({jsonl_file_path})")
        return count
        
    except Exception as e:
        logger.error(f"❌ JSONL → MongoDB 변환 실패: {e}")
        return 0


def batch_jsonl_to_mongodb(
    output_dir: str,
    source_type: str,
    dept_code: Optional[str] = None
) -> dict:
    """
    디렉토리의 모든 JSONL 파일을 MongoDB에 저장
    
    인자:
    - output_dir: JSONL 파일들이 있는 디렉토리
    - source_type: 소스 타입
    - dept_code: 부처 코드
    
    반환:
    - {'documents': 문서 수, 'chunks': 청크 수}
    """
    output_path = Path(output_dir)
    results = {'documents': 0, 'chunks': 0}
    
    # 문서 파일 찾기
    doc_files = list(output_path.glob('*_document.jsonl'))
    chunk_files = list(output_path.glob('*_chunks.jsonl'))
    
    # 문서 저장
    for doc_file in doc_files:
        count = jsonl_to_mongodb(
            str(doc_file),
            source_type=source_type,
            dept_code=dept_code,
            is_chunk=False
        )
        results['documents'] += count
    
    # 청크 저장
    for chunk_file in chunk_files:
        count = jsonl_to_mongodb(
            str(chunk_file),
            source_type=source_type,
            dept_code=dept_code,
            is_chunk=True
        )
        results['chunks'] += count
    
    return results


# CLI 사용 예시
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("사용법: python jsonl_to_mongodb.py <output_dir> [source_type] [dept_code]")
        print("예시: python jsonl_to_mongodb.py data/output law moleg")
        sys.exit(1)
    
    output_dir = sys.argv[1]
    source_type = sys.argv[2] if len(sys.argv) > 2 else "law"
    dept_code = sys.argv[3] if len(sys.argv) > 3 else None
    
    results = batch_jsonl_to_mongodb(output_dir, source_type, dept_code)
    print(f"\n✅ 완료!")
    print(f"   문서: {results['documents']}개")
    print(f"   청크: {results['chunks']}개")
