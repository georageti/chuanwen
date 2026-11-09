"""生成 GitHub Release 的下载包：dist/chuanwen-mac.zip、dist/chuanwen-windows.zip。

包里是仓库里已提交的文件（按工作区原样，保留可执行权限、Windows 脚本的 CRLF 和 BOM），外加一份「先看我.txt」；
另一个系统专用的启动和安装文件不放进去，免得看着乱。

用法：python3 -I scripts/build_release.py
发布：gh release create vX.Y.Z dist/chuanwen-*.zip   （文件名保持不变，README 的下载按钮就一直指向最新版）
"""
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
TOP = "chuanwen"

ONLY_WINDOWS = {"scripts/setup.ps1", "scripts/make_ico.py", "启动川文.bat", "安装川文.bat", "assets/chuanwen.ico"}
ONLY_MAC = {"scripts/setup.sh", "scripts/build_mac_app.py", "启动川文.command", "安装川文.command", "assets/chuanwen.icns"}
SKIP_ALL = {"scripts/expand_builtin.py", "scripts/build_release.py"}  # 开发用，普通用户用不到

README_MAC = """川文（Mac 版）· 英语写作语料本

1. 先装 Python 3.10 以上：https://www.python.org/downloads/ （已经装过可以跳过）
2. 双击文件夹里的「安装川文.command」。
   第一次如果提示“无法验证开发者”或“Apple 无法检查其是否包含恶意软件”：
   右键它 → 打开 → 打开。（还不行就到“系统设置 → 隐私与安全性”，往下找到它，点“仍要打开”。）
3. 装好后会自动打开川文。以后在“应用程序”或 Spotlight（⌘ 空格）里搜“川文”打开，
   用完在网页「设置」最下面点“关闭川文”。

第一次用，到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请）。
不填也能用：内置的 1300 多条素材可以直接练回忆；拆文章、改写批改、话题演练、AI 扩充需要 Key。
请把这个文件夹放在固定的位置再安装（比如“文稿”里）；以后挪了位置，重新双击安装一次即可。
详细说明：https://github.com/georageti/chuanwen
"""

README_WIN = """川文（Windows 版）· 英语写作语料本

1. 先装 Python 3.10 以上：https://www.python.org/downloads/
   安装时一定要勾选 “Add python.exe to PATH”。
2. 先把整个压缩包解压到固定的位置（比如“文档”里），不要在压缩包里直接运行。
3. 双击文件夹里的「安装川文.bat」。如果弹出“Windows 已保护你的电脑”，点“更多信息 → 仍要运行”。
4. 装好后会自动打开川文；桌面和开始菜单会出现“川文”，以后双击它打开（不会弹黑色窗口）。
   用完在网页「设置」最下面点“关闭川文”。

第一次用，到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请）。
不填也能用：内置的 1300 多条素材可以直接练回忆；拆文章、改写批改、话题演练、AI 扩充需要 Key。
Windows 版还没有在真机上大量测试，遇到问题欢迎到 GitHub 提 issue：
https://github.com/georageti/chuanwen
"""


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def add(z: zipfile.ZipFile, src: Path, arc: str) -> None:
    info = zipfile.ZipInfo.from_file(src, f"{TOP}/{arc}")
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as f:
        z.writestr(info, f.read(), compresslevel=9)


def build(name: str, skip: set[str], readme: str) -> Path:
    out = DIST / name
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        for f in tracked_files():
            if f in skip or f in SKIP_ALL:
                continue
            add(z, ROOT / f, f)
        info = zipfile.ZipInfo(f"{TOP}/先看我.txt")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, readme.replace("\n", "\r\n") if "windows" in name else readme)
    return out


def main() -> None:
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("提醒：工作区有没提交的改动，下载包只包含已提交的文件，但内容按工作区原样打包：\n" + dirty)
    for name, skip, readme in (("chuanwen-mac.zip", ONLY_WINDOWS, README_MAC),
                               ("chuanwen-windows.zip", ONLY_MAC, README_WIN)):
        p = build(name, skip, readme)
        print(f"{p.name}  {p.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
