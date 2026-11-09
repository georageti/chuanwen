#!/bin/bash
# 一键搭环境：建虚拟环境、装依赖、跑自测。
# 用法：在项目目录里运行  bash scripts/setup.sh   （需要 Python 3.10 以上）
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== 1/2 建虚拟环境 .venv、装依赖（第一次约 1 分钟）"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt

echo "== 2/2 自测"
.venv/bin/python tests/run_tests.py | tail -1

echo
echo "好了。启动：.venv/bin/python run.py"
echo "想要 Mac 上的快捷入口：.venv/bin/python -I scripts/build_mac_app.py --install"
