"""
기본 스크래퍼 클래스 (BaseScraper)

모든 스크래퍼가 상속받아야 하는 기본 클래스
JSONL 저장, MongoDB 저장, 로깅 등 공통 기능 제공
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import json
import os
import logging

from scripts.core.database.mongo_client import get_mongo_db
from scripts.core.database.repository import DocumentRepository, ChunkRepository


class BaseScraper(ABC):
    """모든 스크래퍼의 기본 클래스"""
    
    def __init__(self, scraper_type: str, logger: logging.Logger):
        """
        초기화
        
        인자:
        - scraper_type: scraper 타입 (law, case, adrule, interpretation)
        - logger: 로거 객체
        """
        self.scraper_type = scraper_type
        self.logger = logger
    
    def save_to_file(self, data: dict | list, filename: str) -> bool:
        """JSONL 파일로 저장"""
        try:
            if not isinstance(data, list):
                data = [data]
            
            directory = os.path.dirname(filename)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            with open(filename, 'w', encoding='utf-8') as f:
                for item in data:
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
            
            self.logger.info(f"✅ JSONL 저장: {len(data)}건 → {filename}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ JSONL 저장 실패: {e}")
            return False
    
    def save_to_mongodb(
        self,
        doc_data: Dict,
        chunks: List[Dict],
        dept_code: Optional[str] = None
    ) -> bool:
        """MongoDB에 저장"""
        try:
            db = get_mongo_db()
            doc_repo = DocumentRepository(db)
            chunk_repo = ChunkRepository(db)
            
            # 1. 문서에 메타데이터 추가
            doc_data['source_type'] = self.scraper_type
            doc_data['dept_code'] = dept_code
            doc_data['crawled_at'] = datetime.utcnow()
            doc_data['updated_at'] = datetime.utcnow()
            doc_data['status'] = 'active'
            
            # 2. 문서 저장 (upsert)
            doc_repo.upsert(doc_data)
            self.logger.info(f"✅ MongoDB 저장: 문서 {doc_data.get('doc_id', 'unknown')}")
            
            # 3. 청크 저장
            for i, chunk in enumerate(chunks):
                chunk['source_type'] = self.scraper_type
                chunk['dept_code'] = dept_code
                chunk['chunk_no'] = i + 1
                chunk['crawled_at'] = datetime.utcnow()
                chunk['updated_at'] = datetime.utcnow()
                chunk_repo.upsert(chunk)
            
            self.logger.info(f"✅ MongoDB 저장: 청크 {len(chunks)}개")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ MongoDB 저장 실패: {e}")
            return False
    
    def save_both(
        self,
        doc_data: Dict,
        chunks: List[Dict],
        output_dir: str,
        output_name: str,
        dept_code: Optional[str] = None
    ) -> Tuple[bool, bool]:
        """
        JSONL과 MongoDB 모두에 저장
        
        반환:
        - (jsonl_success, mongodb_success)
        """
        # 1. JSONL 저장
        doc_filename = os.path.join(output_dir, f'{output_name}_document.jsonl')
        chunk_filename = os.path.join(output_dir, f'{output_name}_chunks.jsonl')
        
        jsonl_ok = True
        if doc_data.get("title"):
            self.save_to_file(doc_data, doc_filename)
        else:
            self.logger.warning("문서 제목이 없어 JSONL 저장 스킵")
            jsonl_ok = False
        
        if chunks:
            self.save_to_file(chunks, chunk_filename)
        else:
            self.logger.warning("청크 데이터가 없음")
        
        # 2. MongoDB 저장
        mongodb_ok = self.save_to_mongodb(doc_data, chunks, dept_code)
        
        return jsonl_ok, mongodb_ok
