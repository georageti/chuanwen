#!/bin/zsh
# 双击启动川文，会自动打开浏览器；关掉这个终端窗口就退出
cd "$(dirname "$0")"
exec .venv/bin/python run.py
