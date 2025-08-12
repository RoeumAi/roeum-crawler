# workers/embed_worker.py
"""
큐(embedding_queue)에서 청크를 뽑아 임베딩을 만든 뒤 embeddings 테이블에 적재하는 워커.

환경변수(.env)
- DB_DSN                 : postgresql://USER:PW@HOST:PORT/DB  (예: postgresql://roeum:roeum@127.0.0.1:5432/roeum)
  * 또는 PGUSER/PGPASSWORD/PGHOST/PGPORT/PGDATABASE 로 구성

- EMBED_BACKEND          : local | openai | stub   (기본: stub)
- EMBED_DIM              : 임베딩 차원. 0/미지정이면 backend에 맞춰 자동 결정(로컬 모델 차원/오픈AI probe/기본 768)
- WORKER_BATCH           : 한 번에 처리할 큐 건수 (기본 64)
- WORKER_POLL            : 큐가 비었을 때 재시도 간격(초) (기본 2.0)

# local 모드
- LOCAL_EMBED_MODEL      : sentence-transformers 모델명 (기본: intfloat/multilingual-e5-base)

# openai 모드
- OPENAI_API_KEY         : OpenAI API 키
- OPENAI_EMBED_MODEL     : OpenAI 임베딩 모델명 (기본: text-embedding-3-small)

테이블
- embedding_queue(id, source_url, title, subtitle1, chunk_no, chunk, status, error, created_at, updated_at)
- embeddings(id, queue_id, chunk_no, vector, created_at, updated_at)  -- queue_id 유니크
"""

from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv

# .env 로드(프로젝트 루트)
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)

import os
import time
import logging
import random
import hashlib
from typing import List, Tuple

import psycopg2
from psycopg2.extras import execute_values


# ---------------------- 설정 ----------------------

log = logging.getLogger("embed_worker")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BACKEND = os.getenv("EMBED_BACKEND", "stub").lower()
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "intfloat/multilingual-e5-base")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

EMBED_DIM = int(os.getenv("EMBED_DIM", "0"))  # 0이면 자동결정
BATCH = int(os.getenv("WORKER_BATCH", "64"))
POLL = float(os.getenv("WORKER_POLL", "2.0"))

# DB DSN 조립
DSN = (
        os.getenv("DB_DSN")
        or "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
    user=os.getenv("PGUSER", "roeum"),
    pw=os.getenv("PGPASSWORD", "roeum"),
    host=os.getenv("PGHOST", "127.0.0.1"),
    port=os.getenv("PGPORT", "5432"),
    db=os.getenv("PGDATABASE", "roeum"),
)
)

_local_model = None
_client = None


# ---------------------- 유틸 ----------------------

def _vec_literal(v: List[float]) -> str:
    """pgvector 컬럼에 넣기 위한 문자열 리터럴('[1,2,3]')"""
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


def _l2_normalize(v: List[float]) -> List[float]:
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v] if n > 0 else v


def _ensure_dim(v: List[float]) -> List[float]:
    global EMBED_DIM
    if EMBED_DIM and len(v) != int(EMBED_DIM):
        raise ValueError(
            f"Embedding dim mismatch: got {len(v)}, expected {EMBED_DIM}. "
            f"테이블 vector({EMBED_DIM}) 또는 모델/EMBED_DIM을 맞춰주세요."
        )
    return v


# ---------------------- 임베딩 초기화/호출 ----------------------

def _init_embedder():
    """백엔드별 임베더 초기화 + EMBED_DIM 자동결정(필요 시)"""
    global _local_model, _client, EMBED_DIM, BACKEND

    if BACKEND == "local":
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(LOCAL_EMBED_MODEL)
        if not EMBED_DIM:
            try:
                EMBED_DIM = int(_local_model.get_sentence_embedding_dimension())
            except Exception:
                EMBED_DIM = 768
        log.info(f"[local] model={LOCAL_EMBED_MODEL}, dim={EMBED_DIM}")

    elif BACKEND == "openai":
        try:
            from openai import OpenAI  # openai>=1.0
        except Exception:
            # 구버전 패키지명을 쓰는 환경을 방지
            raise RuntimeError("openai 패키지를 설치하거나 BACKEND를 local/stub으로 변경하세요.")
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        if not EMBED_DIM:
            # 한 번 probe해서 길이 알아내기
            resp = _client.embeddings.create(model=OPENAI_EMBED_MODEL, input="dim-probe")
            EMBED_DIM = len(resp.data[0].embedding)
        log.info(f"[openai] model={OPENAI_EMBED_MODEL}, dim={EMBED_DIM}")

    else:
        # stub
        if not EMBED_DIM:
            EMBED_DIM = 768
        log.info(f"[stub] dim={EMBED_DIM}")


def embed_many(texts, batch_size: int = 64, normalize: bool = True) -> List[List[float]]:
    """
    texts: str | List[str]  -> List[vector]
    BACKEND:
      - local  : sentence-transformers
      - openai : OpenAI Embeddings
      - stub   : 재현성 있는 가짜 벡터
    """
    def _as_list(x):
        return x if isinstance(x, (list, tuple)) else [x]

    texts = [((t or "").replace("\xa0", " ")).strip() for t in _as_list(texts)]
    out: List[List[float]] = []

    if BACKEND == "local":
        if _local_model is None:
            raise RuntimeError("Local embedder 미초기화. _init_embedder() 먼저 호출하세요.")
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vecs = _local_model.encode(batch, normalize_embeddings=False, convert_to_numpy=False)
            for v in vecs:
                v = list(map(float, v))
                _ensure_dim(v)
                out.append(_l2_normalize(v) if normalize else v)
        return out

    if BACKEND == "openai":
        if _client is None:
            raise RuntimeError("OpenAI client 미초기화. _init_embedder() 먼저 호출하세요.")
        model = OPENAI_EMBED_MODEL

        def _embed_batch(batch):
            delay = 1.5
            for attempt in range(6):
                try:
                    return _client.embeddings.create(model=model, input=batch)
                except Exception as e:
                    if attempt == 5:
                        raise
                    time.sleep(delay + random.random())
                    delay = min(delay * 2, 30)

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = _embed_batch(batch)
            for d in resp.data:
                v = [float(x) for x in d.embedding]
                _ensure_dim(v)
                out.append(_l2_normalize(v) if normalize else v)
        return out

    # stub
    rng = random.Random()
    dim = int(EMBED_DIM) if EMBED_DIM else 768
    for t in texts:
        seed = int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:16], 16)
        rng.seed(seed)
        v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        _ensure_dim(v)
        out.append(_l2_normalize(v) if normalize else v)
    return out


# ---------------------- DB ----------------------

def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    return conn


def ensure_tables(conn):
    """필요 테이블/인덱스 생성(없을 때만). vector 차원은 EMBED_DIM 사용."""
    dim = int(EMBED_DIM) if EMBED_DIM else 768
    with conn.cursor() as cur:
        # pgvector 확장
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        # 큐(파이프라인에서 생성하지만 안전하게 한 번 더)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS embedding_queue (
            id          BIGSERIAL PRIMARY KEY,
            source_url  TEXT,
            title       TEXT,
            subtitle1   TEXT,
            chunk_no    INT NOT NULL,
            chunk       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            error       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_embedding_queue_status ON embedding_queue(status);
        """)
        # 결과 테이블
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS embeddings (
            id         BIGSERIAL PRIMARY KEY,
            queue_id   BIGINT NOT NULL REFERENCES embedding_queue(id) ON DELETE CASCADE,
            chunk_no   INT NOT NULL,
            vector     vector({dim}) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(queue_id)
        );
        """)
    conn.commit()
    log.info(f"ensure_tables: vector dim={dim}")


def claim_batch(conn, limit: int) -> List[Tuple[int, int, str]]:
    """
    pending 상태의 큐를 잡아서 working 으로 표시하고 반환.
    반환: [(id, chunk_no, chunk), ...]
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, chunk_no, chunk
            FROM embedding_queue
            WHERE status='pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        ids = [r[0] for r in rows]
        if ids:
            execute_values(cur, """
                UPDATE embedding_queue AS q
                SET status='working', updated_at=now()
                FROM (VALUES %s) AS t(id)
                WHERE q.id = t.id
            """, [(i,) for i in ids])
    conn.commit()
    return rows


def mark_done(conn, ids: List[int]):
    if not ids:
        return
    with conn.cursor() as cur:
        execute_values(cur, """
            UPDATE embedding_queue AS q
               SET status='done', updated_at=now()
              FROM (VALUES %s) AS t(id)
             WHERE q.id = t.id
        """, [(i,) for i in ids])
    conn.commit()


def mark_error(conn, ids: List[int], msg: str):
    if not ids:
        return
    with conn.cursor() as cur:
        execute_values(cur, """
            UPDATE embedding_queue AS q
               SET status='error', error=%s, updated_at=now()
              FROM (VALUES %s) AS t(id)
             WHERE q.id = t.id
        """, [(msg, i) for i in ids])
    conn.commit()


def insert_embeddings(conn, rows: List[Tuple[int, int, List[float]]]):
    """
    rows: [(queue_id, chunk_no, vector_list), ...]
    ON CONFLICT(queue_id) UPSERT
    """
    if not rows:
        return
    payload = [(qid, cno, _vec_literal(vec)) for (qid, cno, vec) in rows]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO embeddings (queue_id, chunk_no, vector)
            VALUES %s
            ON CONFLICT (queue_id)
            DO UPDATE SET vector=EXCLUDED.vector, updated_at=now()
        """, payload)
    conn.commit()


# ---------------------- 메인 루프 ----------------------

def main():
    log.info(f"worker start | backend={BACKEND} batch={BATCH} poll={POLL}s dsn={DSN}")
    _init_embedder()

    conn = _connect()
    ensure_tables(conn)

    while True:
        try:
            batch = claim_batch(conn, BATCH)
            if not batch:
                time.sleep(POLL)
                continue

            ids = [row[0] for row in batch]
            texts = [row[2] for row in batch]
            cnos  = [row[1] for row in batch]

            vecs = embed_many(texts, batch_size=BATCH, normalize=True)
            rows = [(ids[i], cnos[i], vecs[i]) for i in range(len(ids))]

            insert_embeddings(conn, rows)
            mark_done(conn, ids)

            log.info(f"done: {len(rows)} chunks (last id={ids[-1]})")

        except KeyboardInterrupt:
            log.info("worker stopped by user")
            break
        except Exception as e:
            log.exception("fatal loop error: %s", e)
            # 혹시 락/트랜잭션 남았으면 롤백
            try:
                conn.rollback()
            except Exception:
                pass
            # 너무 빠른 재시도 방지
            time.sleep(2.0)

    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
