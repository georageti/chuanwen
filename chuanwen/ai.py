"""AI：从文章里拆写作素材、出改写题并批改、批改话题演练。

支持三种来源（和川听同一套）：
  deepseek / openai：OpenAI 兼容的 /chat/completions 接口（DeepSeek 国内直连；也能接 Kimi、通义等）
  anthropic：Claude，用官方 anthropic SDK，JSON Schema 结构化输出
  none：不用 AI（只能手动划选加素材、练回忆）
AI 给出的每一处原文片段都要核对：必须按顺序出现在原句里，对不上的丢掉。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

import httpx


class AIError(RuntimeError):
    pass


KIND_SET = {"collocation", "pattern", "idea", "linker"}
REGISTERS = {"formal", "neutral", "informal"}


# ---------- 挖空 ----------

def make_cloze(sentence: str, pieces: list[str]) -> list[dict] | None:
    """把句子按答案切开：[{t: '文字'}, {blank: '答案'}, ...]；答案找不到就返回 None"""
    segs, pos = [], 0
    pieces = [p.strip() for p in pieces if p and p.strip()]
    if not pieces:
        return None
    for p in pieces:
        m = re.compile(r"(?<![A-Za-z])" + re.escape(p).replace(r"\ ", r"\s+") + r"(?![A-Za-z])", re.I).search(sentence, pos)
        if not m:
            return None
        if m.start() > pos:
            segs.append({"t": sentence[pos : m.start()]})
        segs.append({"blank": m.group(0)})
        pos = m.end()
    if pos < len(sentence):
        segs.append({"t": sentence[pos:]})
    return segs


def spans_of(segs: list[dict]) -> list[list[int]]:
    pos, spans = 0, []
    for x in segs:
        text = x.get("blank", x.get("t", ""))
        if "blank" in x:
            spans.append([pos, pos + len(text)])
        pos += len(text)
    return spans


# ---------- 挖空只挖素材本身 ----------
# AI 有时把填空位的词也算进片段（"spares employees the daily commute" 对 "spare sb sth"），
# 练回忆时就得把 employees 这种猜不到的词也拼出来。这里只留和素材里的词对得上的部分。

SLOT_WORDS = {"sb", "sth", "one's", "oneself", "somebody", "something", "someone", "x", "y", "do", "doing", "..."}
BE_FORMS = {"be", "is", "are", "was", "were", "been", "being", "am", "'s", "'re"}


def _word_match(token: str, w: str) -> bool:
    t = token.lower().replace("’", "'")
    if w == "be":
        return t in BE_FORMS
    if t == w:
        return True
    if len(w) >= 4:
        return t.startswith(w[: max(4, len(w) - 2)])  # spare→spares, double→doubles, accelerate→accelerated
    return False


def trim_to_form(sentence: str, segs: list[dict], form: str) -> list[dict] | None:
    words = [w for w in re.findall(r"[a-z]+(?:['’-][a-z]+)*", form.lower().replace("’", "'")) if w not in SLOT_WORDS]
    if not words:
        return None
    kept: list[str] = []
    for piece in (x["blank"] for x in segs if "blank" in x):
        run: list[str] = []
        for tok in piece.split():
            core = tok.strip(".,;:!?\"“”()")
            if core and any(_word_match(core, w) for w in words):
                run.append(core)
            else:
                if run:
                    kept.append(" ".join(run))
                run = []
        if run:
            kept.append(" ".join(run))
    if not kept:
        return None
    new = make_cloze(sentence, kept)
    # 素材的第一个实词（4 个字母以上，通常是动词）要对上；对不上多半是不规则变形（took / take），保留 AI 原来的。
    # 后面的实词可以对不上：AI 有时把泛指的词写进素材（"take up time" 对 "take up two hours"）。
    content = [w for w in words if len(w) >= 4]
    matched = lambda w: any(_word_match(t, w) for k in kept for t in k.split())  # noqa: E731
    ok = not content or matched(content[0])
    return new if new and ok else None


# ---------- 分句 ----------

ABBR = {"mr", "mrs", "ms", "dr", "st", "vs", "e.g", "i.e", "u.s", "u.k", "no", "prof", "etc"}
BOUNDARY = re.compile(r"[.!?。！？][\"'”’)]?(\s+)(?=[\"'“‘(]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n|\n(?=\s*[-*•\d])", text):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        start = 0
        for m in BOUNDARY.finditer(para):
            word = re.search(r"([A-Za-z.]+)\.$", para[: m.start() + 1])
            if word and word.group(1).lower() in ABBR:
                continue  # Mr. / e.g. 这类缩写不断句
            out.append(para[start : m.start(1)])
            start = m.end(1)
        out.append(para[start:])
    out = [re.sub(r"^[-*•]\s+", "", p.strip()) for p in out]
    return [p for p in out if len(re.findall(r"[A-Za-z]+", p)) >= 3]  # 太短的标题、页码不要


# ---------- 调 AI ----------

def _call_openai_compatible(base: str, key: str, model: str, prompt: str, system: str,
                            temperature: float = 0.4, max_tokens: int = 6000) -> str:
    url = base.rstrip("/") + "/chat/completions"  # 接口地址填到 /v1 为止（DeepSeek 可以不带 /v1）
    try:
        r = httpx.post(url, timeout=180, headers={"Authorization": f"Bearer {key}"}, json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
    except httpx.HTTPError as e:
        raise AIError(f"连不上 {base}：{e.__class__.__name__}") from e
    if r.status_code == 401:
        raise AIError("API Key 不对，或者已失效")
    if r.status_code == 402:
        raise AIError("账户余额不足")
    if r.status_code >= 400:
        raise AIError(f"AI 接口报错 {r.status_code}：{r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(key: str, model: str, prompt: str, system: str, schema: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=240, max_retries=2)
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],  # 被安全分类器误拦时，由服务端自动换模型重试
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Claude API Key 不对，或者已失效") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("这个 Key 没有权限调用该模型") from e
    except anthropic.NotFoundError as e:
        raise AIError(f"找不到模型 {model}") from e
    except anthropic.RateLimitError as e:
        raise AIError("请求太频繁或额度用完了，稍后再试") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude 接口报错 {e.status_code}：{e.message[:200]}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("连不上 Claude（国内需要开代理）") from e
    if resp.stop_reason == "refusal":
        raise AIError("Claude 拒绝了这次请求")
    return next((b.text for b in resp.content if b.type == "text"), "")


def unavailable_reason(cfg: dict) -> str | None:
    ai = cfg["ai"]
    provider = ai["provider"]
    if provider == "none":
        return "设置里选的是“不用 AI”"
    if provider == "anthropic":
        return None if ai["anthropic"]["key"] else "还没有填 Claude 的 API Key"
    if provider in ("deepseek", "openai"):
        p = ai[provider]
        if not p["key"]:
            return f"还没有填 {'DeepSeek' if provider == 'deepseek' else '接口'} 的 API Key"
        if not p.get("base") or not p.get("model"):
            return "请在设置里填写接口地址和模型名"
        return None
    return f"未知的 AI 来源：{provider}"


def _call(cfg: dict, prompt: str, system: str, schema: dict, temperature: float = 0.4, max_tokens: int = 6000) -> str:
    reason = unavailable_reason(cfg)
    if reason:
        raise AIError(reason)
    ai = cfg["ai"]
    if ai["provider"] == "anthropic":
        p = ai["anthropic"]
        return _call_anthropic(p["key"], p["model"] or "claude-opus-5-5", prompt, system, schema)
    p = ai[ai["provider"]]
    return _call_openai_compatible(p["base"], p["key"], p["model"], prompt, system, temperature, max_tokens)


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("AI 返回的不是合法 JSON") from e
    if not isinstance(data, dict):
        raise AIError("AI 返回的格式不对")
    return data


def ask(cfg: dict, prompt: str, system: str, schema: dict, **kw) -> dict:
    """测试里替换这个函数来模拟 AI"""
    return _parse(_call(cfg, prompt, system, schema, **kw))


def _pieces(v) -> list[str]:
    if isinstance(v, str):
        v = [x.strip() for x in v.split("...")]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _form(kind: str, form: str) -> str:
    form = re.sub(r"\s+", " ", form).strip()
    if kind != "idea":
        form = re.sub(r"(?<!\.)\.$", "", form)  # 搭配末尾的句号去掉，省略号留着
    return form[:200]


def _clean_item(u: dict, topics: set[str]) -> dict:
    kind = str(u.get("type", "")).strip().lower()
    reg = str(u.get("register", "")).strip().lower()
    return {
        "kind": kind,
        "form": _form(kind, str(u.get("form", ""))),
        "zh": str(u.get("zh", "")).strip()[:60],
        "topics": [t for t in (u.get("topics") or []) if isinstance(t, str) and t in topics][:3],
        "register": reg if reg in REGISTERS else "",
        "alts": [str(a).strip() for a in (u.get("alts") or []) if str(a).strip()][:3],
        "note": str(u.get("note", "")).strip()[:60],
    }


def _topic_lines(topics: list[dict]) -> str:
    return "; ".join(f"{t['slug']} ({t['en']})" for t in topics)


# ---------- 拆素材 ----------

MINE_SYSTEM = """You help a Chinese learner who is preparing for IELTS / postgraduate-exam English writing.
From an English text, pick out material the learner can REUSE in their own essays.

For every numbered sentence return:
- zh: a natural, faithful Simplified Chinese translation of the sentence.
- items: reusable material in this sentence (often 0-3; be selective, quality over quantity):
  * collocation: natural word partnerships worth stealing — verb+noun (curb emissions), adjective+noun
    (a pressing concern), adverb+adjective (deeply entrenched), verb+preposition, noun+of+noun.
    Skip trivial ones (make money, very good), proper nouns and topic-specific jargon.
  * pattern: a reusable sentence frame (cleft, inversion, concession, comparison, "the more ... the more").
  * linker: a discourse marker more advanced than and / but / so / because / also.
  * idea: an argument, example, cause, effect or statistic that could support an essay on some topic.

Fields for each item:
- type: collocation | pattern | linker | idea
- text: the exact words copied from the sentence, as an array of pieces in order. Usually one piece.
  Copy only the words that belong to the expression itself, never the words that fill an open slot:
  "spares employees the daily commute" for "spare sb sth" -> ["spares"];
  "stunt their professional growth" for "stunt one's growth" -> ["stunt", "growth"].
  For a pattern give only the fixed frame words, e.g. ["It is not", "but", "that"]. For an idea give the clause that
  carries it.
- form: the reusable dictionary form. Collocations: base verb form, with sb / sth / one's for open slots
  ("pose a threat to sth"). Patterns: X / Y for the replaceable parts ("It is not X but Y that ..."). Linkers: as is.
  Ideas: one self-contained English sentence (max 25 words) that states the point in general terms, so it can be
  reused without the original article.
- zh: short Simplified Chinese meaning (max 16 characters; for ideas the gist, max 40 characters).
- topics: 0-2 slugs from this list that the item suits: {topics}. Use "general" for items that fit any essay.
- register: formal | neutral | informal
- alts: 0-3 English alternatives with a similar meaning (empty for ideas).
- note: optional Chinese usage tip (max 24 characters), e.g. a common mistake.

Reply with JSON only."""

MINE_SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "sid": {"type": "integer"},
            "zh": {"type": "string"},
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["collocation", "pattern", "linker", "idea"]},
                    "text": {"type": "array", "items": {"type": "string"}},
                    "form": {"type": "string"},
                    "zh": {"type": "string"},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "register": {"type": "string", "enum": ["formal", "neutral", "informal"]},
                    "alts": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["type", "text", "form", "zh", "topics", "register", "alts", "note"],
                "additionalProperties": False,
            }},
        },
        "required": ["sid", "zh", "items"],
        "additionalProperties": False,
    }}},
    "required": ["sentences"],
    "additionalProperties": False,
}

MINE_EXAMPLE = {"sentences": [{"sid": 1, "zh": "不可否认，快速的城市化给公共服务带来了巨大压力。", "items": [
    {"type": "pattern", "text": ["There is no denying that"], "form": "There is no denying that ...", "zh": "不可否认……",
     "topics": ["general"], "register": "formal", "alts": ["Undeniably, ..."], "note": ""},
    {"type": "collocation", "text": ["put enormous pressure on"], "form": "put pressure on sth", "zh": "给……带来压力",
     "topics": ["urbanization"], "register": "neutral", "alts": ["place a strain on sth"], "note": "pressure 前常加 enormous / mounting"},
]}]}

MINE_CHUNK_CHARS = 2400
MINE_MAX_CHARS = 60000


def _chunks(sentences: list[str], ids: list[int], limit: int) -> list[list[int]]:
    out, cur, size = [], [], 0
    for i in ids:
        s = sentences[i]
        if cur and size + len(s) > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(s)
    if cur:
        out.append(cur)
    return out


def mine(sentences: list[str], topics: list[dict], cfg: dict) -> dict:
    """让 AI 逐句拆素材。返回 {sents: [{sid, text, zh, items: [...]}], warnings}。出错抛 AIError。"""
    warnings: list[str] = []
    budget, ids = 0, []
    for i, s in enumerate(sentences):
        if budget + len(s) > MINE_MAX_CHARS:
            warnings.append(f"文档较长，只分析了前 {MINE_MAX_CHARS // 10000} 万字符")
            break
        budget += len(s)
        ids.append(i)
    system = MINE_SYSTEM.replace("{topics}", _topic_lines(topics))
    slugs = {t["slug"] for t in topics}

    def run(chunk: list[int]) -> list[dict]:
        prompt = "\n".join(["Analyse these sentences. Return JSON shaped like this example:",
                            json.dumps(MINE_EXAMPLE, ensure_ascii=False), "", "Sentences:"]
                           + [f"[{i + 1}] {sentences[i]}" for i in chunk])
        return ask(cfg, prompt, system, MINE_SCHEMA, temperature=0.3, max_tokens=8000).get("sentences", []) or []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, _chunks(sentences, ids, MINE_CHUNK_CHARS)))

    sents = {i: {"sid": i, "text": sentences[i], "zh": "", "items": []} for i in ids}
    dropped = 0
    for entries in results:
        for e in entries if isinstance(entries, list) else []:
            try:
                sid = int(e.get("sid")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if sid not in sents:
                continue
            rec, sentence = sents[sid], sentences[sid]
            rec["zh"] = str(e.get("zh", "")).strip() or rec["zh"]
            for u in e.get("items") or []:
                if not isinstance(u, dict):
                    continue
                it = _clean_item(u, slugs)
                if it["kind"] not in KIND_SET or not it["form"]:
                    dropped += 1
                    continue
                pieces = _pieces(u.get("text"))
                segs = make_cloze(sentence, pieces) if pieces else None
                if segs and it["kind"] == "collocation":
                    segs = trim_to_form(sentence, segs, it["form"]) or segs
                if not segs and it["kind"] != "idea":
                    dropped += 1
                    continue
                if segs:
                    it["pieces"] = [x["blank"] for x in segs if "blank" in x]
                    it["spans"] = spans_of(segs)
                else:
                    it["pieces"], it["spans"] = [], []
                answer = it["pieces"] if it["kind"] != "idea" else []
                it["examples"] = [{"en": sentence, "zh": rec["zh"], "answer": answer}]
                rec["items"].append(it)
    for rec in sents.values():  # 例句的中文翻译要等整句返回后才有
        for it in rec["items"]:
            it["examples"][0]["zh"] = rec["zh"]
    missing = sum(1 for r in sents.values() if not r["zh"])
    if missing:
        warnings.append(f"有 {missing} 句 AI 没有返回分析")
    if dropped:
        warnings.append(f"AI 给出的 {dropped} 处在原文里对不上，已丢弃")
    return {"sents": [sents[i] for i in ids], "warnings": warnings}


# ---------- 划选的文字：判断类型、给出中文 ----------

DESCRIBE_SYSTEM = """A Chinese learner selected some words in an English sentence because they want to reuse them in essays.
Describe the selection as reusable writing material.
- type: collocation | pattern | linker | idea
- form: reusable dictionary form (base verb; sb / sth / one's for slots; X / Y in patterns; for an idea one general
  English sentence of max 25 words)
- zh: short Simplified Chinese meaning as used in this sentence (max 16 characters; idea max 40)
- topics: 0-2 slugs from: {topics}
- register: formal | neutral | informal
- alts: 0-3 English alternatives
- note: optional Chinese usage tip (max 24 characters)
- sentence_zh: Simplified Chinese translation of the whole sentence
Reply with JSON only."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["collocation", "pattern", "linker", "idea"]},
        "form": {"type": "string"}, "zh": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "register": {"type": "string", "enum": ["formal", "neutral", "informal"]},
        "alts": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"}, "sentence_zh": {"type": "string"},
    },
    "required": ["type", "form", "zh", "topics", "register", "alts", "note", "sentence_zh"],
    "additionalProperties": False,
}


def describe(selection: str, sentence: str, topics: list[dict], cfg: dict) -> dict:
    data = ask(cfg, json.dumps({"sentence": sentence, "selection": selection}, ensure_ascii=False),
               DESCRIBE_SYSTEM.replace("{topics}", _topic_lines(topics)), DESCRIBE_SCHEMA, temperature=0.2, max_tokens=800)
    it = _clean_item(data, {t["slug"] for t in topics})
    if it["kind"] not in KIND_SET:
        it["kind"] = "collocation"
    it["form"] = it["form"] or selection
    it["sentence_zh"] = str(data.get("sentence_zh", "")).strip()
    return it


# ---------- 改写升级 ----------

REWRITE_SYSTEM = """You set rewriting exercises for a Chinese learner of English writing (target: IELTS 7).
For each target expression, write {n} exercises. Each exercise is a PLAIN, rather flat English sentence (B1 level,
10-22 words) that says something an essay might say, where using the target expression would clearly upgrade it.
Vary the essay topics. The plain sentence must NOT already contain the target.
Fields:
- plain: the plain sentence.
- zh: a Simplified Chinese translation of what the upgraded sentence should mean.
- model: a model upgraded sentence that uses the target naturally.
Reply with JSON only, shaped like {"items": [{"id": 1, "tasks": [{"plain": "...", "zh": "...", "model": "..."}]}]}."""

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "tasks": {"type": "array", "items": {
                "type": "object",
                "properties": {"plain": {"type": "string"}, "zh": {"type": "string"}, "model": {"type": "string"}},
                "required": ["plain", "zh", "model"], "additionalProperties": False}},
        },
        "required": ["id", "tasks"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def rewrite_tasks(materials: list[dict], cfg: dict, n: int = 2) -> dict[int, list[dict]]:
    """materials: [{id, kind, form, zh}] → {id: [{plain, zh, model}]}"""
    out: dict[int, list[dict]] = {}
    for i in range(0, len(materials), 6):
        batch = materials[i : i + 6]
        prompt = "Targets:\n" + "\n".join(json.dumps({"id": m["id"], "type": m["kind"], "target": m["form"],
                                                      "meaning_zh": m.get("zh", "")}, ensure_ascii=False) for m in batch)
        data = ask(cfg, prompt, REWRITE_SYSTEM.replace("{n}", str(n)), REWRITE_SCHEMA, temperature=0.8, max_tokens=4000)
        wanted = {m["id"] for m in batch}
        for entry in data.get("items", []):
            try:
                mid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if mid not in wanted:
                continue
            for t in entry.get("tasks") or []:
                plain, model = str(t.get("plain", "")).strip(), str(t.get("model", "")).strip()
                if plain and model:
                    out.setdefault(mid, []).append({"plain": plain, "zh": str(t.get("zh", "")).strip(), "model": model})
    return out


CHECK_REWRITE_SYSTEM = """You mark a rewriting exercise for a Chinese learner of English writing.
The learner was given a plain sentence and asked to upgrade it using a target expression.
Judge the learner's answer:
- used: did they use the target expression (any correct inflection; slots like sb / sth / X / Y filled appropriately)?
- score: 3 = target used correctly, the sentence is natural and accurate, and it keeps the meaning of the plain
  sentence; 2 = target used but with a small error or awkwardness, or the answer drifts away from the plain sentence's
  meaning (an upgrade must say the same thing, not something else); 1 = target misused or serious errors;
  0 = target not used.
- corrected: the learner's sentence with the minimum changes needed to be correct and natural (same as the answer if
  nothing needs fixing).
- comment_zh: 1-2 sentences of feedback in Simplified Chinese (max 80 characters): what is good, what to fix and why.
Reply with JSON only."""

CHECK_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"used": {"type": "boolean"}, "score": {"type": "integer"}, "corrected": {"type": "string"},
                   "comment_zh": {"type": "string"}},
    "required": ["used", "score", "corrected", "comment_zh"], "additionalProperties": False,
}


def check_rewrite(material: dict, task: dict, answer: str, cfg: dict) -> dict:
    prompt = json.dumps({"target": material["form"], "target_meaning_zh": material.get("zh", ""),
                         "plain_sentence": task["plain"], "intended_meaning_zh": task.get("zh", ""),
                         "learner_answer": answer}, ensure_ascii=False)
    d = ask(cfg, prompt, CHECK_REWRITE_SYSTEM, CHECK_REWRITE_SCHEMA, temperature=0.1, max_tokens=800)
    try:
        score = max(0, min(3, int(d.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    used = bool(d.get("used"))
    if not used:
        score = 0
    return {"used": used, "score": score, "corrected": str(d.get("corrected", "")).strip() or answer,
            "comment": str(d.get("comment_zh", "")).strip()}


# ---------- 话题演练 ----------

PROMPT_SYSTEM = """Write {n} IELTS Task 2 style essay questions on the given topic. Each question is one or two
sentences and ends with a clear task (agree/disagree, discuss both views, causes and solutions, advantages and
disadvantages). Also give a Simplified Chinese translation.
Reply with JSON only, shaped like {"prompts": [{"en": "...", "zh": "..."}]}."""

PROMPT_SCHEMA = {
    "type": "object",
    "properties": {"prompts": {"type": "array", "items": {
        "type": "object", "properties": {"en": {"type": "string"}, "zh": {"type": "string"}},
        "required": ["en", "zh"], "additionalProperties": False}}},
    "required": ["prompts"], "additionalProperties": False,
}


def essay_prompts(topic: dict, cfg: dict, n: int = 3) -> list[dict]:
    d = ask(cfg, f"Topic: {topic['en']} ({topic['zh']})", PROMPT_SYSTEM.replace("{n}", str(n)), PROMPT_SCHEMA,
            temperature=0.8, max_tokens=1200)
    return [{"en": str(p.get("en", "")).strip(), "zh": str(p.get("zh", "")).strip()}
            for p in d.get("prompts", []) if str(p.get("en", "")).strip()][:n]


DRILL_SYSTEM = """You are an experienced IELTS writing examiner and coach for a Chinese learner.
The learner wrote one paragraph (or a short essay) answering the question below, and tried to use some suggested
expressions from their own library. Give honest, specific feedback.
Return:
- band: an estimated IELTS band for this writing (number, steps of 0.5, from 3 to 9). Judge Task Response too: if the
  text does not actually answer the question (off-topic, or only partly addresses it), the band must reflect that.
- summary_zh: 2-3 sentences of overall feedback in Simplified Chinese (max 140 characters). If the text drifts away
  from the question, say so first.
- used: for every suggested expression that the learner actually used: id, quote (the exact words copied from the
  learner's text), ok (true if used correctly and naturally), comment_zh (max 40 characters).
- issues: up to 6 of the most important language problems: quote (exact words copied from the learner's text),
  fix (the corrected words), why_zh (max 40 characters).
- improved: an improved version of the learner's text at about band 7.5. Keep their ideas and structure, and keep the
  expressions they used well.
Reply with JSON only."""

DRILL_SCHEMA = {
    "type": "object",
    "properties": {
        "band": {"type": "number"},
        "summary_zh": {"type": "string"},
        "used": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "quote": {"type": "string"}, "ok": {"type": "boolean"},
                           "comment_zh": {"type": "string"}},
            "required": ["id", "quote", "ok", "comment_zh"], "additionalProperties": False}},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {"quote": {"type": "string"}, "fix": {"type": "string"}, "why_zh": {"type": "string"}},
            "required": ["quote", "fix", "why_zh"], "additionalProperties": False}},
        "improved": {"type": "string"},
    },
    "required": ["band", "summary_zh", "used", "issues", "improved"],
    "additionalProperties": False,
}


def _in_text(quote: str, text: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s.lower().replace("’", "'")).strip()  # noqa: E731
    return bool(quote.strip()) and norm(quote) in norm(text)


def check_drill(prompt: str, text: str, suggestions: list[dict], cfg: dict) -> dict:
    payload = {"question": prompt, "suggested_expressions": [{"id": m["id"], "type": m["kind"], "form": m["form"]}
                                                            for m in suggestions], "learner_text": text}
    d = ask(cfg, json.dumps(payload, ensure_ascii=False), DRILL_SYSTEM, DRILL_SCHEMA, temperature=0.2, max_tokens=4000)
    ids = {m["id"] for m in suggestions}
    used, seen = [], set()
    for u in d.get("used") or []:
        try:
            mid = int(u.get("id"))
        except (TypeError, ValueError):
            continue
        quote = str(u.get("quote", "")).strip()
        if mid in ids and mid not in seen and _in_text(quote, text):
            seen.add(mid)
            used.append({"id": mid, "quote": quote, "ok": bool(u.get("ok")), "comment": str(u.get("comment_zh", "")).strip()})
    issues = []
    for x in d.get("issues") or []:
        quote = str(x.get("quote", "")).strip()
        if _in_text(quote, text):
            issues.append({"quote": quote, "fix": str(x.get("fix", "")).strip(), "why": str(x.get("why_zh", "")).strip()})
    try:
        band = round(float(d.get("band", 0)) * 2) / 2
    except (TypeError, ValueError):
        band = 0
    return {"band": max(0, min(9, band)), "summary": str(d.get("summary_zh", "")).strip(), "used": used,
            "issues": issues[:6], "improved": str(d.get("improved", "")).strip()}


def test_connection(cfg: dict) -> str:
    d = ask(cfg, "Topic: Education (教育)", PROMPT_SYSTEM.replace("{n}", "1"), PROMPT_SCHEMA, temperature=0.5, max_tokens=400)
    p = (d.get("prompts") or [{}])[0].get("en", "")
    if not p:
        raise AIError("AI 有回应，但没给出能用的内容")
    return p


# ---------- AI 扩充话题素材 ----------
# 内置素材的批量生成（scripts/expand_builtin.py）和素材库里的“AI 扩充”按钮都用这里。

EXPAND_SYSTEM = """You build a writing-material bank for Chinese learners preparing for IELTS Writing Task 2 and
postgraduate-exam English writing (target: IELTS 7). Everything must be accurate, natural, current English that a
strong essay would actually use. No rare, archaic, or overly literary expressions; no clichés like "every coin has
two sides". Never repeat anything from the "already have" list, even with small changes.

Return JSON with these arrays (give exactly the requested number of each; an array may be requested as 0):
- collocations: high-value word partnerships that lift an essay's lexical score. Prefer strong verb+noun
  (curb consumption, tackle obesity, exacerbate inequality), precise adjective+noun (a detrimental effect, a pressing
  concern), adverb+adjective and verb+preposition. Plain noun+noun labels (food prices, food industry) may be at most
  one in five, and only if genuinely essay-worthy. No two items may be near-duplicates of each other.
  No basic B1 verbs or phrasal verbs that every learner knows (lead to, give up, take steps to, be addicted to); each
  item should either use the topic's vocabulary or be an advanced (C1) expression. Each: form (dictionary form; base verb; sb / sth / one's for open slots; 2-6
  words), zh (Simplified Chinese, max 12 characters), example (one natural essay-style sentence, 10-24 words, that
  uses the collocation once, with ONLY the collocation's own words wrapped in [square brackets]; slot fillers stay
  outside the brackets, e.g. "Over-reliance on GPS may [foster dependence on] technology."), example_zh, alts (0-2
  English alternatives that could replace it in the example with the SAME meaning; a related but different term,
  e.g. food safety for food security, is wrong; leave empty if unsure).
- Every zh must be the natural Chinese a good dictionary would give (保持均衡饮食, not 采用均衡饮食).
- patterns: reusable sentence frames. form uses X / Y for the replaceable parts ("Far from being X, ..."); example
  wraps only the fixed frame words in brackets ("[Far from being] a waste of time, games can ..."); plus zh,
  example_zh, note (Chinese usage tip, max 24 characters).
- linkers: discourse markers more advanced than and / but / so / because / also. form, zh, example (with the linker
  in brackets), example_zh, alts (0-2).
- ideas: arguments a candidate can reuse: pros, cons, causes, effects, solutions and concrete examples, in general
  terms (no invented statistics). Each idea must carry its reasoning or mechanism, not just a slogan: "Sugar taxes
  push manufacturers to reformulate drinks, cutting sugar intake without banning anything" rather than "Sugar taxes
  are good". Each: en (one sentence, max 28 words), zh (max 45 characters).
- prompts: IELTS Task 2 style questions on the topic: en, zh.
Reply with JSON only."""


def _arr(props: dict) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": {k: ({"type": "array", "items": {"type": "string"}} if v == "list" else {"type": "string"}) for k, v in props.items()},
                                       "required": list(props), "additionalProperties": False}}


EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "collocations": _arr({"form": "s", "zh": "s", "example": "s", "example_zh": "s", "alts": "list"}),
        "patterns": _arr({"form": "s", "zh": "s", "example": "s", "example_zh": "s", "note": "s"}),
        "linkers": _arr({"form": "s", "zh": "s", "example": "s", "example_zh": "s", "alts": "list"}),
        "ideas": _arr({"en": "s", "zh": "s"}),
        "prompts": _arr({"en": "s", "zh": "s"}),
    },
    "required": ["collocations", "patterns", "linkers", "ideas", "prompts"],
    "additionalProperties": False,
}


def _norm_form(f: str) -> str:
    f = re.sub(r"\s+", " ", f.strip().lower().replace("’", "'"))
    return re.sub(r"[\s.…,]+$", "", f)


def expand(topic: dict, have: list[str], cfg: dict, counts: dict) -> dict:
    """counts: {collocation, pattern, linker, idea, prompt} → {materials: [...], prompts: [...], dropped: n}
    返回的素材和内置素材同一个结构；例句里的 [方括号] 必须真的对得上，不然丢掉。"""
    want = {k: int(counts.get(k, 0)) for k in ("collocation", "pattern", "linker", "idea", "prompt")}
    prompt = json.dumps({
        "topic": f"{topic['en']} ({topic['zh']})",
        "requested": {"collocations": want["collocation"], "patterns": want["pattern"], "linkers": want["linker"],
                      "ideas": want["idea"], "prompts": want["prompt"]},
        "already_have": have[-400:],
    }, ensure_ascii=False)
    d = ask(cfg, prompt, EXPAND_SYSTEM, EXPAND_SCHEMA, temperature=0.7, max_tokens=8000)
    seen = {_norm_form(h) for h in have}
    out, dropped = [], 0

    def take(kind: str, rows):
        nonlocal dropped
        for r in (rows or [])[: want[kind] + 3]:
            if not isinstance(r, dict):
                dropped += 1
                continue
            if kind == "idea":
                en, zh = str(r.get("en", "")).strip(), str(r.get("zh", "")).strip()
                if not en or not zh or len(en.split()) > 34 or _norm_form(en) in seen:
                    dropped += 1
                    continue
                seen.add(_norm_form(en))
                out.append({"kind": "idea", "form": en, "zh": zh[:60], "topics": [topic["slug"]],
                            "examples": [{"en": en, "zh": zh, "answer": []}], "alts": [], "note": ""})
                continue
            form = _form(kind, str(r.get("form", "")))
            zh = str(r.get("zh", "")).strip()
            raw = str(r.get("example", "")).strip()
            pieces = [p.strip() for p in re.findall(r"\[([^\]]+)\]", raw) if p.strip()]
            en = re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]", r"\1", raw)).strip()
            ok = form and zh and pieces and make_cloze(en, pieces) and _norm_form(form) not in seen
            if ok and kind == "collocation":
                words = len(form.split())
                ok = 2 <= words <= 7 and len(en.split()) >= 6
                # 搭配的第一个词（跳过冠词和 be）必须在挖空里，防止 "puts [pressure on]" 这种漏掉动词的
                head = next((w for w in re.findall(r"[a-z']+", form.lower()) if w not in {"a", "an", "the", "be"} | SLOT_WORDS), "")
                if ok and head and not any(_word_match(t.strip(".,;:!?"), head) for p in pieces for t in p.split()):
                    ok = False
                segs = make_cloze(en, pieces)
                trimmed = trim_to_form(en, segs, form) if ok else None
                if trimmed:
                    pieces = [x["blank"] for x in trimmed if "blank" in x]
            if not ok:
                dropped += 1
                continue
            seen.add(_norm_form(form))
            m = {"kind": kind, "form": form, "zh": zh[:40], "topics": [topic["slug"]],
                 "examples": [{"en": en, "zh": re.sub(r"[\[\]【】]", "", str(r.get("example_zh", ""))).strip(), "answer": pieces}],
                 "alts": [str(a).strip() for a in (r.get("alts") or []) if str(a).strip()][:3],
                 "note": str(r.get("note", "")).strip()[:40] if kind == "pattern" else ""}
            out.append(m)

    take("collocation", d.get("collocations"))
    take("pattern", d.get("patterns"))
    take("linker", d.get("linkers"))
    take("idea", d.get("ideas"))
    prompts = [{"en": str(p.get("en", "")).strip(), "zh": str(p.get("zh", "")).strip()}
               for p in (d.get("prompts") or [])[: want["prompt"]] if isinstance(p, dict) and str(p.get("en", "")).strip()]
    return {"materials": out, "prompts": prompts, "dropped": dropped}



# ---------- 审校：复核中文和近义说法，删掉低价值条目 ----------

REVIEW_SYSTEM = """You are a meticulous editor checking a writing-material bank for Chinese learners (target IELTS 7).
For each numbered item decide:
- keep: false if the item is wrong, unnatural, too basic for a band-7 essay (lead to, give up, very important), a
  near-duplicate of another item in the list, or its example sentence is unnatural. Otherwise true.
- zh: the correct, natural Simplified Chinese meaning (fix it if needed; keep it short).
- alts: only alternatives that really mean the same and could replace the item in its example sentence. Remove any
  related-but-different term (food safety is NOT an alternative for food security). Empty list is fine.
- example_ok: false if the example sentence has a grammar error or misuses the item.
Reply with JSON only: {"items": [{"i": 1, "keep": true, "zh": "...", "alts": ["..."], "example_ok": true}]}"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "keep": {"type": "boolean"}, "zh": {"type": "string"},
                       "alts": {"type": "array", "items": {"type": "string"}}, "example_ok": {"type": "boolean"}},
        "required": ["i", "keep", "zh", "alts", "example_ok"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def review(materials: list[dict], cfg: dict) -> tuple[list[dict], int]:
    """逐批复核。返回（保留的素材，删掉的条数）。某一批 AI 出错时原样保留那一批。"""
    kept, removed = [], 0
    for start in range(0, len(materials), 40):
        batch = materials[start : start + 40]
        lines = [json.dumps({"i": i + 1, "type": m["kind"], "item": m["form"], "zh": m["zh"], "alts": m.get("alts", []),
                             "example": (m["examples"][0]["en"] if m["kind"] != "idea" else "")}, ensure_ascii=False)
                 for i, m in enumerate(batch)]
        try:
            d = ask(cfg, "\n".join(lines), REVIEW_SYSTEM, REVIEW_SCHEMA, temperature=0.1, max_tokens=6000)
        except AIError:
            kept += batch
            continue
        verdict = {}
        for v in d.get("items") or []:
            try:
                verdict[int(v.get("i")) - 1] = v
            except (TypeError, ValueError):
                continue
        for i, m in enumerate(batch):
            v = verdict.get(i)
            if v is None:
                kept.append(m)
                continue
            if not v.get("keep", True) or v.get("example_ok") is False:
                removed += 1
                continue
            zh = str(v.get("zh", "")).strip()
            if zh:
                m["zh"] = zh[:60]
            if m["kind"] != "idea" and m["kind"] != "pattern":
                m["alts"] = [str(a).strip() for a in (v.get("alts") or []) if str(a).strip()][:3]
            kept.append(m)
    return kept, removed
