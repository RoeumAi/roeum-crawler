# roeum-crawler 일일 크롤링 이미지 (ECS Fargate ARM64).
# 진입점은 docker/entrypoint.sh → run_daily.sh. 상태는 전부 DB 기준이라 볼륨이 필요 없다.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul \
    HF_HOME=/opt/hf \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata time tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# 토크나이저를 이미지에 받아 둔다(실행 중 Hugging Face 접속 없음).
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('kakao1513/KURE-legal-ft-v1')"

COPY . .
RUN useradd --create-home crawler \
    && mkdir -p /app/logs /app/data /app/debug \
    && chown -R crawler /app/logs /app/data /app/debug
USER crawler

# 컨테이너 기본값: JSONL 덤프 안 함, 로그는 표준 출력(CloudWatch), Discord 요약 대신 배치 관리 알림.
ENV HF_HUB_OFFLINE=1 \
    SAVE_JSONL=false \
    CRAWLER_LOG_STDOUT=1 \
    CRAWLER_DISCORD_SUMMARY=0 \
    PYTHON=python3

ENTRYPOINT ["/app/docker/entrypoint.sh"]
