"""启动川文（不依赖当前目录）：.venv/bin/python run.py [--port 8800] [--no-open]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chuanwen.__main__ import main  # noqa: E402

main()
