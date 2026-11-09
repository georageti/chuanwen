# 川文

英语写作语料本：把好文章拆成你自己的写作素材——**好搭配、句型、论点、衔接**——先记住，再改写，最后在话题作文里用出来。和川听、川词、川打同一套学习体系（FSRS 复习）。

界面风格参照 Nous Research 的 Hermes Agent 官网：深藏青底、电光蓝、窄体细标题、等宽小标签、蓝色网点版画。

## 下载

[![下载 Mac 版](assets/btn-mac.svg)](https://github.com/georageti/chuanwen/releases/latest/download/chuanwen-mac.zip)
[![下载 Windows 版](assets/btn-windows.svg)](https://github.com/georageti/chuanwen/releases/latest/download/chuanwen-windows.zip)

**不要点 GitHub 绿色的 Code → Download ZIP**，那个是源代码，不带安装说明。用上面的按钮下载，解压后打开里面的「先看我.txt」：

1. 先装 Python 3.10 以上：[python.org/downloads](https://www.python.org/downloads/)（Windows 安装时勾选 “Add python.exe to PATH”）。
2. 双击「安装川文」（Mac 是 `.command`，Windows 是 `.bat`）。第一次会装依赖，约 1 分钟，装好自动打开。
   - Mac 提示“无法验证开发者”：右键 → 打开 → 打开；还不行就到“系统设置 → 隐私与安全性”点“仍要打开”。
   - Windows 提示“Windows 已保护你的电脑”：点“更多信息 → 仍要运行”。
3. 以后：Mac 在“应用程序”或 Spotlight 里打开 **川文**；Windows 双击桌面或开始菜单里的 **川文**。

所有版本见 [Releases](https://github.com/georageti/chuanwen/releases)。

## 启动和关闭

- 已经在运行时，再点只会打开网页；没在运行时，先在后台启动，再打开网页。网址是 http://127.0.0.1:8800 ，只在本机能访问。
- 用完在网页「设置」最下面点“关闭川文”。
- 也可以双击「启动川文」，或在终端运行 `.venv/bin/python run.py`（Windows：`.venv\Scripts\python.exe run.py`）。
- 项目文件夹挪了位置，重新双击一次「安装川文」。

第一次用：到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请），也可以选 Claude 或其他 OpenAI 兼容接口。不填也能用内置素材练回忆。

## 功能

1. **内置素材**：21 个话题（科技、教育、环境、城市化、人工智能……加一个“通用”），268 条：160 个搭配、60 个论点、24 个句型、24 个衔接，每个话题 2 道作文题。素材文件在 `data/builtin.txt`，格式写在文件开头。
2. **文章**：粘贴英文，或拖入 Word / TXT / Markdown。AI 逐句阅读，给出中文翻译，挑出四类素材，标出话题、语域、近义说法；每一处都核对必须真的出现在原句里，对不上的丢掉。勾选加入素材库；也可以在句子里划选任意文字手动加入。没有 AI 时只分句，靠划选。
3. **素材库**：按话题 × 类型浏览，搜索、收藏、写批注、朗读；可以自建话题；导出 Markdown、打印成卡片。在任何话题里点 **✦ AI 扩充**，AI 会再出一批不重复的素材（先生成、再复核中文和近义说法），挑选后加入；选了类型就只扩那一类。
4. **练习**
   - **回忆**：看中文意思和例句，把挖掉的搭配 / 句型 / 衔接拼出来（`↵` 检查，错三次显示答案，用提示记为“模糊”）；论点素材看中文想英文，看答案后按 `1` `2` `3` 自评。FSRS 安排复习，每天新素材默认 10 条。
   - **改写升级**：给一句平淡的英文，用指定素材把它升级（`⌘↵` 提交），AI 打 0–3 分、给修改和范句。题目会缓存。
   - **话题演练**：选话题，系统从库里挑出 12 条相关素材，你写一段（右边的素材写进去会亮起），AI 估 band、标出用上了哪些、列出主要问题、给参考改写。草稿自动保存在浏览器里。
   - 改写和演练的结果也算一次复习。
5. **数据**：在 `~/Library/Application Support/川文/`（设置环境变量 `CHUANWEN_HOME` 可以换位置）。「设置」里可以导出 / 导入学习记录（`chuanwen-backup/1`，合并不覆盖），以及把自己收集的搭配导出成川词能导入的 `wordbook/1`。

## 目录

```
chuanwen/    后端（FastAPI）
  ai.py        分句、挖空；AI 拆素材、改写出题和批改、话题演练批改（DeepSeek / Claude / OpenAI 兼容）
  builtin.py   解析内置素材 data/builtin.txt
  store.py     SQLite、设置、复习记录、导出导入
  srs.py       FSRS-5（与川打、川听同一套公式）
  docs.py      Word / TXT / Markdown 转文本
  server.py    接口
web/         网页（原生 JS）：app.js 页面，art.js 程序生成的蓝色版画，fonts/ 开源字体
tests/       run_tests.py 自测；fake_ai.py 本地假 AI 接口
```

## 自测

```bash
.venv/bin/python tests/run_tests.py
```

用临时数据目录，AI 部分接本地假接口，不联网、不花钱。

## 从源码运行（开发者）

需要 Python 3.10 以上。

```bash
git clone https://github.com/georageti/chuanwen.git
cd chuanwen
bash scripts/setup.sh        # Windows：powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.venv/bin/python run.py
```

发新版：改完提交推送后 `python3 -I scripts/build_release.py`，再 `gh release create vX.Y.Z dist/chuanwen-*.zip`。

字体：Barlow、Barlow Condensed、DM Mono、EB Garamond（SIL Open Font License）。代码使用 MIT 许可证。
