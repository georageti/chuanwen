"""假的 OpenAI 兼容接口：按系统提示词判断是哪种请求，返回固定格式的内容。
测试用，也可以单独跑起来给预览用：.venv/bin/python tests/fake_ai.py 8899
设置里选“其他兼容接口”，地址 http://127.0.0.1:8899/v1，Key 随便填，模型 fake。"""
from __future__ import annotations

import json
import re
import sys

from fastapi import Body, FastAPI

app = FastAPI()
CALLS: list[str] = []  # 测试里看调用了哪些
EXPAND_N = 0  # 生成素材的编号，每次不重复


def words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", s)


def mine(prompt: str) -> dict:
    out = []
    for m in re.finditer(r"^\[(\d+)\] (.+)$", prompt, re.M):
        sid, sent = int(m.group(1)), m.group(2)
        w = words(sent)
        items = []
        if len(w) >= 4:
            items.append({"type": "collocation", "text": [f"{w[1]} {w[2]}"], "form": f"{w[1]} {w[2]}".lower(), "zh": "模拟搭配",
                          "topics": ["education", "nonsense"], "register": "neutral", "alts": ["alt one"], "note": ""})
        if sent.lower().startswith("there is no denying that"):
            items.append({"type": "pattern", "text": ["There is no denying that"], "form": "There is no denying that ...",
                          "zh": "不可否认", "topics": ["general"], "register": "formal", "alts": [], "note": ""})
        items.append({"type": "collocation", "text": ["this phrase is not in the sentence"], "form": "ghost", "zh": "",
                      "topics": [], "register": "neutral", "alts": [], "note": ""})  # 对不上原文，应该被丢掉
        items.append({"type": "idea", "text": [], "form": f"Idea from sentence {sid}.", "zh": "模拟论点",
                      "topics": ["technology"], "register": "neutral", "alts": [], "note": ""})
        out.append({"sid": sid, "zh": f"第 {sid} 句的模拟翻译。", "items": items})
    return {"sentences": out}


def reply(system: str, prompt: str) -> dict:
    if "REUSE in their own essays" in system:
        CALLS.append("mine")
        return mine(prompt)
    if "selected some words" in system:
        CALLS.append("describe")
        d = json.loads(prompt)
        return {"type": "collocation", "form": d["selection"].lower(), "zh": "模拟释义", "topics": ["media"],
                "register": "neutral", "alts": [], "note": "", "sentence_zh": "模拟整句翻译"}
    if "rewriting exercises" in system:
        CALLS.append("rewrite")
        items = []
        for line in prompt.splitlines()[1:]:
            t = json.loads(line)
            items.append({"id": t["id"], "tasks": [
                {"plain": "Many people think this problem is very big.", "zh": "许多人认为这个问题很严重。",
                 "model": f"Many people believe this problem calls for us to {t['target']}."},
                {"plain": "The city has too many cars now.", "zh": "这座城市现在车太多了。",
                 "model": f"The city needs to {t['target']}."}]})
        return {"items": items}
    if "mark a rewriting exercise" in system:
        CALLS.append("check_rewrite")
        d = json.loads(prompt)
        target = [w.lower() for w in words(d["target"]) if w.lower() not in {"sb", "sth", "x", "y"}]
        used = all(w in d["learner_answer"].lower() for w in target)
        return {"used": used, "score": 3 if used else 0, "corrected": d["learner_answer"], "comment_zh": "模拟点评"}
    if "build a writing-material bank" in system:
        CALLS.append("expand")
        d = json.loads(prompt)
        n = d["requested"]
        global EXPAND_N
        base_n = EXPAND_N
        EXPAND_N += n["collocations"] + n["ideas"] + n["patterns"] + n["linkers"]
        cols = [{"form": f"mitigate the risk number {base_n + i}", "zh": "降低风险", "example": f"We must [mitigate the risk number {base_n + i}] soon.",
                 "example_zh": "我们必须[尽快]降低风险。", "alts": ["reduce the risk"]} for i in range(n["collocations"])]
        cols.append({"form": "puts pressure on", "zh": "施压", "example": "It puts [pressure on] us.", "example_zh": "", "alts": []})  # 漏了动词，应丢掉
        cols.append({"form": "not in example", "zh": "x", "example": "The [missing words] here.", "example_zh": "", "alts": []})  # 对不上，应丢掉
        ideas = [{"en": f"Idea number {base_n + i} explains why policy works.", "zh": "模拟论点"} for i in range(n["ideas"])]
        pats = [{"form": f"Pattern {base_n + i} X, Y", "zh": "句型", "example": f"[Pattern {base_n + i}] cities, people.", "example_zh": "", "note": ""} for i in range(n["patterns"])]
        links = [{"form": f"Linker number {base_n + i}", "zh": "衔接", "example": f"[Linker number {base_n + i}], it works.", "example_zh": "", "alts": []} for i in range(n["linkers"])]
        return {"collocations": cols, "patterns": pats, "linkers": links, "ideas": ideas,
                "prompts": [{"en": "Extra prompt?", "zh": "题"}][: n["prompts"]]}
    if "meticulous editor" in system:
        CALLS.append("review")
        rows = [json.loads(line) for line in prompt.splitlines()]
        return {"items": [{"i": r["i"], "keep": r["i"] != 1, "zh": r["zh"] + "（复核）", "alts": [], "example_ok": True} for r in rows]}
    if "Chinese-to-English translation exercises" in system:
        CALLS.append("trans_gen")
        d = json.loads(prompt)
        lib = d["library_expressions"]
        items = []
        for length, n in d["requested"].items():
            for i in range(n):
                t = lib[i % len(lib)] if lib else None
                items.append({"length": length, "zh": f"这是一道{length}模拟翻译题，第{i}道，治标不治本。",
                              "ref_en": f"This is a mock sentence that uses {t['form'] if t else 'nothing'} wisely.",
                              "targets": [t["id"], 999999] if t else [],
                              "hints": [{"zh": "治标不治本", "en": ["treat the symptoms, not the cause"]}]})
        items.append({"length": "huge", "zh": "坏题", "ref_en": "x", "targets": [], "hints": []})  # 长度不对，应丢掉
        items.append({"length": "short", "zh": "no chinese here", "ref_en": "x", "targets": [], "hints": []})  # 没中文，应丢掉
        return {"items": items}
    if "You mark a Chinese-to-English translation" in system:
        CALLS.append("trans_check")
        d = json.loads(prompt)
        ans = d["learner_translation"]
        first = " ".join(ans.split()[:2])
        return {"score": 4, "accuracy": 5, "naturalness": 3, "summary_zh": "模拟总评",
                "corrected": ans.replace("very big", "major"), "better": ["A more idiomatic version.", "Another way to say it."],
                "chunks": [{"yours": first, "options": ["Option A", "Option B"], "why_zh": "模拟理由"},
                           {"yours": "not in answer", "options": ["x"], "why_zh": "应该被丢掉"}],
                "good": [{"quote": first, "why_zh": "好"}],
                "expressions": [{"type": "collocation", "form": "treat the symptoms", "zh": "治标",
                                 "example": "Such policies only treat the symptoms."},
                                {"type": "bogus", "form": "x", "zh": "", "example": ""}],
                "targets_used": [{"id": t["id"], "used": True, "ok": True} for t in d["target_expressions"]] + [{"id": 999999, "used": True, "ok": True}]}
    if "pasted something they want to add" in system:
        CALLS.append("recognize")
        text = prompt.strip()
        if len(text.split()) <= 6:
            return {"items": [{"type": "collocation", "form": text.lower(), "zh": "识别出的搭配", "topics": ["health", "bogus"],
                               "register": "neutral", "alts": ["alt"], "note": "",
                               "example": f"Governments should [{text}] as soon as possible.", "example_zh": "政府应尽快[这样做]。"}]}
        return {"items": [
            {"type": "collocation", "form": "pose a threat to sth", "zh": "构成威胁", "topics": [], "register": "formal", "alts": [], "note": "",
             "example": "Plastic waste [poses a threat to] marine life.", "example_zh": "塑料垃圾威胁海洋生物。"},
            {"type": "pattern", "form": "Far from being X, Y", "zh": "远非", "topics": ["general"], "register": "formal", "alts": [], "note": "",
             "example": "Far from being cheap, it is costly.", "example_zh": ""},
            {"type": "idea", "form": "Plastic pollution harms marine ecosystems.", "zh": "塑料污染危害海洋生态", "topics": ["environment"],
             "register": "neutral", "alts": [], "note": "", "example": "", "example_zh": ""},
            {"type": "collocation", "form": "pose a threat to sth", "zh": "重复", "topics": [], "register": "formal", "alts": [], "note": "",
             "example": "x", "example_zh": ""}]}
    if "essay questions" in system:
        CALLS.append("prompts")
        return {"prompts": [{"en": "Some say X is good. Discuss both views.", "zh": "模拟题目"}]}
    if "writing examiner and coach" in system:
        CALLS.append("drill")
        d = json.loads(prompt)
        text = d["learner_text"]
        used = []
        for m in d["suggested_expressions"]:
            w = [x for x in words(m["form"]) if x.lower() not in {"sb", "sth", "x", "y"}]
            if w and all(x.lower() in text.lower() for x in w):
                i = text.lower().index(w[0].lower())
                used.append({"id": m["id"], "quote": text[i : i + len(w[0])], "ok": True, "comment_zh": "用得好"})
        used.append({"id": 999999, "quote": "not here", "ok": True, "comment_zh": "假的，应该被丢掉"})
        first = " ".join(text.split()[:3])
        return {"band": 6.3, "summary_zh": "模拟总评", "used": used,
                "issues": [{"quote": first, "fix": first.upper(), "why_zh": "模拟问题"},
                           {"quote": "not in text", "fix": "x", "why_zh": "应该被丢掉"}],
                "improved": "An improved paragraph."}
    CALLS.append("unknown")
    return {}


BODIES: list[dict] = []  # 测试里看请求体


@app.post("/v1/chat/completions")
def chat(body: dict = Body(...)):
    BODIES.append(body)
    if body.get("model") == "strict-model" and ("response_format" in body or "reasoning_effort" in body):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": {"message": "Unknown field: response_format"}}, status_code=400)
    if body.get("model") == "missing-model":
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": {"message": "model not found"}}, status_code=404)
    msgs = body["messages"]
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    prompt = next((m["content"] for m in msgs if m["role"] == "user"), "")
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(reply(system, prompt), ensure_ascii=False)}}]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8899, log_level="warning")
