CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS laws (
  id BIGSERIAL PRIMARY KEY,
  lsi_seq BIGINT UNIQUE,
  source_url TEXT UNIQUE NOT NULL,
  title_line TEXT NOT NULL,
  department TEXT,
  content_hash TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS law_articles (
  id BIGSERIAL PRIMARY KEY,
  law_id BIGINT REFERENCES laws(id) ON DELETE CASCADE,
  art_no INT,
  heading TEXT NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE(law_id, art_no)
);

CREATE TABLE IF NOT EXISTS article_chunks (
  id BIGSERIAL PRIMARY KEY,
  article_id BIGINT REFERENCES law_articles(id) ON DELETE CASCADE,
  chunk_no INT NOT NULL,
  text TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE(article_id, chunk_no)
);

CREATE TABLE IF NOT EXISTS embedding_queue (
  id BIGSERIAL PRIMARY KEY,
  chunk_id BIGINT REFERENCES article_chunks(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'NEW',
  retries INT NOT NULL DEFAULT 0,
  enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id BIGINT PRIMARY KEY REFERENCES article_chunks(id) ON DELETE CASCADE,
  vector VECTOR(1536),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
