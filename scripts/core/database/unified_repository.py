"""
새로운 통합 Repository (문서 + 청크 통합)

doc_type: ["법령", "행정규칙", "행정해석", "판례"]
각 문서는 여러 청크를 포함 가능

변경 감지 및 버전 관리 기능:
- 첫 저장: 신규 문서로 저장 (is_active=true)
- 두 번째부터: 내용 변경 감지 → 새 버전 또는 메타데이터 업데이트
"""

from typing import Optional, List, Dict, Tuple
from datetime import datetime
import logging

from scripts.core.database.change_detector import ChangeDetector, VersionManager, ChangeLogger

logger = logging.getLogger(__name__)


class UnifiedDocumentRepository:
    """통합 문서 Repository (문서 + 청크 통합)"""
    
    COLLECTION_NAME = "law"
    
    def __init__(self, db, db_name=None, collection_name=None):
        """
        초기화
        
        인자:
        - db: MongoDB database 객체
        - db_name: 데이터베이스명 (기본값: original_db)
        - collection_name: 컬렉션명 (기본값: law)
        """
        self.db = db
        self.db_name = db_name or "original_db"
        self.collection_name = collection_name or "law"
        self.collection = db[self.collection_name]
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """인덱스 생성"""
        try:
            # doc_id + is_active 인덱스 (유니크 제약 없음)
            # 여러 버전이 존재할 수 있으므로 unique=False
            try:
                self.collection.create_index([
                    ("doc_id", 1),
                    ("metadata.is_active", 1)
                ], sparse=True)
            except Exception as idx_e:
                # 인덱스가 이미 존재하는 경우 무시
                if "IndexKeySpecsConflict" not in str(idx_e):
                    raise
                logger.debug(f"doc_id + is_active 인덱스는 이미 존재합니다.")
            
            # 검색용 인덱스
            self.collection.create_index([("doc_type", 1)])
            self.collection.create_index([("metadata.source_type", 1)])
            self.collection.create_index([("metadata.created_at", -1)])
            self.collection.create_index([("metadata.effective", 1)])
            
            # 텍스트 검색 인덱스
            self.collection.create_index([
                ("title", "text"),
                ("content", "text"),
                ("sub_title", "text")
            ])
            
            logger.info("✅ UnifiedDocumentRepository 인덱스 생성 완료")
            
        except Exception as e:
            logger.error(f"❌ 인덱스 생성 실패: {e}")
    
    def upsert(self, doc: Dict) -> str:
        """
        문서 저장 (upsert)
        
        인자:
        - doc: 문서 데이터
          {
            "doc_id": "...",
            "doc_type": "법령",
            "title": "...",
            "sub_title": "...",
            "content": "...",
            "metadata": {
              "source_url": "...",
              "source_type": "web",
              "effective": "2026-01-20",
              "created_at": "2026-01-20",
              "is_active": true
            }
          }
        
        반환:
        - doc_id
        """
        try:
            # 기존 데이터가 있으면 is_active를 false로 설정
            if doc.get("metadata", {}).get("is_active"):
                self.collection.update_many(
                    {
                        "doc_id": doc["doc_id"],
                        "metadata.is_active": True
                    },
                    {
                        "$set": {
                            "metadata.is_active": False,
                            "metadata.updated_at": datetime.utcnow()
                        }
                    }
                )
            
            # 새 데이터 저장
            result = self.collection.replace_one(
                {
                    "doc_id": doc["doc_id"],
                    "metadata.is_active": doc.get("metadata", {}).get("is_active", True)
                },
                doc,
                upsert=True
            )
            
            return doc["doc_id"]
            
        except Exception as e:
            logger.error(f"❌ 문서 저장 실패: {e}")
            raise
    
    def find_by_id(self, doc_id: str, active_only: bool = True) -> Optional[Dict]:
        """
        문서 조회 by doc_id
        
        인자:
        - doc_id: 문서 ID
        - active_only: 활성 문서만 조회 (기본값: True)
        
        반환:
        - 문서 데이터 또는 None
        """
        query = {"doc_id": doc_id}
        if active_only:
            query["metadata.is_active"] = True
        
        return self.collection.find_one(query)
    
    def find_by_article(self, doc_id: str, article_number, active_only: bool = True) -> Optional[Dict]:
        """doc_id + article_number 복합 키로 특정 조문 조회"""
        query = {"doc_id": doc_id, "article_number": article_number}
        if active_only:
            query["metadata.is_active"] = True
        return self.collection.find_one(query)

    def find_by_type(
        self,
        doc_type: str,
        active_only: bool = True,
        limit: int = 100
    ) -> List[Dict]:
        """
        문서 타입별 조회
        
        인자:
        - doc_type: 문서 타입 ("법령", "행정규칙", etc)
        - active_only: 활성 문서만 조회
        - limit: 최대 조회 수
        
        반환:
        - 문서 리스트
        """
        query = {"doc_type": doc_type}
        if active_only:
            query["metadata.is_active"] = True
        
        return list(self.collection.find(query).limit(limit))
    
    def find_by_effective_date(
        self,
        effective_date: str,
        doc_type: Optional[str] = None
    ) -> List[Dict]:
        """
        효력일자별 조회
        
        인자:
        - effective_date: 효력일자 (YYYY-MM-DD)
        - doc_type: 문서 타입 (선택사항)
        
        반환:
        - 문서 리스트
        """
        query = {
            "metadata.effective": effective_date,
            "metadata.is_active": True
        }
        if doc_type:
            query["doc_type"] = doc_type
        
        return list(self.collection.find(query))
    
    def find_by_source_url(self, source_url: str) -> List[Dict]:
        """
        소스 URL별 조회 (모든 버전)
        
        인자:
        - source_url: 소스 URL
        
        반환:
        - 문서 리스트 (버전 포함)
        """
        return list(
            self.collection.find({"metadata.source_url": source_url})
            .sort("metadata.created_at", -1)
        )
    
    def count_by_type(self, doc_type: str, active_only: bool = True) -> int:
        """문서 타입별 개수"""
        query = {"doc_type": doc_type}
        if active_only:
            query["metadata.is_active"] = True
        return self.collection.count_documents(query)
    
    def deactivate(self, doc_id: str) -> bool:
        """문서 비활성화"""
        try:
            result = self.collection.update_one(
                {"doc_id": doc_id, "metadata.is_active": True},
                {
                    "$set": {
                        "metadata.is_active": False,
                        "metadata.updated_at": datetime.utcnow()
                    }
                }
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ 문서 비활성화 실패: {e}")
            return False
    
    def text_search(self, query: str, limit: int = 50) -> List[Dict]:
        """
        텍스트 검색
        
        인자:
        - query: 검색어
        - limit: 최대 결과 수
        
        반환:
        - 검색 결과
        """
        return list(
            self.collection.find(
                {
                    "$text": {"$search": query},
                    "metadata.is_active": True
                },
                {"score": {"$meta": "textScore"}}
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
    
    def upsert_with_change_detection(self, new_doc: Dict) -> Tuple[str, Dict]:
        """
        변경 감지 기능이 포함된 upsert
        
        첫 저장: 신규 문서
        두 번째부터: 내용 비교 → 새 버전 또는 메타데이터 업데이트
        
        인자:
        - new_doc: 새로운 문서
        
        반환:
        - (doc_id, action_result)
          {
            "action": "new_version" | "update_existing" | "no_change",
            "doc_id": "...",
            "version": 1 | 2 | ...,
            "changed": bool,
            "changed_fields": [...],
            "timestamp": ISO datetime
          }
        """
        try:
            doc_id = new_doc.get("doc_id")
            article_number = new_doc.get("article_number")
            
            # 1. 기존 활성 문서 조회 (article_number 있으면 복합키, 없으면 doc_id만)
            if article_number is not None:
                existing_doc = self.find_by_article(doc_id, article_number, active_only=True)
            else:
                existing_doc = self.find_by_id(doc_id, active_only=True)
            
            # 2. 변경 감지
            comparison = ChangeDetector.compare_documents(existing_doc or {}, new_doc)
            
            # 로깅
            ChangeLogger.log_change_detection(doc_id, comparison, logger)
            
            # 3. 새 버전 필요 여부 판단
            new_effective = new_doc.get("metadata", {}).get("effective") or ""
            old_effective = (existing_doc or {}).get("metadata", {}).get("effective") or ""
            # 새 문서 시행일자가 없거나 sentinel이면 날짜 변경으로 판단하지 않음
            effective_date_changed = (
                existing_doc and
                new_effective not in ("", "99991231") and
                old_effective != new_effective
            )
            
            needs_new_version = ChangeDetector.needs_new_version(
                existing_doc, 
                new_doc, 
                effective_date_changed
            )
            
            # 4. 메타데이터 준비
            if needs_new_version:
                # 새 버전 생성
                metadata = VersionManager.prepare_version_update(
                    existing_doc, 
                    new_doc, 
                    comparison
                )
                new_doc["metadata"] = metadata
                
                # 기존 버전 비활성화
                if existing_doc:
                    self.collection.update_one(
                        {"_id": existing_doc["_id"]},
                        {
                            "$set": {
                                "metadata.is_active": False,
                                "metadata.updated_at": datetime.now().isoformat()
                            }
                        }
                    )
                
                # 새 문서 삽입
                result = self.collection.insert_one(new_doc)
                
                # 첫 저장인지 버전 생성인지 구분
                action = "insert" if not existing_doc else "new_version"
                version_number = metadata.get("current_version", 1)
                
                ChangeLogger.log_version_action(
                    doc_id,
                    action,
                    {
                        "reason": "내용 변경" if comparison["changed"] else "효력일자 변경",
                        "version_number": version_number,
                        "previous_version_id": metadata.get("previous_version_id")
                    },
                    logger
                )
            else:
                # 기존 문서 메타데이터만 업데이트
                metadata = VersionManager.prepare_metadata_update(existing_doc, new_doc)
                metadata["last_check_at"] = datetime.now().isoformat()
                
                # 메타데이터만 업데이트 (metadata 필드 안의 각 필드를 개별 업데이트)
                update_dict = {}
                for key, value in metadata.items():
                    update_dict[f"metadata.{key}"] = value
                
                update_result = self.collection.update_one(
                    {"_id": existing_doc["_id"]},
                    {"$set": update_dict}
                )
                
                action = "update_existing"
                version_number = metadata.get("version", 1)
                
                ChangeLogger.log_version_action(
                    doc_id,
                    action,
                    {
                        "version_number": version_number,
                        "updated_fields": ["metadata"]
                    },
                    logger
                )
            
            # 5. 결과 반환
            return doc_id, {
                "action": action,
                "doc_id": doc_id,
                "version": version_number,
                "changed": comparison["changed"],
                "changed_fields": comparison.get("changed_fields", []),
                "content_hash_changed": comparison.get("old_hash") != comparison.get("new_hash"),
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ 변경 감지 upsert 실패 ({doc_id}): {e}", exc_info=True)
            raise
