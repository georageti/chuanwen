"""川文本地服务：FastAPI，只监听 127.0.0.1。网页在 web/。"""
from __future__ import annotations

import json
import os
import random
import re
import signal
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, docs, srs, store

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="川文", docs_url=None, redoc_url=None)
UVICORN = None  # run.py 启动时放进 uvicorn.Server，用来正常退出


@app.middleware("http")
async def revalidate_web_files(request, call_next):
    """网页文件每次都向服务器确认，更新界面后浏览器不会继续用旧的 app.css / app.js"""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def err(msg: str, code: int = 400):
    raise HTTPException(code, msg)


def ai_reason() -> str | None:
    return ai.unavailable_reason(store.load_config())


def download(data: str, name: str, media: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})


# ---------- 概况和设置 ----------

@app.get("/api/stats")
def api_stats():
    s = store.stats()
    s["aiReady"] = ai_reason() is None
    s["aiReason"] = ai_reason()
    return s


@app.get("/api/settings")
def api_settings():
    return store.public_config()


@app.post("/api/settings")
def api_save_settings(patch: dict = Body(...)):
    ai_patch = patch.get("ai", {})
    for p in ("deepseek", "anthropic", "openai"):  # 网页显示的是打码的 Key，没改就别存回去
        k = ai_patch.get(p, {}).get("key")
        if k is not None and k.startswith("••••"):
            ai_patch[p].pop("key")
    study = patch.get("study", {})
    if "daily_new" in study:
        study["daily_new"] = max(0, min(200, int(study["daily_new"])))
    if "session_size" in study:
        study["session_size"] = max(5, min(200, int(study["session_size"])))
    store.save_config(patch)
    return store.public_config()


@app.post("/api/settings/test")
def api_test_ai():
    try:
        return {"ok": True, "sample": ai.test_connection(store.load_config())}
    except ai.AIError as e:
        return {"ok": False, "error": str(e)}


# ---------- 话题 ----------

@app.get("/api/topics")
def api_topics():
    return store.list_topics()


@app.post("/api/topics")
def api_add_topic(body: dict = Body(...)):
    en, zh = str(body.get("en", "")).strip(), str(body.get("zh", "")).strip()
    if not en and not zh:
        err("请填写话题名称")
    return store.add_topic(en or zh, zh or en)


@app.post("/api/topics/{slug}/expand")
def api_expand_topic(slug: str, body: dict = Body(default={})):
    """让 AI 给这个话题再出一批素材（不和库里已有的重复），审校后交给网页挑选"""
    t = store.get_topic(slug)
    if not t:
        err("没有这个话题", 404)
    reason = ai_reason()
    if reason:
        err(f"AI 扩充需要先配置 AI：{reason}")
    kind = body.get("kind") or ""
    if slug == "general":
        counts = {"pattern": 6, "linker": 6}
    else:
        counts = {"collocation": 10, "idea": 4}
    if kind in store.KINDS:
        counts = {kind: 12 if kind != "idea" else 6}
    cfg = store.load_config()
    have = [m["form"] for m in store.list_materials(slug)]
    try:
        r = ai.expand(t, have, cfg, counts)
        mats, _removed = ai.review(r["materials"], cfg)
    except ai.AIError as e:
        err(str(e))
    lib = store.norms_in_library()
    mats = [m for m in mats if store.norm(m["kind"], m["form"]) not in lib]
    if not mats:
        err("AI 这次没给出新的素材，再试一次")
    return {"items": mats}


@app.delete("/api/topics/{slug}")
def api_delete_topic(slug: str):
    store.delete_topic(slug)
    return {"ok": True}


# ---------- 素材 ----------

def clean_material(m: dict) -> dict:
    kind = str(m.get("kind", ""))
    form = re.sub(r"\s+", " ", str(m.get("form", ""))).strip()
    if kind not in store.KINDS:
        err(f"不认识的素材类型：{kind}")
    if not form:
        err("素材内容不能为空")
    slugs = store.topic_slugs()
    examples = []
    for e in m.get("examples") or []:
        en = str(e.get("en", "")).strip()
        if not en:
            continue
        answer = [str(a) for a in (e.get("answer") or []) if str(a).strip()]
        if answer and not ai.make_cloze(en, answer):
            answer = []
        examples.append({"en": en, "zh": str(e.get("zh", "")).strip(), "answer": answer})
    return {"kind": kind, "form": form[:200], "zh": str(m.get("zh", "")).strip()[:120], "examples": examples,
            "topics": [t for t in (m.get("topics") or []) if t in slugs],
            "register": str(m.get("register", "")), "alts": [str(a) for a in (m.get("alts") or [])][:5],
            "note": str(m.get("note", "")).strip()[:120]}


@app.get("/api/materials")
def api_materials(topic: str | None = None, kind: str | None = None, q: str | None = None,
                  starred: bool = False, source: str | None = None):
    return store.list_materials(topic, kind, q, starred, source)


@app.get("/api/materials/{mid}")
def api_material(mid: int):
    m = store.get_material(mid)
    if not m:
        err("没有这条素材", 404)
    return m


@app.post("/api/materials")
def api_add_materials(body: dict = Body(...)):
    doc_id = body.get("doc_id")
    source = "doc" if doc_id else ("ai" if body.get("source") == "ai" else "manual")
    added, ids = 0, []
    for m in body.get("items", []):
        mid, new = store.add_material(clean_material(m), source, doc_id)
        ids.append(mid)
        added += new
    return {"added": added, "ids": ids, "stats": store.stats()}


@app.patch("/api/materials/{mid}")
def api_update_material(mid: int, patch: dict = Body(...)):
    if "topics" in patch:
        slugs = store.topic_slugs()
        patch["topics"] = [t for t in patch["topics"] if t in slugs]
    m = store.update_material(mid, patch)
    if not m:
        err("没有这条素材", 404)
    return m


@app.delete("/api/materials/{mid}")
def api_delete_material(mid: int):
    store.delete_material(mid)
    return {"ok": True}


# ---------- 文档 ----------

def analyse(text: str) -> dict:
    """分句，能用 AI 就拆素材；AI 用不了或出错时只分句，网页上可以手动划选"""
    sentences = ai.split_sentences(text)
    if not sentences:
        err("没找到英文句子")
    plain = {"mode": "plain", "sents": [{"sid": i, "text": s, "zh": "", "items": []} for i, s in enumerate(sentences)],
             "warnings": []}
    reason = ai_reason()
    if reason:
        plain["warnings"].append(f"{reason}，没有自动拆素材。可以在句子里划选文字，手动加入素材库。")
        return plain
    try:
        r = ai.mine(sentences, store.list_topics(), store.load_config())
    except ai.AIError as e:
        plain["warnings"].append(f"AI 出错（{e}），没有自动拆素材。可以稍后点“用 AI 重新拆”，或者手动划选。")
        return plain
    covered = {s["sid"] for s in r["sents"]}
    sents = r["sents"] + [s for s in plain["sents"] if s["sid"] not in covered]
    return {"mode": "ai", "sents": sorted(sents, key=lambda s: s["sid"]), "warnings": r["warnings"]}


@app.post("/api/docs")
async def api_add_doc(file: UploadFile | None = File(None), text: str = Form(""), title: str = Form("")):
    if file is not None and file.filename:
        try:
            body = docs.to_text(file.filename, await file.read())
        except docs.UnsupportedFile as e:
            err(str(e))
        name = file.filename
        title = title or Path(file.filename).stem
    else:
        body, name = text, ""
    body = body.strip()
    if not body:
        err("内容是空的")
    if len(body) > 300_000:
        err("文档太长了（超过 30 万字符），请分成几份")
    if not title:
        first = ai.split_sentences(body)[:1]
        title = (first[0][:40] + "…") if first and len(first[0]) > 40 else (first[0] if first else "粘贴的文字")
    import anyio

    meta = await anyio.to_thread.run_sync(analyse, body)
    doc_id = store.add_doc(title, name, body, meta)
    return {"id": doc_id, "mode": meta["mode"], "warnings": meta["warnings"]}


@app.get("/api/docs")
def api_docs():
    return store.list_docs()


@app.get("/api/docs/{doc_id}")
def api_doc(doc_id: int):
    d = store.get_doc(doc_id)
    if not d:
        err("没有这篇文档", 404)
    have = store.norms_in_library()
    for s in d["meta"].get("sents", []):
        for it in s.get("items", []):
            it["saved"] = store.norm(it["kind"], it["form"]) in have
    return {"id": d["id"], "title": d["title"], "filename": d["filename"], "chars": d["chars"], "created": d["created"],
            "mode": d["meta"].get("mode", "plain"), "sents": d["meta"].get("sents", []),
            "warnings": d["meta"].get("warnings", [])}


@app.post("/api/docs/{doc_id}/mine")
def api_remine(doc_id: int):
    d = store.get_doc(doc_id)
    if not d:
        err("没有这篇文档", 404)
    reason = ai_reason()
    if reason:
        err(reason)
    meta = analyse(d["text"])
    if meta["mode"] != "ai":
        err(meta["warnings"][-1] if meta["warnings"] else "AI 没能拆出素材")
    store.update_doc_meta(doc_id, meta)
    return {"ok": True, "warnings": meta["warnings"]}


@app.delete("/api/docs/{doc_id}")
def api_delete_doc(doc_id: int):
    store.delete_doc(doc_id)
    return {"ok": True}


@app.post("/api/pick")
def api_pick(body: dict = Body(...)):
    """划选的文字：有 AI 就判断类型、给中文；没有就按长度猜个类型，让用户自己填"""
    sel = re.sub(r"\s+", " ", str(body.get("selection", ""))).strip()
    sentence = re.sub(r"\s+", " ", str(body.get("sentence", ""))).strip()
    if not sel:
        err("没有选中文字")
    if len(sel) > 300:
        err("选得太长了，一次最多 300 个字符")
    guess = "idea" if len(sel.split()) > 10 else "collocation"
    cand = {"kind": guess, "form": sel, "zh": "", "topics": [], "register": "", "alts": [], "note": "", "sentence_zh": ""}
    reason = ai_reason()
    if not reason and sentence:
        try:
            cand = ai.describe(sel, sentence, store.list_topics(), store.load_config())
        except ai.AIError as e:
            reason = str(e)
    answer = [sel] if ai.make_cloze(sentence, [sel]) else []
    cand["examples"] = [{"en": sentence, "zh": cand.get("sentence_zh", ""), "answer": answer if cand["kind"] != "idea" else []}] if sentence else []
    cand["saved"] = store.norm(cand["kind"], cand["form"]) in store.norms_in_library()
    return {"candidate": cand, "ai": reason is None, "reason": reason}


# ---------- 回忆练习 ----------

def make_card(m: dict) -> dict:
    card = {"id": m["id"], "kind": m["kind"], "kindName": m["kindName"], "form": m["form"], "zh": m["zh"],
            "note": m.get("note", ""), "alts": m.get("alts", []), "isNew": m["state"] is None, "topics": m["topics"],
            "type": "flash", "segments": None, "sentence": "", "sentenceZh": "", "answer": []}
    if m["kind"] in store.CLOZE_KINDS:
        valid = [e for e in m["examples"] if e.get("answer") and ai.make_cloze(e["en"], e["answer"])]
        if valid:
            reps = (m["state"] or {}).get("reps", 0)
            e = valid[reps % len(valid)]
            card.update(type="cloze", segments=ai.make_cloze(e["en"], e["answer"]), sentence=e["en"],
                        sentenceZh=e.get("zh", ""), answer=e["answer"])
            return card
    if m["examples"]:
        e = m["examples"][0]
        card.update(sentence=e["en"], sentenceZh=e.get("zh", ""))
    return card


@app.post("/api/recall/plan")
def api_recall_plan(body: dict = Body(default={})):
    """定这一轮练哪些：到期的先来，再加今天还没用完的新素材额度。scope：auto / review / new"""
    topic, kind, scope = body.get("topic") or None, body.get("kind") or None, body.get("scope", "auto")
    cfg = store.load_config()["study"]
    size = int(body.get("size") or cfg["session_size"])
    due = store.due_materials(topic, kind)
    fresh = store.new_materials(topic, kind)
    quota = max(0, cfg["daily_new"] - store.learned_today())
    if scope == "review":
        picked = due
    elif scope == "new":
        picked = fresh
    else:
        picked = due + fresh[:quota]
    picked = picked[:size]
    ids = [m["id"] for m in picked]
    random.shuffle(ids)
    return {"ids": ids, "due": len(due), "new": len(fresh), "quota": quota}


@app.post("/api/recall/cards")
def api_recall_cards(body: dict = Body(...)):
    cards = []
    for mid in body.get("ids", [])[:200]:
        m = store.get_material(int(mid))
        if m:
            cards.append(make_card(m))
    return cards


@app.post("/api/recall/answer")
def api_recall_answer(body: dict = Body(...)):
    mid = int(body["id"])
    if body.get("grade"):
        g = int(body["grade"])
    else:
        g = srs.grade(int(body.get("mistakes", 0)), bool(body.get("peeked")), bool(body.get("hinted")))
    r = store.record(mid, g, "recall")
    if not r:
        err("没有这条素材", 404)
    return r


# ---------- 改写升级 ----------

REWRITE_KINDS = ("collocation", "pattern", "linker")


def rewrite_pool(topic: str | None) -> list[dict]:
    rows = [m for m in store.list_materials(topic) if m["kind"] in REWRITE_KINDS]
    day = srs.today()

    def rank(m):  # 学过且快到期的最该练；其次收藏的、自己收集的
        st = m["state"]
        if st:
            return (0, st["due"] > day, st["due"])
        return (1, not m["starred"], m["source"] == "builtin")
    return sorted(rows, key=rank)


@app.post("/api/rewrite/next")
def api_rewrite_next(body: dict = Body(default={})):
    reason = ai_reason()
    if reason:
        err(f"改写练习需要 AI 批改：{reason}")
    cfg = store.load_config()
    exclude = set(body.get("exclude") or [])
    if body.get("id"):
        m = store.get_material(int(body["id"]))
        if not m:
            err("没有这条素材", 404)
        pool = [m]
    else:
        pool = [m for m in rewrite_pool(body.get("topic") or None) if m["id"] not in exclude]
        if not pool:
            err("这个范围里没有可以改写的素材（搭配、句型、衔接）")
        head = pool[:12]
        random.shuffle(head)
        pool = head
    target = pool[0]
    task = store.next_task(target["id"], "rewrite")
    if not task or task["used"] > 0:
        # 顺手给后面几条也出好题，下次就不用等
        have = store.task_counts("rewrite")
        batch = [target] + [m for m in pool[1:] if not have.get(m["id"])][:4]
        try:
            made = ai.rewrite_tasks([{"id": m["id"], "kind": m["kind"], "form": m["form"], "zh": m["zh"]} for m in batch], cfg)
        except ai.AIError as e:
            err(str(e))
        for mid, tasks in made.items():
            store.add_tasks(mid, "rewrite", tasks)
        task = store.next_task(target["id"], "rewrite")
        if not task:
            err("AI 没能出题，换一条试试")
    store.use_task(task["id"])
    return {"taskId": task["id"], "material": target, "plain": task["data"]["plain"], "zh": task["data"]["zh"]}


@app.post("/api/rewrite/check")
def api_rewrite_check(body: dict = Body(...)):
    task = store.get_task(int(body.get("taskId", 0)))
    answer = str(body.get("answer", "")).strip()
    if not task:
        err("题目不存在了，换一题")
    if not answer:
        err("先写一句")
    m = store.get_material(task["mid"])
    if not m:
        err("这条素材已经删了", 404)
    try:
        fb = ai.check_rewrite(m, task["data"], answer, store.load_config())
    except ai.AIError as e:
        err(str(e))
    g = {3: srs.GOOD, 2: srs.HARD}.get(fb["score"], srs.AGAIN)
    store.record(m["id"], g, "rewrite")
    return {**fb, "model": task["data"]["model"], "grade": g}


# ---------- 话题演练 ----------

def suggestions_for(topic: str, n_col: int = 5, n_idea: int = 3, n_pat: int = 2, n_link: int = 2) -> list[dict]:
    def pick(rows, n):
        rows = sorted(rows, key=lambda m: (m["state"] is None, not m["starred"], m["source"] == "builtin", random.random()))
        return rows[:n]
    mine = store.list_materials(topic)
    general = store.list_materials("general") if topic != "general" else []
    by = lambda rows, k: [m for m in rows if m["kind"] == k]  # noqa: E731
    out = pick(by(mine, "collocation"), n_col) + pick(by(mine, "idea"), n_idea)
    pats = by(mine, "pattern") or by(general, "pattern")
    links = by(mine, "linker") or by(general, "linker")
    return out + pick(pats, n_pat) + pick(links, n_link)


@app.get("/api/drill/start")
def api_drill_start(topic: str, fresh: bool = False):
    t = store.get_topic(topic)
    if not t:
        err("没有这个话题", 404)
    prompts = t["prompts"]
    if (not prompts or fresh) and not ai_reason():
        try:
            made = ai.essay_prompts(t, store.load_config())
            if made:
                prompts = prompts + [p for p in made if p["en"] not in {x["en"] for x in prompts}]
                store.set_topic_prompts(topic, prompts)
        except ai.AIError:
            pass
    if not prompts:
        prompts = [{"en": f"Discuss the most important issues related to {t['en'].lower()} today, and give your own opinion.",
                    "zh": f"讨论当今与“{t['zh']}”相关的最重要问题，并给出你的观点。"}]
    prompt = prompts[-1] if fresh else random.choice(prompts)
    return {"topic": t, "prompt": prompt, "prompts": prompts, "suggestions": suggestions_for(topic),
            "aiReady": ai_reason() is None, "aiReason": ai_reason()}


def library_hits(text: str, skip: set[int]) -> list[dict]:
    """没在建议里、但文章里确实用到的库内搭配和衔接（只认没有占位符的，原样出现才算）"""
    low = " " + re.sub(r"\s+", " ", text.lower().replace("’", "'")) + " "
    hits = []
    for m in store.list_materials():
        if m["id"] in skip or m["kind"] not in ("collocation", "linker"):
            continue
        f = m["form"].lower()
        if re.search(r"\b(sb|sth|one's|oneself|x|y)\b", f) or len(f) < 6:
            continue
        if re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", low):
            hits.append({"id": m["id"], "form": m["form"], "kind": m["kind"], "kindName": m["kindName"], "zh": m["zh"]})
    return hits[:12]


@app.post("/api/drill/check")
def api_drill_check(body: dict = Body(...)):
    text = str(body.get("text", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    topic = str(body.get("topic", ""))
    if len(text.split()) < 20:
        err("至少写 20 个词再提交")
    if len(text) > 6000:
        err("太长了，一次最多约 1000 词")
    reason = ai_reason()
    if reason:
        err(f"话题演练需要 AI 批改：{reason}")
    sugg = [m for m in (store.get_material(int(i)) for i in body.get("ids", [])) if m]
    try:
        fb = ai.check_drill(prompt, text, sugg, store.load_config())
    except ai.AIError as e:
        err(str(e))
    for u in fb["used"]:
        store.record(u["id"], srs.GOOD if u["ok"] else srs.HARD, "drill")
        m = next((x for x in sugg if x["id"] == u["id"]), None)
        if m:
            u.update(form=m["form"], kind=m["kind"], kindName=m["kindName"])
    fb["alsoUsed"] = library_hits(text, {m["id"] for m in sugg})
    fb["words"] = len(text.split())
    fb["id"] = store.add_drill(topic, prompt, text, fb)
    return fb


@app.get("/api/drills")
def api_drills():
    return store.list_drills()


@app.delete("/api/drills/{did}")
def api_delete_drill(did: int):
    store.delete_drill(did)
    return {"ok": True}


# ---------- 导出 / 导入 ----------

@app.get("/api/export/markdown")
def api_export_md(topic: str | None = None, kind: str | None = None, starred: bool = False, source: str | None = None):
    rows = store.list_materials(topic, kind, None, starred, source)
    topics = {t["slug"]: t for t in store.list_topics()}
    title = f"川文素材 · {topics[topic]['zh']}" if topic in topics else "川文素材"
    lines = [f"# {title}", "", f"导出于 {datetime.now():%Y-%m-%d %H:%M}，共 {len(rows)} 条。", ""]
    for k, name in store.KINDS.items():
        part = [m for m in rows if m["kind"] == k]
        if not part:
            continue
        lines += [f"## {name}（{len(part)}）", ""]
        for m in part:
            head = f"- **{m['form']}**" + (f"　{m['zh']}" if m["zh"] else "")
            lines.append(head)
            if m["kind"] != "idea":
                for e in m["examples"][:2]:
                    lines.append(f"  - {e['en']}" + (f"（{e['zh']}）" if e.get("zh") else ""))
            if m["alts"]:
                lines.append(f"  - 也可以说：{'；'.join(m['alts'])}")
            if m.get("note"):
                lines.append(f"  - 提示：{m['note']}")
            if m.get("user_note"):
                lines.append(f"  - 批注：{m['user_note']}")
        lines.append("")
    return download("\n".join(lines), f"{title}-{datetime.now():%Y%m%d}.md", "text/markdown; charset=utf-8")


@app.get("/api/export/backup")
def api_export_backup():
    return download(json.dumps(store.export_backup(), ensure_ascii=False, indent=1),
                    f"川文学习记录-{datetime.now():%Y%m%d}.json", "application/json")


@app.post("/api/import/backup")
async def api_import_backup(file: UploadFile = File(...)):
    try:
        data = json.loads(await file.read())
        return store.import_backup(data)
    except (ValueError, json.JSONDecodeError) as e:
        err(str(e) if isinstance(e, ValueError) and not isinstance(e, json.JSONDecodeError) else "文件不是合法的 JSON")


@app.get("/api/export/wordbook")
def api_export_wordbook(source: str | None = "mine"):
    """导出成川词能导入的 wordbook/1（只导搭配、衔接；论点和句型不适合单词本）"""
    entries = []
    for m in store.list_materials(source=source):
        if m["kind"] not in ("collocation", "linker"):
            continue
        note = "\n\n".join(e["en"] for e in m["examples"][:2])
        entries.append({"word": m["form"], "note": note, "tags": ["川文", m["kindName"]], "customTrans": m["zh"], "groups": []})
    data = {"format": "wordbook/1", "name": "川文搭配", "desc": "写作好搭配和衔接",
            "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "appVersion": "1.0", "languageCode": "en", "entries": entries}
    return download(json.dumps(data, ensure_ascii=False, indent=2), f"川文搭配-{datetime.now():%Y%m%d}.json", "application/json")


@app.post("/api/quit")
def api_quit(request: Request):
    """关闭川文。要求自定义请求头：别的网页跨域发不了带这个头的请求，没法偷偷关掉它。"""
    if request.headers.get("x-chuanwen") != "1":
        err("不允许", 403)

    def stop() -> None:
        if UVICORN is not None:
            UVICORN.should_exit = True
        else:
            os.kill(os.getpid(), signal.SIGINT)
    threading.Timer(0.4, stop).start()
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(_req, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")
