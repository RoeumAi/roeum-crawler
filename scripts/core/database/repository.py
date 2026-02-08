"""
MongoDB 저장소 (Repository) - JSONL 데이터를 MongoDB에 저장

기본 CRUD: insert, find, update, upsert
"""
import json
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DocumentRepository:
    """Document 저장소"""
    
    def __init__(self, db):
        self.db = db
        self.collection = db['documents']
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """필수 인덱스 생성 (처음 한 번만)"""
        try:
            # doc_id는 고유값 (중복 방지)
            self.collection.create_index([("doc_id", 1)], unique=True, sparse=True)
            # 소스 타입별 조회 빠르게
            self.collection.create_index([("source_type", 1)])
            # 부처별 조회
            self.collection.create_index([("dept_code", 1)])
            # 최근 크롤링 시간으로 정렬
            self.collection.create_index([("crawled_at", -1)])
            logger.info("✅ Document 인덱스 생성 완료")
        except Exception as e:
            logger.debug(f"인덱스 생성 (이미 존재할 수 있음): {e}")
    
    def insert(self, doc_data: Dict) -> str:
        """새 문서 삽입"""
        try:
            result = self.collection.insert_one(doc_data)
            logger.debug(f"✅ 신규 문서 삽입: {doc_data.get('doc_id')}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"❌ 삽입 실패: {e}")
            raise
    
    def find_by_id(self, doc_id: str) -> Optional[Dict]:
        """ID로 문서 조회"""
        return self.collection.find_one({"doc_id": doc_id})
    
    def upsert(self, doc_data: Dict) -> str:
        """INSERT 또는 UPDATE (기존이면 update, 없으면 insert)"""
        doc_id = doc_data.get('doc_id')
        
        if not doc_id:
            raise ValueError("doc_id가 필수입니다")
        
        try:
            # replace_one: 기존 문서를 완전히 덮어씀
            result = self.collection.replace_one(
                {"doc_id": doc_id},
                doc_data,
                upsert=True  # 없으면 insert, 있으면 update
            )
            
            if result.matched_count:
                logger.debug(f"✅ 문서 업데이트: {doc_id}")
            else:
                logger.debug(f"✅ 신규 문서 삽입: {doc_id}")
            
            return doc_id
        except Exception as e:
            logger.error(f"❌ Upsert 실패: {e}")
            raise
    
    def find_by_type(self, source_type: str, dept_code: str = None) -> List[Dict]:
        """타입별 문서 조회"""
        query = {"source_type": source_type}
        if dept_code:
            query["dept_code"] = dept_code
        
        return list(self.collection.find(query))
    
    def count_by_type(self, source_type: str) -> int:
        """타입별 문서 개수"""
        return self.collection.count_documents({"source_type": source_type})


class ChunkRepository:
    """Chunk (청크) 저장소"""
    
    def __init__(self, db):
        self.db = db
        self.collection = db['chunks']
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """필수 인덱스 생성"""
        try:
            # chunk_id는 고유값
            self.collection.create_index([("chunk_id", 1)], unique=True, sparse=True)
            # 문서별 청크 조회
            self.collection.create_index([("doc_id", 1)])
            # 청크 번호로 정렬
            self.collection.create_index([("doc_id", 1), ("chunk_no", 1)])
            logger.info("✅ Chunk 인덱스 생성 완료")
        except Exception as e:
            logger.debug(f"인덱스 생성 (이미 존재할 수 있음): {e}")
    
    def insert(self, chunk_data: Dict) -> str:
        """새 청크 삽입"""
        try:
            result = self.collection.insert_one(chunk_data)
            logger.debug(f"✅ 청크 삽입: {chunk_data.get('chunk_id')}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"❌ 청크 삽입 실패: {e}")
            raise
    
    def upsert(self, chunk_data: Dict) -> str:
        """청크 INSERT 또는 UPDATE"""
        chunk_id = chunk_data.get('chunk_id')
        
        if not chunk_id:
            raise ValueError("chunk_id가 필수입니다")
        
        try:
            result = self.collection.replace_one(
                {"chunk_id": chunk_id},
                chunk_data,
                upsert=True
            )
            return chunk_id
        except Exception as e:
            logger.error(f"❌ 청크 upsert 실패: {e}")
            raise
    
    def find_by_doc_id(self, doc_id: str) -> List[Dict]:
        """문서의 모든 청크 조회"""
        return list(self.collection.find(
            {"doc_id": doc_id},
            sort=[("chunk_no", 1)]  # 청크 번호 순서대로
        ))
    
    def count_by_doc_id(self, doc_id: str) -> int:
        """문서의 청크 개수"""
        return self.collection.count_documents({"doc_id": doc_id})
