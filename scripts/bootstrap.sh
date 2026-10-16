#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="/home/wangxm/.local/bin/uv"

if [[ ! -x "$UV_BIN" ]]; then
  echo "未找到 uv：请先执行项目安装说明中的 uv 安装步骤。" >&2
  exit 1
fi

cd "$PROJECT_ROOT"
export UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
"$UV_BIN" sync --all-groups --no-progress
echo "环境准备完成：$PROJECT_ROOT/.venv"
