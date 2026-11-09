"""启动：python run.py [--port 8800] [--no-open]

- 川文已经在这个端口运行时，只打开网页，不再启动第二份。
- 没有控制台（后台启动）时，输出写到数据目录里的 川文.log。
"""
import argparse
import sys
import threading
import urllib.request
import webbrowser


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "api/stats", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="川文：英语写作语料本")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"

    if already_running(url):
        print(f"川文已经在运行：{url}")
        if not args.no_open:
            webbrowser.open(url)
        return

    if sys.stdout is None or sys.stderr is None:
        from chuanwen.store import HOME

        HOME.mkdir(parents=True, exist_ok=True)
        log = open(HOME / "川文.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    import uvicorn

    from chuanwen import server

    config = uvicorn.Config(server.app, host="127.0.0.1", port=args.port, log_level="warning")
    srv = uvicorn.Server(config)
    server.UVICORN = srv
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"川文已启动：{url}  （按 Ctrl+C 退出）", flush=True)
    srv.run()


if __name__ == "__main__":
    main()
