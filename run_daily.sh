#!/bin/bash
set -eu
CRAWLER_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${CRAWLER_PYTHON:-/Users/loum/miniconda3/envs/crawler/bin/python}"
exec "$PYTHON" "$CRAWLER_DIR/scripts/ops/macmini_batch.py" --days 1
