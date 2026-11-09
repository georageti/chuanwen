"""生成 Mac 上的快捷入口 dist/川文.app：放进程序坞或用 Spotlight 搜“川文”打开。

点开时：川文已经在运行 → 直接打开网页；没在运行 → 在后台启动（不弹终端窗口），等它就绪再打开网页。
后台运行的日志在 ~/Library/Logs/川文.log；退出用网页「设置」最下面的“关闭川文”。

用法：python3 -I scripts/build_mac_app.py [--install]   （--install 复制到 ~/Applications）
"""
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "川文.app"
PORT = 8800

LAUNCHER = f"""#!/bin/bash
# 川文快捷入口：已在运行就打开网页，否则先在后台启动
DIR="{ROOT}"
URL="http://127.0.0.1:{PORT}/"
alive() {{ /usr/bin/curl -s -m 1 -o /dev/null "${{URL}}api/stats"; }}
if alive; then
  /usr/bin/open "$URL"
  exit 0
fi
if [ ! -x "$DIR/.venv/bin/python" ]; then
  /usr/bin/osascript -e 'display alert "川文打不开" message "找不到 $DIR/.venv，请按 README 重新搭环境。"'
  exit 1
fi
mkdir -p "$HOME/Library/Logs"
cd "$DIR"
nohup "$DIR/.venv/bin/python" "$DIR/run.py" --no-open --port {PORT} >> "$HOME/Library/Logs/川文.log" 2>&1 &
disown
for i in $(seq 1 60); do
  alive && break
  sleep 0.25
done
if alive; then
  /usr/bin/open "$URL"
else
  /usr/bin/osascript -e 'display alert "川文没能启动" message "详细原因在 ~/Library/Logs/川文.log"'
fi
"""

ICON_SWIFT = r"""
import AppKit
let size: CGFloat = 1024
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
// macOS 图标的标准留白：内容 824，四周各 100
let box = NSRect(x: 100, y: 100, width: 824, height: 824)
let bg = NSBezierPath(roundedRect: box, xRadius: 185, yRadius: 185)
NSColor(srgbRed: 0x19/255, green: 0x19/255, blue: 0x2f/255, alpha: 1).setFill()
bg.fill()
// Hermes 风格：电光蓝细边框 + 米白“文”
NSColor(srgbRed: 0x5d/255, green: 0x5d/255, blue: 1, alpha: 1).setStroke()
let frame = NSBezierPath(roundedRect: box.insetBy(dx: 34, dy: 34), xRadius: 150, yRadius: 150)
frame.lineWidth = 14
frame.stroke()
let style = NSMutableParagraphStyle()
style.alignment = .center
let font = NSFont(name: "PingFangSC-Light", size: 520) ?? NSFont.systemFont(ofSize: 520, weight: .light)
let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor(srgbRed: 0.95, green: 0.95, blue: 0.95, alpha: 1), .paragraphStyle: style]
let text = NSAttributedString(string: "文", attributes: attrs)
let h = text.size().height
text.draw(in: NSRect(x: 100, y: 512 - h / 2 - 10, width: 824, height: h))
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
"""


def build_icon(dest: Path) -> None:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "icon.swift").write_text(ICON_SWIFT)
        png = d / "icon.png"
        subprocess.run(["swiftc", "-O", str(d / "icon.swift"), "-o", str(d / "icon")], check=True, capture_output=True)
        subprocess.run([str(d / "icon"), str(png)], check=True)
        iconset = d / "AppIcon.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = s * scale
                name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


def main() -> None:
    if APP.exists():
        shutil.rmtree(APP)
    (APP / "Contents/MacOS").mkdir(parents=True)
    (APP / "Contents/Resources").mkdir(parents=True)
    exe = APP / "Contents/MacOS/chuanwen"
    exe.write_text(LAUNCHER)
    exe.chmod(0o755)
    prebuilt = ROOT / "assets" / "chuanwen.icns"
    if prebuilt.exists():  # 仓库里带了现成图标，不需要 swiftc（没装 Xcode 命令行工具也能生成）
        shutil.copy2(prebuilt, APP / "Contents/Resources/AppIcon.icns")
    else:
        build_icon(APP / "Contents/Resources/AppIcon.icns")
    with open(APP / "Contents/Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": "川文", "CFBundleDisplayName": "川文", "CFBundleIdentifier": "com.tigeorage.chuanwen",
            "CFBundleExecutable": "chuanwen", "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "12.0",
            "LSUIElement": False, "NSHighResolutionCapable": True,
        }, f)
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(APP)], check=True, capture_output=True)
    print(f"已生成 {APP}")
    if "--install" in sys.argv:
        dest = Path.home() / "Applications" / "川文.app"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(APP, dest, symlinks=True)
        subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
                        "-f", str(dest)], capture_output=True)
        print(f"已安装到 {dest}")


if __name__ == "__main__":
    main()
