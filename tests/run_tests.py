"""川文自测：.venv/bin/python tests/run_tests.py
用临时数据目录；AI 部分接一个本地假接口（tests/fake_ai.py），不联网、不花钱。"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = tempfile.mkdtemp(prefix="chuanwen-test-")
os.environ["CHUANWEN_HOME"] = TMP
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fake_ai  # noqa: E402
from chuanwen import ai, builtin, server, srs, store  # noqa: E402

passed, failed = 0, []


def check(name: str, cond, detail="") -> None:
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name} {detail}")
        print(f"  ✗ {name} {detail}")


def section(title: str):
    print(f"· {title}")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_fake_ai() -> int:
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(fake_ai.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            return port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("假 AI 没起来")


def main() -> None:
    c = TestClient(server.app)

    section("内置素材")
    data = builtin.load()
    check("话题数", len(data["topics"]) == 32, len(data["topics"]))
    check("素材数", len(data["materials"]) >= 1200, len(data["materials"]))
    slugs = {t["slug"] for t in data["topics"]}
    check("话题标识不重复", len(slugs) == len(data["topics"]))
    for t in data["topics"]:
        if t["slug"] != "general":
            check(f"{t['slug']} 有作文题", len(t["prompts"]) >= 2)
    norms = [store.norm(m["kind"], m["form"]) for m in data["materials"]]
    check("内置素材不重复", len(norms) == len(set(norms)), [n for n in norms if norms.count(n) > 1][:3])
    for m in data["materials"]:
        if m["kind"] != "idea":
            e = m["examples"][0]
            check(f"挖空能对上：{m['form']}", ai.make_cloze(e["en"], e["answer"]) is not None, e)
            check(f"有中文：{m['form']}", bool(m["zh"]) and bool(e["zh"]))
    check("例句解析", builtin.parse_example("A [b c] d [e].") == ("A b c d e.", ["b c", "e"]))

    section("分句和挖空")
    ss = ai.split_sentences("Dr. Smith agreed. It is, e.g. in Asia, common! \"Why?\" asked Mr. Lee.\n\nTitle\n- A bullet point about cities.")
    check("分句数", len(ss) == 4, ss)
    check("缩写不断句", ss[0] == "Dr. Smith agreed.", ss)
    check("列表符号去掉", ss[-1] == "A bullet point about cities.", ss)
    segs = ai.make_cloze("It is not wealth but health that matters.", ["It is not", "but", "that"])
    check("多段挖空", segs and [s.get("blank") for s in segs if "blank" in s] == ["It is not", "but", "that"])
    check("挖空找不到返回 None", ai.make_cloze("Hello world there.", ["nope"]) is None)
    check("不跨词匹配", ai.make_cloze("The cat category.", ["cat"]) == [{"t": "The "}, {"blank": "cat"}, {"t": " category."}])
    check("位置", ai.spans_of(ai.make_cloze("Ab cd ef.", ["cd"])) == [[3, 5]])

    def trim(sent, piece, form):
        segs = ai.make_cloze(sent, [piece])
        t = ai.trim_to_form(sent, segs, form)
        return [x["blank"] for x in (t or segs) if "blank" in x]
    check("挖空去掉空位里的词", trim("It spares employees the daily commute.", "spares employees the daily commute", "spare sb sth") == ["spares"])
    check("挖空分段", trim("It can stunt their professional growth.", "stunt their professional growth", "stunt one's growth") == ["stunt", "growth"])
    check("be 动词", trim("It was greatly accelerated by the pandemic.", "was greatly accelerated by", "be accelerated by sth") == ["was", "accelerated by"])
    check("不规则变形保留原样", trim("She took up the challenge.", "took up the challenge", "take up sth") == ["took up the challenge"])
    check("素材里的泛指词", trim("It can take up two hours a day.", "take up two hours a day", "take up time") == ["take up"])
    check("本来就对的不动", trim("It erodes team spirit.", "erodes team spirit", "erode team spirit") == ["erodes team spirit"])

    section("初次启动")
    s = c.get("/api/stats").json()
    check("内置素材入库", s["materials"] == len(data["materials"]), s["materials"])
    check("没 AI", s["aiReady"] is False and "DeepSeek" in s["aiReason"])
    topics = c.get("/api/topics").json()
    check("话题接口", len(topics) == 32 and topics[0]["slug"] == "general")
    lib = c.get("/api/materials?topic=education").json()
    check("按话题筛", all("education" in m["topics"] for m in lib) and len(lib) >= 30, len(lib))
    check("内置按文件顺序", lib[0]["kind"] == "collocation" and lib[-1]["kind"] == "idea", [m["kind"] for m in lib])
    check("搜索", any(m["form"] == "rote learning" for m in c.get("/api/materials?q=死记硬背").json()))

    section("没 AI 时")
    r = c.post("/api/docs", data={"text": "There is no denying that technology has transformed education. However, it also poses a threat to privacy."}).json()
    d = c.get(f"/api/docs/{r['id']}").json()
    check("只分句", d["mode"] == "plain" and len(d["sents"]) == 2 and d["warnings"])
    p = c.post("/api/pick", json={"selection": "poses a threat to", "sentence": d["sents"][1]["text"]}).json()
    check("划选兜底", p["ai"] is False and p["candidate"]["form"] == "poses a threat to" and p["candidate"]["examples"][0]["answer"] == ["poses a threat to"])
    check("改写要 AI", c.post("/api/rewrite/next", json={}).status_code == 400)
    st = c.get("/api/drill/start?topic=education").json()
    check("演练能出内置题", st["prompt"]["en"] and len(st["suggestions"]) >= 8, len(st["suggestions"]))
    check("演练建议含通用句型", any(m["kind"] == "pattern" for m in st["suggestions"]))
    check("演练提交要 AI", c.post("/api/drill/check", json={"topic": "education", "prompt": "x", "text": "word " * 30, "ids": []}).status_code == 400)
    check("空文档报错", c.post("/api/docs", data={"text": "  "}).status_code == 400)
    check("不支持的文件", c.post("/api/docs", files={"file": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 400)

    section("回忆练习")
    plan = c.post("/api/recall/plan", json={}).json()
    check("新素材额度", len(plan["ids"]) == 10 and plan["quota"] == 10, plan)
    cards = c.post("/api/recall/cards", json={"ids": plan["ids"]}).json()
    check("卡片数", len(cards) == 10)
    cloze = next(x for x in cards if x["type"] == "cloze")
    check("挖空卡有答案", cloze["answer"] and cloze["segments"])
    r = c.post("/api/recall/answer", json={"id": cloze["id"], "mistakes": 0, "peeked": False, "hinted": False}).json()
    check("第一次学最多记模糊", r["state"]["reps"] == 1 and r["grade"] == srs.GOOD)
    idea = store.list_materials(kind="idea")[0]
    card = server.make_card(idea)
    check("论点是自评卡", card["type"] == "flash" and card["zh"])
    r = c.post("/api/recall/answer", json={"id": idea["id"], "grade": 1}).json()
    check("自评忘记", r["grade"] == 1 and r["state"]["lapses"] == 1)
    plan2 = c.post("/api/recall/plan", json={}).json()
    check("额度扣掉", plan2["quota"] == 8, plan2["quota"])
    check("按类型筛", all(store.get_material(i)["kind"] == "linker" for i in c.post("/api/recall/plan", json={"kind": "linker", "scope": "new"}).json()["ids"]))
    check("只复习时没有到期", c.post("/api/recall/plan", json={"scope": "review"}).json()["ids"] == [])

    section("素材增删改")
    r = c.post("/api/materials", json={"items": [{"kind": "collocation", "form": " Bridge  the gap ", "zh": "弥合差距", "topics": ["education", "bogus"],
                                                   "examples": [{"en": "We must bridge the gap.", "zh": "", "answer": ["bridge the gap"]},
                                                                {"en": "Bad example.", "answer": ["nothing"]}]}]}).json()
    check("手动加", r["added"] == 1)
    mid = r["ids"][0]
    m = c.get(f"/api/materials/{mid}").json()
    check("空格规整、无效话题去掉", m["form"] == "Bridge the gap" and m["topics"] == ["education"], m)
    check("对不上的答案清空", m["examples"][1]["answer"] == [])
    r = c.post("/api/materials", json={"items": [{"kind": "collocation", "form": "bridge the gap", "examples": [{"en": "Another one to bridge the gap.", "answer": ["bridge the gap"]}]}]}).json()
    check("重复的合并例句", r["added"] == 0 and len(c.get(f"/api/materials/{mid}").json()["examples"]) == 3)
    check("坏类型报错", c.post("/api/materials", json={"items": [{"kind": "x", "form": "a"}]}).status_code == 400)
    c.patch(f"/api/materials/{mid}", json={"starred": True, "user_note": "考研作文用过"})
    m = c.get(f"/api/materials/{mid}").json()
    check("收藏和批注", m["starred"] == 1 and m["user_note"] == "考研作文用过")
    check("只看收藏", [x["id"] for x in c.get("/api/materials?starred=true").json()] == [mid])
    check("只看我的", all(x["source"] != "builtin" for x in c.get("/api/materials?source=mine").json()))
    builtin_one = store.list_materials(source="builtin")[0]
    c.delete(f"/api/materials/{builtin_one['id']}")
    store.kv_set("builtin_version", 0)
    store._seed(store.db())
    check("删掉的内置素材不会再加回来", store.norm(builtin_one["kind"], builtin_one["form"]) not in store.norms_in_library())
    pat = store.list_materials(query="There is no denying")[0]
    store.tx("UPDATE materials SET norm=? WHERE id=?", ("pattern:there is no denying that ...", pat["id"]))  # 模拟老数据库
    store.kv_set("builtin_version", 1)
    store._seed(store.db())
    check("升级时老标识重算、不重复", len([m for m in store.list_materials(kind="pattern") if m["form"].startswith("There is no denying")]) == 1)
    t = c.post("/api/topics", json={"zh": "食品安全", "en": "Food Safety"}).json()
    check("自建话题", t["slug"] == "food-safety" and t["glyph"] == "食")
    check("自建话题演练兜底题", "food safety" in c.get("/api/drill/start?topic=food-safety").json()["prompt"]["en"].lower())

    section("接上假 AI")
    port = start_fake_ai()
    c.post("/api/settings", json={"ai": {"provider": "openai", "openai": {"key": "test-key", "model": "fake", "base": f"http://127.0.0.1:{port}/v1"}}})
    check("设置里 Key 打码", c.get("/api/settings").json()["ai"]["openai"]["key"] == "••••-key")
    c.post("/api/settings", json={"ai": {"openai": {"key": "••••-key"}}})
    check("打码的 Key 不会存回去", store.load_config()["ai"]["openai"]["key"] == "test-key")
    check("测试连接", c.post("/api/settings/test").json()["ok"] is True)
    check("AI 可用", c.get("/api/stats").json()["aiReady"] is True)

    section("AI 拆素材")
    text = "There is no denying that remote work reshapes cities.\n\nYoung workers increasingly leave expensive centres. Rents fall slowly."
    r = c.post("/api/docs", data={"text": text, "title": "测试文章"}).json()
    check("AI 模式", r["mode"] == "ai", r)
    d = c.get(f"/api/docs/{r['id']}").json()
    check("每句有翻译", all(s["zh"] for s in d["sents"]), d["sents"])
    items = [it for s in d["sents"] for it in s["items"]]
    check("对不上原文的丢掉", not any(it["form"] == "ghost" for it in items))
    check("警告里提到丢弃", any("对不上" in w for w in d["warnings"]), d["warnings"])
    check("句型识别", any(it["kind"] == "pattern" and it["saved"] for it in items))  # 内置里已有
    col = next(it for it in items if it["kind"] == "collocation")
    check("搭配带位置", d["sents"][0]["text"][col["spans"][0][0]:col["spans"][0][1]].lower() == col["form"], col)
    check("无效话题过滤", "nonsense" not in col["topics"] and "education" in col["topics"])
    check("例句带翻译", col["examples"][0]["zh"].startswith("第"))
    idea_it = next(it for it in items if it["kind"] == "idea")
    check("论点不挖空", idea_it["examples"][0]["answer"] == [])
    r2 = c.post("/api/materials", json={"doc_id": r["id"], "items": [col, idea_it]}).json()
    check("从文章加入", r2["added"] == 2)
    check("文章列表计数", next(x for x in c.get("/api/docs").json() if x["id"] == r["id"])["saved"] == 2)
    check("加入后标为已收", any(it["saved"] for s in c.get(f"/api/docs/{r['id']}").json()["sents"] for it in s["items"] if it["form"] == col["form"]))
    check("重新拆", c.post(f"/api/docs/{r['id']}/mine").json()["ok"])
    p = c.post("/api/pick", json={"selection": "Rents fall", "sentence": "Rents fall slowly."}).json()
    check("划选用 AI", p["ai"] and p["candidate"]["zh"] == "模拟释义" and p["candidate"]["examples"][0]["zh"] == "模拟整句翻译")

    section("改写升级")
    r = c.post("/api/rewrite/next", json={"id": mid}).json()
    check("出题", r["plain"] and r["material"]["id"] == mid)
    bad = c.post("/api/rewrite/check", json={"taskId": r["taskId"], "answer": "I like it."}).json()
    check("没用上记 0 分", bad["score"] == 0 and bad["used"] is False and bad["grade"] == srs.AGAIN)
    r = c.post("/api/rewrite/next", json={"id": mid}).json()
    good = c.post("/api/rewrite/check", json={"taskId": r["taskId"], "answer": "We must bridge the gap between rich and poor."}).json()
    check("用上记 3 分", good["score"] == 3 and good["model"])
    calls_before = fake_ai.CALLS.count("rewrite")
    r = c.post("/api/rewrite/next", json={}).json()
    check("随机出题", r["material"]["kind"] in ("collocation", "pattern", "linker"))
    c.post("/api/rewrite/next", json={})
    check("顺手出好的题会复用", fake_ai.CALLS.count("rewrite") - calls_before <= 2, fake_ai.CALLS)
    check("空答案报错", c.post("/api/rewrite/check", json={"taskId": r["taskId"], "answer": ""}).status_code == 400)

    section("话题演练")
    st = c.get("/api/drill/start?topic=education").json()
    sugg = st["suggestions"]
    first = next(m for m in sugg if m["kind"] == "collocation")
    essay = (f"Many argue that schools should focus on exams. However, I believe a good system should {first['form']} "
             "because students need more than test scores to succeed in life and work in the future.")
    fb = c.post("/api/drill/check", json={"topic": "education", "prompt": st["prompt"]["en"], "text": essay, "ids": [m["id"] for m in sugg]}).json()
    check("估分取半档", fb["band"] == 6.5, fb.get("band"))
    check("认出用上的素材", any(u["id"] == first["id"] for u in fb["used"]), fb["used"])
    check("假的引用被丢掉", all(u["id"] != 999999 for u in fb["used"]) and all(i["quote"] != "not in text" for i in fb["issues"]))
    check("用上的记一次复习", store.get_material(first["id"])["state"] is not None)
    check("演练记录", len(c.get("/api/drills").json()) == 1)
    check("太短不批", c.post("/api/drill/check", json={"topic": "education", "prompt": "x", "text": "too short", "ids": []}).status_code == 400)
    st = c.get("/api/drill/start?topic=food-safety&fresh=true").json()
    check("自建话题让 AI 出题", st["prompt"]["en"].startswith("Some say"), st["prompt"])
    essay2 = "I think we should bridge the gap between farms and shops, because food safety matters to everyone in every city and village today."
    fb2 = c.post("/api/drill/check", json={"topic": "food-safety", "prompt": "x", "text": essay2, "ids": []}).json()
    check("库里其他素材也认出来", any(h["id"] == mid for h in fb2["alsoUsed"]), fb2["alsoUsed"])

    section("AI 扩充")
    r = c.post("/api/topics/education/expand", json={}).json()
    items = r.get("items", [])
    forms = [m["form"] for m in items]
    check("扩充有结果", len(items) >= 10, r)
    check("漏动词的丢掉", "puts pressure on" not in forms and "not in example" not in forms, forms)
    check("审校删掉第一条", "mitigate the risk number 0" not in forms, forms[:3])
    check("审校改中文、清近义", all(m["zh"].endswith("（复核）") and m["alts"] == [] for m in items if m["kind"] == "collocation"))
    check("中文例句去掉方括号", all("[" not in m["examples"][0]["zh"] for m in items))
    check("扩充带话题", all(m["topics"] == ["education"] for m in items))
    r2 = c.post("/api/materials", json={"source": "ai", "items": items}).json()
    check("加入扩充素材", r2["added"] == len(items))
    check("来源记为 AI", c.get(f"/api/materials/{r2['ids'][0]}").json()["source"] == "ai")
    r = c.post("/api/topics/general/expand", json={"kind": "pattern"}).json()
    check("按类型扩充", r["items"] and all(m["kind"] == "pattern" for m in r["items"]), r)
    check("扩充不存在的话题", c.post("/api/topics/nope/expand", json={}).status_code == 404)

    section("导出导入")
    md = c.get("/api/export/markdown?topic=education").text
    check("Markdown 导出", md.startswith("# 川文素材 · 教育") and "rote learning" in md)
    wb = c.get("/api/export/wordbook").json()
    check("川词格式", wb["format"] == "wordbook/1" and any(e["word"] == "Bridge the gap" for e in wb["entries"]))
    backup = c.get("/api/export/backup").json()
    check("备份格式", backup["format"] == "chuanwen-backup/1" and backup["materials"])
    r = c.post("/api/import/backup", files={"file": ("b.json", io.BytesIO(json.dumps(backup).encode()), "application/json")}).json()
    check("导入自己的备份不新增", r["added"] == 0, r)
    check("坏文件", c.post("/api/import/backup", files={"file": ("b.json", io.BytesIO(b"{}"), "application/json")}).status_code == 400)

    section("网页文件")
    check("首页", "川文" in c.get("/").text)
    for f in ("app.js", "app.css", "art.js", "fonts/barlow-condensed-latin-200-normal.woff2"):
        check(f"静态文件 {f}", c.get("/" + f).status_code == 200)
    check("关闭要请求头", c.post("/api/quit").status_code == 403)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        failed.append("异常中断")
    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
