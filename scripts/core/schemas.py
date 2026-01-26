"""
Scraper Flow 데이터 스키마 정의

모든 scraper의 입출력 데이터 형식을 Pydantic BaseModel로 정의하여
타입 안정성과 검증을 제공합니다.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime


# ============================================================================
# URL 수집 관련 스키마
# ============================================================================

class URLItem(BaseModel):
    """수집된 단일 URL 항목"""
    name: str = Field(..., description="문서명")
    url: str = Field(..., description="상세 페이지 URL")


class URLCollection(BaseModel):
    """URL 수집 결과"""
    status: str = Field(..., description="상태: success 또는 failed")
    urls_count: int = Field(default=0, description="수집된 URL 개수")
    urls: List[URLItem] = Field(default_factory=list, description="URL 목록")
    error: Optional[str] = Field(default=None, description="에러 메시지")
    timestamp: datetime = Field(default_factory=datetime.now, description="수집 시간")
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ============================================================================
# 문서 스크래핑 관련 스키마
# ============================================================================

class DocumentMetadata(BaseModel):
    """문서 메타데이터"""
    source_url: str = Field(..., description="원본 URL")
    source_type: str = Field(default="web", description="소스 타입")
    doc_type: str = Field(..., description="문서 유형: 법령, 판례, 행정규칙 등")
    effective_date: Optional[str] = Field(default=None, description="시행일자")
    created_at: str = Field(..., description="생성일시")
    is_active: bool = Field(default=True, description="활성 여부")
    extra: Dict[str, Any] = Field(default_factory=dict, description="추가 정보")


class DocumentChunk(BaseModel):
    """청크 기반 문서 조각 (판례 전용)"""
    section_key: str = Field(..., description="섹션 키: issue, summary, references 등")
    section_name: str = Field(..., description="섹션명: 판시사항, 판결요지 등")
    content: str = Field(..., description="섹션 내용")


class DocumentResult(BaseModel):
    """스크래핑 결과 (통합 스키마)"""
    status: str = Field(..., description="상태: success 또는 failed")
    doc_id: Optional[str] = Field(default=None, description="문서 ID")
    doc_type: str = Field(..., description="문서 유형")
    title: Optional[str] = Field(default=None, description="제목")
    subtitle: Optional[str] = Field(default=None, description="부제")
    
    # 일반 문서 (law, adrule)
    content: Optional[str] = Field(default=None, description="통합 내용 (law, adrule)")
    
    # 청크 기반 문서 (case)
    chunks: Optional[List[DocumentChunk]] = Field(default=None, description="청크 목록 (case)")
    
    # 메타데이터
    metadata: Optional[DocumentMetadata] = Field(default=None, description="메타데이터")
    
    # 에러 처리
    error: Optional[str] = Field(default=None, description="에러 메시지")
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class BatchScrapingResult(BaseModel):
    """배치 스크래핑 결과"""
    status: str = Field(..., description="상태: success 또는 failed")
    total_urls: int = Field(..., description="처리된 총 URL 수")
    successful: int = Field(..., description="성공한 문서 수")
    failed: int = Field(..., description="실패한 문서 수")
    errors: List[str] = Field(default_factory=list, description="에러 메시지 목록")
    timestamp: datetime = Field(default_factory=datetime.now, description="완료 시간")
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


# ============================================================================
# MongoDB 저장 관련 스키마
# ============================================================================

class MongoDBResult(BaseModel):
    """MongoDB 저장 결과"""
    status: str = Field(..., description="상태: success 또는 failed")
    inserted_count: int = Field(default=0, description="삽입된 문서 수")
    updated_count: int = Field(default=0, description="업데이트된 문서 수")
    error: Optional[str] = Field(default=None, description="에러 메시지")
    timestamp: datetime = Field(default_factory=datetime.now, description="저장 시간")
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
