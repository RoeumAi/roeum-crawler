from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)

import os
import re
import time
import logging
from datetime import datetime
from typing import Iterable, List

import psycopg2
from psycopg2.extras import execute_values


log = logging.getLogger(__name__)


# ---------- 텍스트 전처리 & 청크 도우미 ----------

def _clean(s: str) -> str:
    """
    NBSP(문자/문자열) 제거 + 공백 정리.
    스파이더에서도 1차로 정리하지만, 안전하게 파이프라인에서도 한 번 더.
    """
    if not s:
        return ""
    s = s.replace("\xa0", " ")
    s = re.sub(r"\bNBSP\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_sentences(text: str) -> List[str]:
    """
    문장 분리(look-behind 없이).
    - 한국어 '다.' 패턴과 일반적인 .?! 뒤에 줄바꿈을 먼저 넣고
    - 줄바꿈 기준으로 나눔
    (이렇게 하면 Python re의 가변 길이 look-behind 제약을 피할 수 있음)
    """
    if not text:
        return []

    t = text
    # '다.' 뒤에 줄바꿈 삽입
    t = re.sub(r"(다\.)", r"\1\n", t)
    # .?! 뒤에도 줄바꿈 삽입
    t = re.sub(r"([\.!?])\s+", r"\1\n", t)
    # 빈 줄 제거 + 트림
    sents = [seg.strip() for seg in t.split("\n") if seg.strip()]
    return sents


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> Iterable[str]:
    """
    문장 단위로 모아서 길이 제한을 넘지 않는 청크 생성.
    - max_chars: 청크 최대 글자수(대략 700~900 토큰 정도)
    - overlap: 청크 간 겹침(앞 청크의 끝 일부를 다음 청크에 포함)
    """
    sents = _split_sentences(text)
    if not sents:
        return []

    chunks = []
    cur = []

    def length(lst: List[str]) -> int:
        return sum(len(x) + 1 for x in lst)  # 공백 포함 대충 길이

    for s in sents:
        if length(cur) + len(s) + 1 <= max_chars:
            cur.append(s)
        else:
            if cur:
                chunks.append(" ".join(cur).strip())
                # overlap 만큼 뒤에서부터 문장 가져와 다음 청크의 시작으로 사용
                carry = []
                while cur and length(carry) < overlap:
                    carry.insert(0, cur.pop())  # 뒤에서부터
                cur = carry + [s]
            else:
                # 문장이 너무 길면 강제로 자르기
                chunks.append(s[:max_chars])
                cur = [s[max_chars - overlap:]] if len(s) > max_chars else []

    if cur:
        chunks.append(" ".join(cur).strip())

    return chunks


# ---------- 파이프라인 본체 ----------

class EmbeddingQueuePipeline:
    """
    1) 스파이더 아이템을 받아서
       - 제목/부제/섹션(판시사항·판결요지·참조조문·전문:주문/이유)을 하나의 텍스트로 합치고
       - 문장/청크 단위로 분할한 후
       - PostgreSQL 의 embedding_queue 테이블에 'pending' 상태로 적재
    2) 워커(embed_worker.py)가 embedding_queue를 폴링하면서 임베딩을 생성해
       embeddings 테이블에 저장하고, queue의 status를 'done'으로 갱신
    """

    def __init__(self):
        # .env 의 DSN을 우선 사용. 없으면 개별 env로 조립
        self.dsn = (
                os.getenv("PG_DSN") or
                "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
                    user=os.getenv("PGUSER", "postgres"),
                    pw=os.getenv("PGPASSWORD", "postgres"),
                    host=os.getenv("PGHOST", "127.0.0.1"),
                    port=os.getenv("PGPORT", "5432"),
                    db=os.getenv("PGDATABASE", "roeum"),
                )
        )
        self.conn = None

    # --- Scrapy 훅 ---

    def open_spider(self, spider):
        """스파이더 시작 시 DB 연결 & 테이블 준비"""
        try:
            self.conn = psycopg2.connect(self.dsn)
            self.conn.autocommit = True
            cur = self.conn.cursor()
            # 큐 테이블(없으면 생성)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS embedding_queue (
                id          BIGSERIAL PRIMARY KEY,
                source_url  TEXT,
                title       TEXT,
                subtitle1   TEXT,
                chunk_no    INT NOT NULL,
                chunk       TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',  -- pending / done / error
                error       TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_embedding_queue_status
                ON embedding_queue(status);
            """)
            cur.close()
            log.info("[pipeline] connected to DB and ensured tables.")
        except Exception as e:
            log.error("[pipeline] DB connection failed: %s", e)
            self.conn = None  # DB 없이도 크롤링은 계속

    def close_spider(self, spider):
        """스파이더 종료 시 커넥션 정리"""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def process_item(self, item, spider):
        """
        아이템을 본문 문자열로 합치고, 청크로 나눠 큐에 적재.
        아이템은 그대로 다음 파이프라인/피드로 흘려보냄.
        """
        # ----- 1) 아이템 → 본문 문자열 구성 -----
        title = _clean(item.get("title", ""))
        subtitle1 = _clean(item.get("subtitle1", ""))

        # sections 구조: { '판시사항': str, '판결요지': str, '참조조문': str, '전문': {'주문': str, '이유': str} }
        sections = item.get("sections", {}) or {}
        pan_si = _clean(sections.get("판시사항", ""))
        pan_gyeol = _clean(sections.get("판결요지", ""))
        cham_jo = _clean(sections.get("참조조문", ""))
        jeonmun = sections.get("전문", {}) or {}
        ju_mun = _clean(jeonmun.get("주문", ""))
        i_yu = _clean(jeonmun.get("이유", ""))

        lines: List[str] = []
        if title:
            lines.append(title)
        if subtitle1:
            lines.append(f"({subtitle1})")

        if pan_si:
            lines.append("[판시사항]")
            lines.append(pan_si)
        if pan_gyeol:
            lines.append("[판결요지]")
            lines.append(pan_gyeol)
        if cham_jo:
            lines.append("[참조조문]")
            lines.append(cham_jo)
        # 전문(주문/이유)
        if ju_mun or i_yu:
            lines.append("[전문]")
            if ju_mun:
                lines.append("[주문]")
                lines.append(ju_mun)
            if i_yu:
                lines.append("[이유]")
                lines.append(i_yu)

        body = _clean("\n".join(lines))

        # ----- 2) 본문 → 청크 분할 -----
        chunks = list(chunk_text(body, max_chars=1200, overlap=200))
        if not chunks:
            # 내용이 없으면 스킵(그래도 아이템은 계속 흘려보냄)
            return item

        # ----- 3) DB 큐에 적재 -----
        if not self.conn:
            log.warning("[pipeline] DB not connected; skipping queue insert.")
            return item

        rows = [
            (
                item.get("source_url", ""),
                title,
                subtitle1,
                i + 1,           # chunk_no (1부터)
                ch,              # chunk
                "pending",       # status
                datetime.utcnow()
            )
            for i, ch in enumerate(chunks)
        ]

        try:
            with self.conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO embedding_queue
                        (source_url, title, subtitle1, chunk_no, chunk, status, updated_at)
                    VALUES %s
                """, rows)
            log.info("[pipeline] queued %d chunks for %s", len(rows), item.get("source_url"))
        except Exception as e:
            log.error("[pipeline] queue insert failed for %s: %s", item.get("source_url"), e)

        return item
