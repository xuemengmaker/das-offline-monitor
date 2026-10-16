#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "未找到依赖环境。请先运行：./scripts/bootstrap.sh" >&2
  exit 1
fi

export DAS_DATA_ROOT="${DAS_DATA_ROOT:-/mnt/f/数据}"
exec .venv/bin/uvicorn das_monitor.main:app --host 127.0.0.1 --port 8000 --reload
