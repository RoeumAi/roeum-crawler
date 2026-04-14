"""
토큰 기반 텍스트 청킹 유틸리티

법률 문서의 content를 토큰 단위로 분할하여 임베딩에 적합한 크기로 만듭니다.
"""

import logging
from typing import List, Optional
from transformers import AutoTokenizer

logger = logging.getLogger(__name__)

# 전역 토크나이저 (싱글톤 패턴)
_tokenizer = None


def get_tokenizer():
    """토크나이저 인스턴스를 반환합니다 (싱글톤)"""
    global _tokenizer
    if _tokenizer is None:
        try:
            logger.info("🔧 토크나이저 로딩 중: kakao1513/KURE-legal-ft-v1")
            _tokenizer = AutoTokenizer.from_pretrained("kakao1513/KURE-legal-ft-v1")
            logger.info("✅ 토크나이저 로딩 완료")
        except Exception as e:
            logger.error(f"❌ 토크나이저 로딩 실패: {e}")
            raise
    return _tokenizer


def count_tokens(text: str) -> int:
    """
    텍스트의 토큰 개수를 계산합니다.
    
    Args:
        text: 토큰 개수를 계산할 텍스트
        
    Returns:
        토큰 개수
    """
    if not text:
        return 0
    
    tokenizer = get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=True))


def count_tokens_batch(texts: List[str]) -> List[int]:
    """
    여러 텍스트의 토큰 개수를 배치로 계산합니다.
    
    Args:
        texts: 토큰 개수를 계산할 텍스트 리스트
        
    Returns:
        각 텍스트의 토큰 개수 리스트
    """
    if not texts:
        return []
    
    tokenizer = get_tokenizer()
    encoded = tokenizer(texts, add_special_tokens=True, padding=False, truncation=False)
    return [len(ids) for ids in encoded['input_ids']]


def chunk_text_by_tokens(
    text: str,
    max_tokens: int = 8000,
    overlap_tokens: int = 200
) -> List[str]:
    """
    텍스트를 토큰 단위로 청킹합니다 (sliding window 방식).
    
    Args:
        text: 청킹할 텍스트
        max_tokens: 청크당 최대 토큰 수 (기본값: 8000)
        overlap_tokens: 청크 간 중복 토큰 수 (기본값: 200)
        
    Returns:
        청크 텍스트 리스트
    """
    if not text:
        return []
    
    tokenizer = get_tokenizer()
    
    # 전체 텍스트를 토큰화 (special tokens 제외)
    all_tokens = tokenizer.encode(text, add_special_tokens=False)
    total_tokens = len(all_tokens)
    
    # 토큰이 max_tokens보다 작으면 그대로 반환
    if total_tokens <= max_tokens:
        return [text]
    
    chunks = []
    step = max_tokens - overlap_tokens
    
    # sliding window로 chunk 생성
    for i in range(0, total_tokens, step):
        end_idx = min(i + max_tokens, total_tokens)
        chunk_tokens = all_tokens[i:end_idx]
        chunk_text = tokenizer.decode(chunk_tokens, skip_special_tokens=True)
        chunks.append(chunk_text)
        
        # 마지막 청크까지 도달했으면 종료
        if end_idx >= total_tokens:
            break
    
    logger.debug(f"텍스트 청킹 완료: {total_tokens} 토큰 → {len(chunks)}개 청크")
    return chunks


def should_chunk_content(content: str, threshold: int = 8000) -> bool:
    """
    content가 청킹이 필요한지 판단합니다.
    
    Args:
        content: 확인할 텍스트
        threshold: 청킹 기준 토큰 수 (기본값: 8000)
        
    Returns:
        청킹 필요 여부
    """
    if not content:
        return False
    
    token_count = count_tokens(content)
    return token_count > threshold


# 테스트용
if __name__ == "__main__":
    # 간단한 테스트
    test_text = "이것은 테스트 문장입니다. " * 1000  # 긴 텍스트 생성
    
    print(f"원본 텍스트 길이: {len(test_text)} 문자")
    print(f"토큰 개수: {count_tokens(test_text)}")
    
    chunks = chunk_text_by_tokens(test_text, max_tokens=100, overlap_tokens=20)
    print(f"생성된 청크 수: {len(chunks)}")
    
    for i, chunk in enumerate(chunks[:3], 1):
        token_count = count_tokens(chunk)
        print(f"청크 {i}: {len(chunk)} 문자, {token_count} 토큰")
