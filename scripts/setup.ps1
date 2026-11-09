# 川文 Windows 一键安装
# 用法：双击「安装川文.bat」；或者在 PowerShell 里运行：powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# 需要 Python 3.10 以上（python.org 安装时勾选 “Add python.exe to PATH”）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($msg) { Write-Host "== $msg" -ForegroundColor Green }

Step "1/4 找 Python"
$py = $null
foreach ($cand in @("py -3", "python", "python3")) {
  $parts = $cand.Split(" ")
  try {
    $ver = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]"3.10") { $py = $parts; break }
  } catch {}
}
if (-not $py) {
  Write-Host "没找到 Python 3.10 以上。请到 https://www.python.org/downloads/ 安装，安装时勾选 Add python.exe to PATH，然后重新运行。" -ForegroundColor Red
  Read-Host "按回车退出"; exit 1
}
Write-Host "   用的是：$($py -join ' ') （Python $ver）"

Step "2/4 建虚拟环境、装依赖（第一次约 1 分钟）"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py[0] $py[1..($py.Length - 1)] -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Step "3/4 自测"
& .venv\Scripts\python.exe tests\run_tests.py | Select-Object -Last 1

Step "4/4 在桌面和开始菜单建“川文”快捷方式"
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
  $lnk = $ws.CreateShortcut((Join-Path $dir "川文.lnk"))
  $lnk.TargetPath = (Join-Path $root ".venv\Scripts\pythonw.exe")
  $lnk.Arguments = '"' + (Join-Path $root "run.py") + '"'
  $lnk.WorkingDirectory = $root
  $lnk.IconLocation = (Join-Path $root "assets\chuanwen.ico")
  $lnk.Description = "川文：英语写作语料本"
  $lnk.Save()
}

Write-Host ""
Write-Host "装好了。双击桌面上的“川文”打开（不会弹黑色窗口），用完在网页「设置」最下面点“关闭川文”。" -ForegroundColor Green
Start-Process (Join-Path $root ".venv\Scripts\pythonw.exe") -ArgumentList ('"' + (Join-Path $root "run.py") + '"') -WorkingDirectory $root
Read-Host "按回车退出"
