#!/bin/zsh
# 双击启动川文，会自动打开浏览器；关掉这个终端窗口就退出
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "还没安装：请先双击「安装川文.command」（第一次要右键 → 打开）。"
  read -k 1 -s "?按任意键关闭"
  exit 1
fi
exec .venv/bin/python run.py
