"""
변경 감지 및 버전 관리 유틸리티

기능:
- 문서 내용 변경 감지
- 스키마 검증
- 버전 업데이트 결정
- 변경사항 로깅
"""

import hashlib
from typing import Dict, Optional, Tuple
from datetime import datetime


class ChangeDetector:
    """문서 변경 감지 및 버전 관리"""
    
    @staticmethod
    def calculate_content_hash(content: str) -> str:
        """
        문서 내용의 SHA256 해시 계산
        
        Args:
            content: 문서 내용 (title + sub_title + content)
        
        Returns:
            SHA256 해시값 (16진수 문자열)
        """
        if not content:
            return ""
        
        content_str = content.strip()
        return hashlib.sha256(content_str.encode('utf-8')).hexdigest()
    
    @staticmethod
    def is_content_changed(old_doc: Dict, new_doc: Dict) -> bool:
        """
        문서 내용이 변경되었는지 판단
        
        Args:
            old_doc: 기존 MongoDB 문서
            new_doc: 새로운 문서
        
        Returns:
            True: 내용이 변경됨 (새 버전 필요)
            False: 내용이 동일함 (업데이트만 수행)
        """
        if not old_doc:
            return True  # 기존 문서 없으면 새 버전
        
        # 비교할 필드
        compare_fields = ['title', 'sub_title', 'content']
        
        for field in compare_fields:
            old_value = old_doc.get(field, '').strip()
            new_value = new_doc.get(field, '').strip()
            
            if old_value != new_value:
                return True  # 하나라도 다르면 변경됨
        
        return False  # 모두 동일하면 변경 없음
    
    @staticmethod
    def compare_documents(old_doc: Dict, new_doc: Dict) -> Dict:
        """
        두 문서를 상세 비교
        
        Args:
            old_doc: 기존 문서
            new_doc: 새로운 문서
        
        Returns:
            {
                "changed": bool,
                "changed_fields": [field names],
                "old_hash": old content hash,
                "new_hash": new content hash,
                "changes": {field: {"old": value, "new": value}}
            }
        """
        comparison = {
            "changed": False,
            "changed_fields": [],
            "changes": {}
        }
        
        # 해시 계산
        old_content = f"{old_doc.get('title', '')}{old_doc.get('sub_title', '')}{old_doc.get('content', '')}"
        new_content = f"{new_doc.get('title', '')}{new_doc.get('sub_title', '')}{new_doc.get('content', '')}"
        
        comparison["old_hash"] = ChangeDetector.calculate_content_hash(old_content)
        comparison["new_hash"] = ChangeDetector.calculate_content_hash(new_content)
        
        # 필드별 비교
        compare_fields = ['title', 'sub_title', 'content']
        
        for field in compare_fields:
            old_value = old_doc.get(field, '')
            new_value = new_doc.get(field, '')
            
            if old_value != new_value:
                comparison["changed"] = True
                comparison["changed_fields"].append(field)
                comparison["changes"][field] = {
                    "old": old_value[:100] if len(str(old_value)) > 100 else old_value,
                    "new": new_value[:100] if len(str(new_value)) > 100 else new_value
                }
        
        return comparison
    
    @staticmethod
    def needs_new_version(old_doc: Optional[Dict], new_doc: Dict, 
                         effective_date_changed: bool = False) -> bool:
        """
        새 버전이 필요한지 판단
        
        Args:
            old_doc: 기존 활성 문서 (None이면 신규)
            new_doc: 새로운 문서
            effective_date_changed: 효력일자가 변경되었는지 여부
        
        Returns:
            True: 새 버전 필요 (기존 버전 비활성화)
            False: 기존 버전 유지 (메타데이터만 업데이트)
        """
        # 기존 문서 없으면 새 버전
        if not old_doc:
            return True
        
        # 효력일자 변경되면 새 버전
        if effective_date_changed:
            return True
        
        # 내용 변경되면 새 버전
        if ChangeDetector.is_content_changed(old_doc, new_doc):
            return True
        
        # 모두 동일하면 기존 버전 유지
        return False


class VersionManager:
    """버전 관리 및 메타데이터 업데이트"""
    
    @staticmethod
    def prepare_version_update(current_doc: Dict, new_doc: Dict, 
                              comparison: Dict) -> Dict:
        """
        버전 업데이트를 위한 메타데이터 준비
        
        Args:
            current_doc: 현재 활성 문서
            new_doc: 새로운 문서
            comparison: 변경 비교 결과
        
        Returns:
            업데이트된 메타데이터
        """
        metadata = new_doc.get("metadata", {})
        
        # 버전 정보 추가
        version_info = {
            "current_version": current_doc.get("metadata", {}).get("version", 1) + 1
            if current_doc 
            else 1,
            "previous_version_id": current_doc.get("_id") if current_doc else None,
            "change_summary": {
                "fields_changed": comparison.get("changed_fields", []),
                "content_hash_changed": comparison.get("old_hash") != comparison.get("new_hash")
            },
            "updated_at": datetime.now().isoformat()
        }
        
        # 메타데이터에 버전 정보 추가
        metadata.update(version_info)
        
        return metadata
    
    @staticmethod
    def prepare_metadata_update(current_doc: Dict, new_doc: Dict) -> Dict:
        """
        메타데이터만 업데이트할 때 준비
        
        Args:
            current_doc: 현재 활성 문서
            new_doc: 새로운 문서
        
        Returns:
            업데이트할 메타데이터
        """
        metadata = new_doc.get("metadata", {})
        
        # 기존 버전 정보 유지
        if current_doc:
            metadata["version"] = current_doc.get("metadata", {}).get("version", 1)
            metadata["created_at"] = current_doc.get("metadata", {}).get("created_at")
        
        # 업데이트 타임스탬프만 갱신
        metadata["updated_at"] = datetime.now().isoformat()
        metadata["last_check_at"] = datetime.now().isoformat()
        
        return metadata


class ChangeLogger:
    """변경사항 로깅"""
    
    @staticmethod
    def log_change_detection(doc_id: str, comparison: Dict, logger) -> None:
        """변경 감지 결과 로깅"""
        if comparison["changed"]:
            logger.info(f"📝 문서 변경 감지: {doc_id}")
            for field in comparison["changed_fields"]:
                logger.info(f"   - {field}: 변경됨")
                if field in comparison["changes"]:
                    old_val = comparison["changes"][field]["old"]
                    new_val = comparison["changes"][field]["new"]
                    logger.debug(f"     OLD: {old_val}")
                    logger.debug(f"     NEW: {new_val}")
        else:
            logger.info(f"✅ 문서 내용 동일: {doc_id} (메타데이터만 업데이트)")
    
    @staticmethod
    def log_version_action(doc_id: str, action: str, details: Dict, logger) -> None:
        """버전 관리 액션 로깅"""
        if action == "new_version":
            logger.info(f"🆕 새 버전 생성: {doc_id}")
            logger.info(f"   - 이유: {details.get('reason', 'unknown')}")
            logger.info(f"   - 버전: {details.get('version_number', 'unknown')}")
            if details.get('previous_version_id'):
                logger.debug(f"   - 이전 버전 ID: {details['previous_version_id']}")
        
        elif action == "update_existing":
            logger.info(f"🔄 기존 버전 업데이트: {doc_id}")
            logger.info(f"   - 버전: {details.get('version_number', 'unknown')} (변경 없음)")
            logger.info(f"   - 메타데이터 업데이트: {', '.join(details.get('updated_fields', []))}")
