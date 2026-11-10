"""AI：从文章里拆写作素材、出改写题并批改、批改话题演练。

支持三种来源（和川听同一套）：
  deepseek / gemini / gpt / openai：OpenAI 格式的 /chat/completions 接口（DeepSeek 国内直连；Gemini 走 Google 的兼容接口；
                               gpt 是 OpenAI 官方，按新模型的参数规矩发；openai 是其他兼容接口，也能接 Kimi、通义等）
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
                            temperature: float = 0.4, max_tokens: int = 6000, extra: dict | None = None,
                            reasoning_model: bool = False) -> str:
    """reasoning_model：OpenAI 的 GPT-5 这类思考型模型，长度用 max_completion_tokens，不接受自定义 temperature"""
    url = base.rstrip("/") + "/chat/completions"  # 接口地址填到 /v1 为止（DeepSeek 可以不带 /v1）
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        **({"max_completion_tokens": max_tokens} if reasoning_model else {"temperature": temperature, "max_tokens": max_tokens}),
        **(extra or {}),
    }
    # 有的接口不认 response_format 或额外参数：报 400 时去掉它们再试一次（JSON 靠提示词和解析容错）
    for attempt in range(2):
        try:
            r = httpx.post(url, timeout=180, headers={"Authorization": f"Bearer {key}"}, json=body)
        except httpx.HTTPError as e:
            raise AIError(f"连不上 {base}：{e.__class__.__name__}（Gemini、Claude 国内需要开代理）") from e
        if r.status_code == 400 and attempt == 0 and ("response_format" in body or extra):
            body = {k: v for k, v in body.items() if k != "response_format" and k not in (extra or {})}
            continue
        break
    if r.status_code in (401, 403):
        raise AIError("API Key 不对、已失效，或者没有这个模型的权限")
    if r.status_code == 402:
        raise AIError("账户余额不足")
    if r.status_code == 404:
        raise AIError(f"找不到模型 {model}，请在设置里检查模型名")
    if r.status_code == 429:
        raise AIError("请求太频繁或额度用完了，稍后再试（Gemini 免费额度每分钟有次数限制）")
    if r.status_code >= 400:
        raise AIError(f"AI 接口报错 {r.status_code}：{r.text[:200]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise AIError("AI 接口返回的格式不对") from e
    if not content:
        raise AIError("AI 没有返回内容（可能被安全过滤拦下了），换一下再试")
    return content


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


PROVIDER_NAMES = {"deepseek": "DeepSeek", "gemini": "Gemini", "gpt": "OpenAI（GPT）", "openai": "接口", "anthropic": "Claude"}
OPENAI_STYLE = ("deepseek", "gemini", "gpt", "openai")  # 都走 /chat/completions


def unavailable_reason(cfg: dict) -> str | None:
    ai = cfg["ai"]
    provider = ai["provider"]
    if provider == "none":
        return "设置里选的是“不用 AI”"
    if provider == "anthropic":
        return None if ai["anthropic"]["key"] else "还没有填 Claude 的 API Key"
    if provider in OPENAI_STYLE:
        p = ai[provider]
        if not p["key"]:
            return f"还没有填 {PROVIDER_NAMES[provider]} 的 API Key"
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
    # Gemini 和 GPT 的思考型模型会把思考也算进输出长度，调低思考强度、多给点长度，免得 JSON 写到一半被截断
    extra = {"reasoning_effort": "low"} if ai["provider"] in ("gemini", "gpt") else None
    return _call_openai_compatible(p["base"], p["key"], p["model"], prompt, system, temperature,
                                   max_tokens + (4000 if extra else 0), extra, reasoning_model=ai["provider"] == "gpt")


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


EXAMS = {
    "ielts": "IELTS Academic Writing, target band {band}",
    "toefl": "TOEFL iBT Writing, target score {band}",
    "kaoyan": "China's postgraduate entrance exam English (考研英语), target score {band}",
    "cet6": "CET-6 (College English Test Band 6), target score {band}",
    "cet4": "CET-4 (College English Test Band 4), target score {band}",
}
DEFAULT_BAND = {"ielts": "7", "toefl": "100", "kaoyan": "80", "cet6": "550", "cet4": "500"}
STRICTNESS = {
    "lenient": "lenient: encourage the learner, flag only clear errors, and score generously",
    "standard": "standard: mark like a fair, experienced examiner",
    "strict": "strict: flag every unnatural or imprecise choice and score conservatively",
}


def profile(cfg: dict) -> str:
    """学习者设定，附在每个系统提示词后面"""
    ex = cfg.get("exam", {})
    target = ex.get("target", "ielts")
    band = str(ex.get("band") or DEFAULT_BAND.get(target, "7"))
    lang = "English" if cfg.get("ai", {}).get("feedback_lang") == "en" else "Simplified Chinese"
    strict = STRICTNESS.get(cfg.get("ai", {}).get("strictness", "standard"), STRICTNESS["standard"])
    return ("\n\nLearner profile (adjust difficulty, word choice and marking to it):\n"
            f"- Exam: {EXAMS.get(target, EXAMS['ielts']).format(band=band)}.\n"
            f"- Marking: {strict}.\n"
            f"- Write every feedback / comment / explanation field (the fields whose names end in _zh) in {lang}; "
            "translations of English sentences stay in Simplified Chinese.")


def ask(cfg: dict, prompt: str, system: str, schema: dict, **kw) -> dict:
    """测试里替换这个函数来模拟 AI"""
    return _parse(_call(cfg, prompt, system + profile(cfg), schema, **kw))


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
- items: reusable material in this sentence; include ALL useful common noun phrases as well as other expressions,
  usually 2-6 in a content-rich sentence. Do not force a fixed quota or invent phrases absent from the text.
  * collocation: BOTH everyday topic vocabulary / noun phrases (vocational education, public transport,
    food prices, social mobility, renewable energy, work experience) AND verb+noun, adjective+noun,
    adverb+adjective, verb+preposition, noun+of+noun combinations.
    Short, common noun phrases are core writing vocabulary: keep them even if they are not advanced or idiomatic.
    Extract a useful noun phrase separately even when it sits inside a longer verb phrase; retain its full meaning.
    Skip empty combinations (very good), proper names and highly specialised jargon, not useful topic concepts.
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

REWRITE_SYSTEM = """You set rewriting exercises for a Chinese learner of English writing.
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

PROMPT_SYSTEM = """Write {n} essay questions on the given topic, in the style of the learner's exam (IELTS Task 2 style if unsure). Each question is one or two
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


DRILL_SYSTEM = """You are an experienced writing examiner and coach for a Chinese learner.
The learner wrote one paragraph (or a short essay) answering the question below, and tried to use some suggested
expressions from their own library. Give honest, specific feedback.
Return:
- band: an estimated IELTS writing band for this text, used as a common yardstick whatever the learner's exam (number, steps of 0.5, from 3 to 9). Judge Task Response too: if the
  text does not actually answer the question (off-topic, or only partly addresses it), the band must reflect that.
- summary_zh: 2-3 sentences of overall feedback in Simplified Chinese (max 140 characters). If the text drifts away
  from the question, say so first.
- used: for every suggested expression that the learner actually used: id, quote (the exact words copied from the
  learner's text), ok (true if used correctly and naturally), comment_zh (max 40 characters).
- issues: up to 6 of the most important language problems: quote (exact words copied from the learner's text),
  fix (the corrected words), why_zh (max 40 characters).
- improved: an improved version of the learner's text, a little above the learner's target level. Keep their ideas and structure, and keep the
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
postgraduate-exam English writing. Everything must be accurate, natural, current English that a
strong essay would actually use. No rare, archaic, or overly literary expressions; no clichés like "every coin has
two sides". Never repeat anything from the "already have" list, even with small changes.

Return JSON with these arrays (give exactly the requested number of each; an array may be requested as 0):
- collocations: a balanced mix of common noun phrases / topic vocabulary AND reusable verb and adjective combinations.
  At least HALF the requested collocations should be precise noun phrases or concept labels, unless the supplied
  topic genuinely has too few remaining distinct concepts. Examples across topics: vocational education, higher
  education, public transport, food prices, social mobility, job security, renewable energy, cultural heritage.
  These short, common expressions are valuable in their own right; do not inflate them into longer verb phrases
  or reject them for being below C1. Cover different subtopics, not just the examples listed here.
  The remaining items can include verb+noun, adjective+noun, adverb+adjective and verb+preposition combinations.
  No empty phrases or near-duplicates. Each: form (dictionary form; base verb; sb / sth / one's for open slots; 2-6
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
- prompts: essay questions on the topic in the style of the learner's exam: en, zh.
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

REVIEW_SYSTEM = """You are a meticulous editor checking a writing-material bank for Chinese learners.
For each numbered item decide:
- keep: false if the item is wrong, unnatural, too basic for the learner's target level (lead to, give up, very important), a
  near-duplicate of another item in the list, or its example sentence is unnatural. Otherwise true.
  Common noun phrases / concept labels such as vocational education, food prices and public transport MUST be kept
  when accurate and useful: being short, familiar or below C1 is not a reason to remove topic vocabulary.
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



# ---------- 话题翻译 ----------

TRANS_GEN_SYSTEM = """You write Chinese-to-English translation exercises for a Chinese learner of English writing.
Each exercise is a Chinese text on the given topic that the learner will translate into English, the kind of thing a
good essay on that topic would say. The Chinese must read like natural, well-written Chinese (not translationese),
and should be rich in expressions that are worth learning to render idiomatically in English: elegant word
partnerships, four-character expressions and idioms (治标不治本, 一刀切, 得不偿失, 不可同日而语), and set phrases.

Lengths:
- short: one sentence, 15-35 Chinese characters, built around one expression that has a precise, elegant English
  equivalent (e.g. 这项政策治标不治本 → the policy only treats the symptoms; 加剧贫富分化 → widen the wealth gap).
- medium: one or two sentences, 35-70 characters, with at least one four-character expression, idiom or set phrase.
- long: a long, layered sentence or two linked sentences, 70-130 characters, with subordinate clauses, concession,
  contrast or cause-and-effect, and at least two expressions that need idiomatic rendering, so that it tests sentence
  structure as well as vocabulary.
Avoid flat textbook Chinese such as 技术进步不仅提高了生产效率，也降低了成本; prefer the vivid, compact style of a
good Chinese op-ed (e.g. 一味追求经济增长而忽视环境代价，无异于饮鸩止渴；短期看似红利可观，长远却得不偿失。).

You are given some expressions from the learner's own library (with ids). Where it fits naturally, build an exercise so
that one of them is the best way to translate part of it, and list its id in targets. Never force it, except when
the request says every_exercise_must_use_a_library_expression: then build EVERY exercise around a different library
expression (the learner is practising their own collection), choosing a fitting topic for each.

For each exercise return:
- length: short | medium | long
- zh: the Chinese text
- ref_en: an idiomatic English translation at the learner's target level
- targets: ids of library expressions that ref_en uses (may be empty)
- hints: 1-3 of the trickiest chunks, each {zh: the Chinese chunk, en: [1-3 good English renderings]}
Reply with JSON only, exactly in this shape:
{"items": [{"length": "short", "zh": "...", "ref_en": "...", "targets": [12], "hints": [{"zh": "...", "en": ["...", "..."]}]}]}"""

TRANS_GEN_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "length": {"type": "string", "enum": ["short", "medium", "long"]},
            "zh": {"type": "string"}, "ref_en": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "integer"}},
            "hints": {"type": "array", "items": {
                "type": "object",
                "properties": {"zh": {"type": "string"}, "en": {"type": "array", "items": {"type": "string"}}},
                "required": ["zh", "en"], "additionalProperties": False}},
        },
        "required": ["length", "zh", "ref_en", "targets", "hints"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def _content_words(form: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+(?:['-][a-z]+)*", form.lower().replace("’", "'"))
            if w not in SLOT_WORDS and w not in {"a", "an", "the", "of", "to", "in", "on", "and", "be"}]


def uses_form(form: str, text: str) -> bool:
    """粗略判断 text 里是否用了这个素材（允许变形）"""
    words = _content_words(form)
    toks = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", text)
    return bool(words) and all(any(_word_match(t, w) for t in toks) for w in words)


def translation_items(topic: dict, candidates: list[dict], counts: dict, cfg: dict, require_targets: bool = False) -> list[dict]:
    """counts: {short, medium, long} → [{length, zh, ref, targets: [id], hints}]
    require_targets：练的是学习者自建的语料库，每道题都要围绕其中一条素材来出"""
    payload = {"topic": f"{topic['en']} ({topic['zh']})",
               "every_exercise_must_use_a_library_expression": require_targets,
               "requested": {k: int(counts.get(k, 0)) for k in ("short", "medium", "long")},
               "library_expressions": [{"id": m["id"], "type": m["kind"], "form": m["form"], "zh": m["zh"]} for m in candidates]}
    d = ask(cfg, json.dumps(payload, ensure_ascii=False), TRANS_GEN_SYSTEM, TRANS_GEN_SCHEMA, temperature=0.8, max_tokens=6000)
    by_id = {m["id"]: m for m in candidates}
    rows = d.get("items")
    if not isinstance(rows, list):  # 有的模型不按字段名来（比如叫 exercises），取第一个列表
        rows = next((v for v in d.values() if isinstance(v, list)), [])
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        zh, ref = str(it.get("zh", "")).strip(), str(it.get("ref_en", "")).strip()
        length = str(it.get("length", "")).strip()
        if not re.search(r"[\u4e00-\u9fff]", zh) or not ref or length not in ("short", "medium", "long"):
            continue
        targets = []
        for t in it.get("targets") or []:
            try:
                t = int(t)
            except (TypeError, ValueError):
                continue
            if t in by_id and t not in targets and uses_form(by_id[t]["form"], ref):
                targets.append(t)
        hints = []
        for h in it.get("hints") or []:
            if isinstance(h, dict) and str(h.get("zh", "")).strip():
                raw_en = h.get("en") or []
                if isinstance(raw_en, str):
                    raw_en = [x for x in re.split(r"\s*[/;；]\s*", raw_en)]
                en = [str(x).strip() for x in raw_en if str(x).strip()][:3]
                if en:
                    hints.append({"zh": str(h["zh"]).strip(), "en": en})
        out.append({"length": length, "zh": zh, "ref": ref, "targets": targets, "hints": hints[:3]})
    return out


TRANS_CHECK_SYSTEM = """You mark a Chinese-to-English translation by a Chinese learner of English writing.
Judge both faithfulness (is every idea in the Chinese there, nothing added or distorted?) and naturalness (would a
skilled native writer put it this way?). Be specific and generous with alternatives: the learner wants to see other
good ways to say things and the more idiomatic choices.

Return:
- score: overall 1-5 (5 = accurate and idiomatic; 4 = accurate with minor awkwardness; 3 = meaning mostly there but
  clearly unnatural or with errors; 2 = important meaning lost or serious errors; 1 = largely wrong or missing).
- accuracy: 1-5. naturalness: 1-5.
- summary_zh: 1-3 sentences of overall feedback (max 120 characters).
- corrected: the learner's translation with the minimum changes needed to be correct and faithful (keep their wording
  wherever it is acceptable).
- better: 1-2 complete, idiomatic translations at the learner's target level, written the way a skilled native writer
  would, possibly quite different in structure from the learner's.
- chunks: up to 6 parts of the learner's answer worth improving or worth knowing alternatives for: yours (the exact words
  copied from the learner's answer), options (2-4 better or alternative English renderings), why_zh (max 50
  characters: why the options are better or how they differ).
- good: up to 3 things the learner did well: quote (exact words copied from their answer), why_zh (max 30 characters).
- expressions: 2-5 idiomatic expressions from your better versions that are worth saving to a writing-material library:
  type (collocation | pattern | linker), form (dictionary form; sb / sth / one's for slots; X / Y in patterns), zh
  (max 12 characters), example (the sentence from your better version that contains it).
- targets_used: for every target expression listed in the request: id, used (did the learner use it or an equally good
  equivalent?), ok (used correctly?).
Reply with JSON only."""

TRANS_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"}, "accuracy": {"type": "integer"}, "naturalness": {"type": "integer"},
        "summary_zh": {"type": "string"}, "corrected": {"type": "string"},
        "better": {"type": "array", "items": {"type": "string"}},
        "chunks": {"type": "array", "items": {
            "type": "object",
            "properties": {"yours": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
                           "why_zh": {"type": "string"}},
            "required": ["yours", "options", "why_zh"], "additionalProperties": False}},
        "good": {"type": "array", "items": {
            "type": "object", "properties": {"quote": {"type": "string"}, "why_zh": {"type": "string"}},
            "required": ["quote", "why_zh"], "additionalProperties": False}},
        "expressions": {"type": "array", "items": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": ["collocation", "pattern", "linker"]}, "form": {"type": "string"},
                           "zh": {"type": "string"}, "example": {"type": "string"}},
            "required": ["type", "form", "zh", "example"], "additionalProperties": False}},
        "targets_used": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "integer"}, "used": {"type": "boolean"}, "ok": {"type": "boolean"}},
            "required": ["id", "used", "ok"], "additionalProperties": False}},
    },
    "required": ["score", "accuracy", "naturalness", "summary_zh", "corrected", "better", "chunks", "good",
                 "expressions", "targets_used"],
    "additionalProperties": False,
}


def _clamp(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def check_translation(item: dict, answer: str, targets: list[dict], cfg: dict) -> dict:
    payload = {"chinese": item["zh"], "reference_translation": item.get("ref", ""),
               "target_expressions": [{"id": m["id"], "form": m["form"], "zh": m["zh"]} for m in targets],
               "learner_translation": answer}
    d = ask(cfg, json.dumps(payload, ensure_ascii=False), TRANS_CHECK_SYSTEM, TRANS_CHECK_SCHEMA, temperature=0.2, max_tokens=4000)
    chunks = []
    for c in d.get("chunks") or []:
        yours = str(c.get("yours", "")).strip()
        opts = [str(o).strip() for o in (c.get("options") or []) if str(o).strip()][:4]
        if _in_text(yours, answer) and opts:
            chunks.append({"yours": yours, "options": opts, "why": str(c.get("why_zh", "")).strip()})
    good = [{"quote": str(g.get("quote", "")).strip(), "why": str(g.get("why_zh", "")).strip()}
            for g in (d.get("good") or []) if _in_text(str(g.get("quote", "")), answer)]
    better = [str(b).strip() for b in (d.get("better") or []) if str(b).strip()][:2]
    exprs = []
    for e in d.get("expressions") or []:
        kind = str(e.get("type", ""))
        form = _form(kind, str(e.get("form", "")))
        example = str(e.get("example", "")).strip()
        if kind not in ("collocation", "pattern", "linker") or not form:
            continue
        pieces = [form] if make_cloze(example, [form]) else []
        exprs.append({"kind": kind, "form": form, "zh": str(e.get("zh", "")).strip()[:40], "topics": [],
                      "examples": [{"en": example, "zh": "", "answer": pieces}] if example else [], "alts": [], "note": ""})
    ids = {m["id"] for m in targets}
    used = []
    for t in d.get("targets_used") or []:
        try:
            tid = int(t.get("id"))
        except (TypeError, ValueError):
            continue
        if tid in ids:
            used.append({"id": tid, "used": bool(t.get("used")), "ok": bool(t.get("ok"))})
    return {"score": _clamp(d.get("score"), 1, 5), "accuracy": _clamp(d.get("accuracy"), 1, 5),
            "naturalness": _clamp(d.get("naturalness"), 1, 5), "summary": str(d.get("summary_zh", "")).strip(),
            "corrected": str(d.get("corrected", "")).strip() or answer, "better": better, "chunks": chunks[:6],
            "good": good[:3], "expressions": exprs[:5], "targetsUsed": used}



# ---------- 添加素材：AI 识别粘贴的文字 ----------

RECOGNIZE_SYSTEM = """A Chinese learner of English writing pasted something they want to add to their writing-material
library. It may be a single expression (a collocation, a sentence pattern, a linker), a sentence, a paragraph, or even
a Chinese description of an expression they want.

Return the reusable writing material in it:
- If it is a single English expression, return exactly that one item (fix its form if needed).
- If it is a sentence or passage, return the expressions worth reusing in essays, including common noun phrases
  and concept labels (vocational education, public transport, job security, food prices). Give broad coverage
  across the whole passage; do not impose a small fixed item cap or prefer difficult phrases only. Also return at most one idea
  (an argument stated in general terms) if the text contains a good one.
- If it is Chinese, return the best English expression(s) for what they describe.

For each item:
- type: collocation | pattern | linker | idea
- collocation includes useful noun phrases and everyday topic vocabulary as well as verb combinations.
- form: dictionary form (base verb; sb / sth / one's for open slots; X / Y in patterns). For an idea: one general English
  sentence, max 25 words.
- zh: short Simplified Chinese meaning (max 16 characters; idea max 40)
- topics: 0-2 slugs from: {topics}
- register: formal | neutral | informal
- alts: 0-3 English alternatives with the same meaning (empty for ideas)
- note: optional Chinese usage tip (max 24 characters)
- example: one natural essay-style sentence (10-24 words) using the item. If the learner's text contains a good sentence
  with the item, use that sentence. Wrap ONLY the item's own words in [square brackets]; for patterns only the fixed frame
  words; for ideas no brackets.
- example_zh: Simplified Chinese translation of the example.
Reply with JSON only, shaped like {"items": [{"type": "...", "form": "...", ...}]}."""

RECOGNIZE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["collocation", "pattern", "linker", "idea"]},
            "form": {"type": "string"}, "zh": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
            "register": {"type": "string", "enum": ["formal", "neutral", "informal"]},
            "alts": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"},
            "example": {"type": "string"}, "example_zh": {"type": "string"},
        },
        "required": ["type", "form", "zh", "topics", "register", "alts", "note", "example", "example_zh"],
        "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def recognize(text: str, topics: list[dict], cfg: dict, default_topic: str = "") -> list[dict]:
    """返回和内置素材同一结构的素材列表；例句里的方括号对不上时，就当作没有挖空（回忆时改成看中文想英文）"""
    slugs = {t["slug"] for t in topics}
    d = ask(cfg, text[:4000], RECOGNIZE_SYSTEM.replace("{topics}", _topic_lines(topics)), RECOGNIZE_SCHEMA,
            temperature=0.3, max_tokens=4000)
    rows = d.get("items")
    if not isinstance(rows, list):
        rows = next((v for v in d.values() if isinstance(v, list)), [])
    out, seen = [], set()
    for u in rows:
        if not isinstance(u, dict):
            continue
        it = _clean_item(u, slugs)
        if it["kind"] not in KIND_SET or not it["form"] or _norm_form(it["form"]) in seen:
            continue
        seen.add(_norm_form(it["form"]))
        if default_topic in slugs and default_topic not in it["topics"]:
            it["topics"] = [default_topic] + it["topics"][:1]
        raw = str(u.get("example", "")).strip()
        pieces = [p.strip() for p in re.findall(r"\[([^\]]+)\]", raw) if p.strip()]
        en = re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]", r"\1", raw)).strip()
        if it["kind"] == "idea":
            en, pieces = en or it["form"], []
        segs = make_cloze(en, pieces) if en and pieces else None
        if segs and it["kind"] == "collocation":
            segs = trim_to_form(en, segs, it["form"]) or segs
        answer = [x["blank"] for x in segs if "blank" in x] if segs else []
        zh_ex = re.sub(r"[\[\]【】]", "", str(u.get("example_zh", ""))).strip()
        it["examples"] = [{"en": en, "zh": zh_ex, "answer": answer}] if en else []
        out.append(it)
    return out
