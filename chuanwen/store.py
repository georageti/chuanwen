"""用户数据：文档、素材、复习状态、改写题缓存、话题演练记录、设置。

默认位置：macOS ~/Library/Application Support/川文/，Windows %APPDATA%\\川文\\，其他系统 ~/.local/share/川文/。
设置环境变量 CHUANWEN_HOME 可以换位置（测试和预览用）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import builtin, srs


def _default_home() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "川文"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/川文"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "川文"


HOME = Path(os.environ.get("CHUANWEN_HOME") or _default_home())
DB_PATH = HOME / "chuanwen.db"
CONFIG_PATH = HOME / "config.json"
AUDIO_DIR = HOME / "audio"  # 朗读缓存

KINDS = {
    "collocation": "搭配",
    "pattern": "句型",
    "idea": "论点素材",
    "linker": "衔接",
}
CLOZE_KINDS = {"collocation", "pattern", "linker"}  # 这几类按挖空拼写回忆；论点素材是看中文想英文，自评

DEFAULT_CONFIG = {
    "ai": {
        "provider": "deepseek",  # deepseek | gemini | anthropic | openai | none（不用 AI）
        "deepseek": {"key": "", "model": "deepseek-chat", "base": "https://api.deepseek.com"},
        "anthropic": {"key": "", "model": "claude-opus-5-5"},
        "gemini": {"key": "", "model": "gemini-flash-latest", "base": "https://generativelanguage.googleapis.com/v1beta/openai"},
        "openai": {"key": "", "model": "", "base": ""},
        "feedback_lang": "zh",     # 点评语言：zh 中文 / en 英文
        "strictness": "standard",  # 批改严格程度：lenient / standard / strict
    },
    "exam": {"target": "ielts", "band": ""},  # 目标考试：ielts / toefl / kaoyan / cet6 / cet4；band 留空用默认分数
    "study": {
        "daily_new": 10, "session_size": 30,
        "new_order": "mine_first",  # 新素材顺序：mine_first 自己收集的先 / rotate 按话题轮流 / random 随机
        "kinds": ["collocation", "pattern", "linker", "idea"],  # 回忆练哪几类
        "max_tries": 3,             # 回忆：错几次显示答案
        "auto_speak": True,         # 回忆：答完自动朗读
    },
    "drill": {"suggestions": 12, "min_words": 20},
    "translate": {"length": "mix", "batch": 6, "show_hints": True},  # length：mix / short / medium / long
    "voice": {"name": "en-US-AriaNeural", "rate": -5},  # 朗读：微软语音名（browser = 用浏览器自带语音）；语速 -50..+50（%）
    "ui": {"font": "normal", "motion": True, "serif": True, "input": "sans"},  # 字号；动画；例句用衬线体；输入字体 sans / serif / mono
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY, title TEXT, filename TEXT, chars INT, created REAL, text TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS materials(
  id INTEGER PRIMARY KEY, norm TEXT UNIQUE, kind TEXT, form TEXT, zh TEXT, examples TEXT, topics TEXT,
  register TEXT, alts TEXT, note TEXT, source TEXT, doc_id INT, starred INT DEFAULT 0, user_note TEXT DEFAULT '',
  added REAL, state TEXT);
CREATE TABLE IF NOT EXISTS removed(norm TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS topics(
  slug TEXT PRIMARY KEY, en TEXT, zh TEXT, glyph TEXT, prompts TEXT, builtin INT DEFAULT 0, ord INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS log(day TEXT, mid INT, mode TEXT, grade INT, at REAL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, mid INT, mode TEXT, data TEXT, used INT DEFAULT 0, created REAL);
CREATE INDEX IF NOT EXISTS tasks_mid ON tasks(mid, mode);
CREATE TABLE IF NOT EXISTS drills(
  id INTEGER PRIMARY KEY, topic TEXT, prompt TEXT, text TEXT, feedback TEXT, created REAL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS trans(
  id INTEGER PRIMARY KEY, topic TEXT, length TEXT, zh TEXT, ref TEXT, hints TEXT, targets TEXT, source TEXT,
  used INT DEFAULT 0, created REAL);
CREATE INDEX IF NOT EXISTS trans_topic ON trans(topic, used);
CREATE TABLE IF NOT EXISTS trans_log(
  id INTEGER PRIMARY KEY, item INT, topic TEXT, zh TEXT, answer TEXT, feedback TEXT, created REAL);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        HOME.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _seed(_conn)
    return _conn


def tx(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


def kv_get(k: str, default=None):
    rows = q("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(rows[0]["v"]) if rows else default


def kv_set(k: str, v) -> None:
    tx("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))


# ---------- 内置素材 ----------

def _seed(conn: sqlite3.Connection) -> None:
    """第一次运行、或内置素材有新版本时，把内置话题和素材放进库里。用户删掉的内置素材不会再加回来。"""
    row = conn.execute("SELECT v FROM kv WHERE k='builtin_version'").fetchone()
    have = json.loads(row[0]) if row else 0
    if have >= builtin.VERSION:
        return
    data = builtin.load()
    _renorm(conn)
    removed = {r[0] for r in conn.execute("SELECT norm FROM removed")}
    for i, t in enumerate(data["topics"]):
        conn.execute("INSERT OR REPLACE INTO topics(slug, en, zh, glyph, prompts, builtin, ord) VALUES(?,?,?,?,?,1,?)",
                     (t["slug"], t["en"], t["zh"], t["glyph"], json.dumps(t["prompts"], ensure_ascii=False), i))
    now = time.time()
    order = {t["slug"]: i for i, t in enumerate(data["topics"])}
    seen: dict[str, int] = {}
    for m in data["materials"]:
        # 内置素材按话题轮流排：练新素材时每轮能碰到不同话题、不同类型
        slug = m["topics"][0]
        seen[slug] = seen.get(slug, 0) + 1
        i = seen[slug] * 100 + order[slug]
        n = norm(m["kind"], m["form"])
        if n in removed:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO materials(norm, kind, form, zh, examples, topics, register, alts, note, source, added) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (n, m["kind"], m["form"], m["zh"], json.dumps(m["examples"], ensure_ascii=False),
             json.dumps(m["topics"]), m.get("register", ""), json.dumps(m.get("alts", []), ensure_ascii=False),
             m.get("note", ""), "builtin", now - 1_000_000 + i))  # 内置的排在用户自己加的后面
    conn.execute("INSERT OR REPLACE INTO kv VALUES('builtin_version', ?)", (json.dumps(builtin.VERSION),))
    conn.commit()


def _renorm(conn: sqlite3.Connection) -> None:
    """去重规则改过（末尾的省略号不算），老数据库的去重标识按新规则重算，补内置素材时才不会重复"""
    for mid, kind, form, old in conn.execute("SELECT id, kind, form, norm FROM materials").fetchall():
        n = norm(kind, form)
        if n != old:
            try:
                conn.execute("UPDATE materials SET norm=? WHERE id=?", (n, mid))
            except sqlite3.IntegrityError:
                pass  # 已经有一条新标识的，保留两条，不删用户数据
    for (old,) in conn.execute("SELECT norm FROM removed").fetchall():
        kind, _, form = old.partition(":")
        n = norm(kind, form)
        if n != old:
            conn.execute("INSERT OR IGNORE INTO removed VALUES(?)", (n,))


# ---------- 设置 ----------

def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    cfg = DEFAULT_CONFIG
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(DEFAULT_CONFIG, json.loads(CONFIG_PATH.read_text("utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    cfg = json.loads(json.dumps(cfg))
    cfg["ai"]["deepseek"]["key"] = cfg["ai"]["deepseek"]["key"] or os.environ.get("DEEPSEEK_API_KEY", "")
    cfg["ai"]["gemini"]["key"] = cfg["ai"]["gemini"]["key"] or os.environ.get("GEMINI_API_KEY", "")
    cfg["ai"]["anthropic"]["key"] = cfg["ai"]["anthropic"]["key"] or os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def save_config(patch: dict) -> dict:
    current = {}
    if CONFIG_PATH.exists():
        try:
            current = json.loads(CONFIG_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            current = {}
    merged = _merge(_merge(DEFAULT_CONFIG, current), patch)
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(CONFIG_PATH, 0o600)  # 里面有 API Key
    return load_config()


def public_config() -> dict:
    cfg = load_config()
    for p in ("deepseek", "gemini", "anthropic", "openai"):
        k = cfg["ai"][p]["key"]
        cfg["ai"][p]["key"] = ("••••" + k[-4:]) if k else ""
        cfg["ai"][p]["hasKey"] = bool(k)
    return cfg


# ---------- 话题 ----------

def list_topics() -> list[dict]:
    rows = q("SELECT * FROM topics ORDER BY builtin DESC, ord, slug")
    counts = {}
    for r in q("SELECT topics FROM materials"):
        for t in json.loads(r["topics"] or "[]"):
            counts[t] = counts.get(t, 0) + 1
    for r in rows:
        r["prompts"] = json.loads(r["prompts"] or "[]")
        r["count"] = counts.get(r["slug"], 0)
    return rows


def topic_slugs() -> set[str]:
    return {r["slug"] for r in q("SELECT slug FROM topics")}


def get_topic(slug: str) -> dict | None:
    return next((t for t in list_topics() if t["slug"] == slug), None)


def add_topic(en: str, zh: str) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", en.lower()).strip("-")[:30] or f"t{int(time.time())}"
    if slug in topic_slugs():
        slug = f"{slug}-{int(time.time()) % 10000}"
    glyph = (zh.strip() or en.strip() or "文")[0]
    tx("INSERT INTO topics(slug, en, zh, glyph, prompts, builtin, ord) VALUES(?,?,?,?,?,0,?)",
       (slug, en.strip(), zh.strip(), glyph, "[]", int(time.time())))
    return get_topic(slug)


def delete_topic(slug: str) -> None:
    tx("DELETE FROM topics WHERE slug=? AND builtin=0", (slug,))


def set_topic_prompts(slug: str, prompts: list[dict]) -> None:
    tx("UPDATE topics SET prompts=? WHERE slug=?", (json.dumps(prompts, ensure_ascii=False), slug))


# ---------- 素材 ----------

def norm(kind: str, form: str) -> str:
    f = re.sub(r"\s+", " ", form.strip().lower().replace("’", "'").replace("‘", "'"))
    f = re.sub(r"[\s.…,]+$", "", f)  # “There is no denying that ...” 和不带省略号的算同一条
    return f"{kind}:{f}"


def _row(r: dict) -> dict:
    for k in ("examples", "topics", "alts"):
        r[k] = json.loads(r.get(k) or "[]")
    r["state"] = json.loads(r["state"]) if r.get("state") else None
    r["kindName"] = KINDS.get(r["kind"], r["kind"])
    return r


def get_material(mid: int) -> dict | None:
    rows = q("SELECT * FROM materials WHERE id=?", (mid,))
    return _row(rows[0]) if rows else None


def list_materials(topic: str | None = None, kind: str | None = None, query: str | None = None,
                   starred: bool = False, source: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM materials WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if starred:
        sql += " AND starred=1"
    if source == "mine":
        sql += " AND source!='builtin'"
    elif source:
        sql += " AND source=?"
        args.append(source)
    # 自己收集的按新到旧排在前面，内置的按素材文件里的顺序
    rows = [_row(r) for r in q(sql + " ORDER BY source='builtin', CASE WHEN source='builtin' THEN added ELSE -added END",
                               tuple(args))]
    if topic:
        rows = [r for r in rows if topic in r["topics"]]
    if query:
        ql = query.lower().strip()
        rows = [r for r in rows if ql in r["form"].lower() or ql in (r["zh"] or "") or ql in (r["user_note"] or "")
                or any(ql in e.get("en", "").lower() for e in r["examples"])]
    return rows


def norms_in_library() -> set[str]:
    return {r["norm"] for r in q("SELECT norm FROM materials")}


def add_material(m: dict, source: str, doc_id: int | None = None) -> tuple[int, bool]:
    """加一条素材；已有同类同形的就把新例句并进去。返回 (id, 是否新加)"""
    n = norm(m["kind"], m["form"])
    rows = q("SELECT * FROM materials WHERE norm=?", (n,))
    if rows:
        old = _row(rows[0])
        exs = old["examples"]
        have = {e["en"] for e in exs}
        for e in m.get("examples", []):
            if e.get("en") and e["en"] not in have:
                exs.append(e)
        topics = list(dict.fromkeys(old["topics"] + m.get("topics", [])))
        tx("UPDATE materials SET examples=?, topics=? WHERE id=?",
           (json.dumps(exs, ensure_ascii=False), json.dumps(topics), old["id"]))
        return old["id"], False
    tx("DELETE FROM removed WHERE norm=?", (n,))
    cur = tx("INSERT INTO materials(norm, kind, form, zh, examples, topics, register, alts, note, source, doc_id, added) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (n, m["kind"], m["form"].strip(), m.get("zh", "").strip(), json.dumps(m.get("examples", []), ensure_ascii=False),
              json.dumps(m.get("topics", [])), m.get("register", ""), json.dumps(m.get("alts", []), ensure_ascii=False),
              m.get("note", ""), source, doc_id, time.time()))
    return cur.lastrowid, True


def update_material(mid: int, patch: dict) -> dict | None:
    allowed = {"zh", "user_note", "starred", "topics", "form", "alts", "register", "note"}
    for k, v in patch.items():
        if k not in allowed:
            continue
        if k in ("topics", "alts"):
            v = json.dumps(v, ensure_ascii=False)
        if k == "starred":
            v = 1 if v else 0
        tx(f"UPDATE materials SET {k}=? WHERE id=?", (v, mid))
        if k == "form":
            m = get_material(mid)
            try:
                tx("UPDATE materials SET norm=? WHERE id=?", (norm(m["kind"], m["form"]), mid))
            except sqlite3.IntegrityError:
                pass
    return get_material(mid)


def delete_material(mid: int) -> None:
    rows = q("SELECT norm, source FROM materials WHERE id=?", (mid,))
    if not rows:
        return
    if rows[0]["source"] == "builtin":
        tx("INSERT OR IGNORE INTO removed VALUES(?)", (rows[0]["norm"],))
    tx("DELETE FROM materials WHERE id=?", (mid,))
    tx("DELETE FROM tasks WHERE mid=?", (mid,))


# ---------- 文档 ----------

def add_doc(title: str, filename: str, text: str, meta: dict) -> int:
    cur = tx("INSERT INTO docs(title, filename, chars, created, text, meta) VALUES(?,?,?,?,?,?)",
             (title, filename, len(text), time.time(), text, json.dumps(meta, ensure_ascii=False)))
    return cur.lastrowid


def update_doc_meta(doc_id: int, meta: dict) -> None:
    tx("UPDATE docs SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), doc_id))


def list_docs() -> list[dict]:
    rows = q("SELECT id, title, filename, chars, created, meta FROM docs ORDER BY created DESC")
    counts = {r["doc_id"]: r["n"] for r in q("SELECT doc_id, COUNT(*) n FROM materials WHERE doc_id IS NOT NULL GROUP BY doc_id")}
    for r in rows:
        meta = json.loads(r.pop("meta") or "{}")
        r["mode"] = meta.get("mode", "plain")
        r["found"] = sum(len(s.get("items", [])) for s in meta.get("sents", []))
        r["saved"] = counts.get(r["id"], 0)
    return rows


def get_doc(doc_id: int) -> dict | None:
    rows = q("SELECT * FROM docs WHERE id=?", (doc_id,))
    if not rows:
        return None
    d = rows[0]
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def delete_doc(doc_id: int) -> None:
    tx("DELETE FROM docs WHERE id=?", (doc_id,))


# ---------- 复习 ----------

def _filtered(rows: list[dict], topic: str | None, kind: str | None) -> list[dict]:
    if topic:
        rows = [r for r in rows if topic in r["topics"]]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    return rows


def due_materials(topic: str | None = None, kind: str | None = None) -> list[dict]:
    day = srs.today()
    rows = _filtered([_row(r) for r in q("SELECT * FROM materials WHERE state IS NOT NULL")], topic, kind)
    due = [r for r in rows if r["state"]["due"] <= day]
    due.sort(key=lambda r: (r["state"]["due"], r["state"]["s"]))
    return due


def new_materials(topic: str | None = None, kind: str | None = None) -> list[dict]:
    rows = _filtered([_row(r) for r in q("SELECT * FROM materials WHERE state IS NULL")], topic, kind)
    order = load_config()["study"].get("new_order", "mine_first")
    if order == "random":
        import random
        random.shuffle(rows)
    elif order == "rotate":  # 不分来源，按话题轮流（内置素材本来就是轮流排的）
        rows.sort(key=lambda r: (-r["starred"], r["added"] if r["source"] == "builtin" else 0))
    else:  # 自己收集的在前，收藏的在前，内置的按原顺序
        rows.sort(key=lambda r: (r["source"] == "builtin", -r["starred"], r["added"] if r["source"] == "builtin" else -r["added"]))
    return rows


def learned_today() -> int:
    day = srs.today()
    return sum(1 for r in q("SELECT state FROM materials WHERE state IS NOT NULL")
               if json.loads(r["state"]).get("learned") == day)


def record(mid: int, g: int, mode: str) -> dict | None:
    m = get_material(mid)
    if not m:
        return None
    g = max(srs.AGAIN, min(srs.GOOD, int(g)))
    state = srs.learn(g) if m["state"] is None else srs.review(m["state"], g)
    tx("UPDATE materials SET state=? WHERE id=?", (json.dumps(state), mid))
    tx("INSERT INTO log VALUES(?,?,?,?,?)", (srs.today(), mid, mode, g, time.time()))
    return {"grade": g, "state": state}


def history(days: int = 14) -> list[dict]:
    today = srs.today()
    counts = {r["day"]: r["n"] for r in q("SELECT day, COUNT(*) n FROM log WHERE day >= ? GROUP BY day",
                                         (srs.add_days(today, -(days - 1)),))}
    return [{"day": d, "n": counts.get(d, 0)} for d in (srs.add_days(today, -i) for i in range(days - 1, -1, -1))]


def stats() -> dict:
    rows = [_row(r) for r in q("SELECT * FROM materials")]
    day = srs.today()
    learned = [r for r in rows if r["state"]]
    by_kind = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    return {
        "materials": len(rows),
        "mine": sum(1 for r in rows if r["source"] != "builtin"),
        "byKind": by_kind,
        "new": len(rows) - len(learned),
        "due": sum(1 for r in learned if r["state"]["due"] <= day),
        "mastered": sum(1 for r in learned if r["state"]["s"] >= srs.MASTERED_DAYS),
        "today": q("SELECT COUNT(*) n FROM log WHERE day=?", (day,))[0]["n"],
        "learnedToday": learned_today(),
        "dailyNew": load_config()["study"]["daily_new"],
        "docs": q("SELECT COUNT(*) n FROM docs")[0]["n"],
        "drills": q("SELECT COUNT(*) n FROM drills")[0]["n"],
        "translations": q("SELECT COUNT(*) n FROM trans_log")[0]["n"],
        "history": history(14),
    }


# ---------- 改写题缓存 ----------

def add_tasks(mid: int, mode: str, items: list[dict]) -> None:
    for it in items:
        tx("INSERT INTO tasks(mid, mode, data, created) VALUES(?,?,?,?)",
           (mid, mode, json.dumps(it, ensure_ascii=False), time.time()))


def next_task(mid: int, mode: str) -> dict | None:
    rows = q("SELECT * FROM tasks WHERE mid=? AND mode=? ORDER BY used, id LIMIT 1", (mid, mode))
    if not rows:
        return None
    r = rows[0]
    r["data"] = json.loads(r["data"])
    return r


def get_task(tid: int) -> dict | None:
    rows = q("SELECT * FROM tasks WHERE id=?", (tid,))
    if not rows:
        return None
    rows[0]["data"] = json.loads(rows[0]["data"])
    return rows[0]


def use_task(tid: int) -> None:
    tx("UPDATE tasks SET used = used + 1 WHERE id=?", (tid,))


def task_counts(mode: str) -> dict[int, int]:
    return {r["mid"]: r["n"] for r in q("SELECT mid, COUNT(*) n FROM tasks WHERE mode=? GROUP BY mid", (mode,))}


# ---------- 话题演练 ----------

def add_drill(topic: str, prompt: str, text: str, feedback: dict) -> int:
    return tx("INSERT INTO drills(topic, prompt, text, feedback, created) VALUES(?,?,?,?,?)",
              (topic, prompt, text, json.dumps(feedback, ensure_ascii=False), time.time())).lastrowid


def list_drills(limit: int = 50) -> list[dict]:
    rows = q("SELECT * FROM drills ORDER BY created DESC LIMIT ?", (limit,))
    for r in rows:
        r["feedback"] = json.loads(r["feedback"] or "{}")
    return rows


def delete_drill(did: int) -> None:
    tx("DELETE FROM drills WHERE id=?", (did,))


# ---------- 话题翻译 ----------
# trans：题目（AI 出的长短句缓存，或者从素材例句来的）；trans_log：每次作答和批改

def _trans_row(r: dict) -> dict:
    for k in ("hints", "targets"):
        r[k] = json.loads(r.get(k) or "[]")
    return r


def add_trans(topic: str, item: dict, source: str) -> int:
    return tx("INSERT INTO trans(topic, length, zh, ref, hints, targets, source, created) VALUES(?,?,?,?,?,?,?,?)",
              (topic, item["length"], item["zh"], item.get("ref", ""), json.dumps(item.get("hints", []), ensure_ascii=False),
               json.dumps(item.get("targets", [])), source, time.time())).lastrowid


def get_trans(tid: int) -> dict | None:
    rows = q("SELECT * FROM trans WHERE id=?", (tid,))
    return _trans_row(rows[0]) if rows else None


def unused_trans(topic: str, lengths: set[str], exclude: set[int]) -> list[dict]:
    rows = [_trans_row(r) for r in q("SELECT * FROM trans WHERE topic=? AND source='ai' AND used=0 ORDER BY id", (topic,))]
    return [r for r in rows if r["length"] in lengths and r["id"] not in exclude]


def use_trans(tid: int) -> None:
    tx("UPDATE trans SET used = used + 1 WHERE id=?", (tid,))


def add_trans_log(item: dict, answer: str, feedback: dict) -> int:
    return tx("INSERT INTO trans_log(item, topic, zh, answer, feedback, created) VALUES(?,?,?,?,?,?)",
              (item["id"], item["topic"], item["zh"], answer, json.dumps(feedback, ensure_ascii=False), time.time())).lastrowid


def list_trans_log(limit: int = 30) -> list[dict]:
    rows = q("SELECT * FROM trans_log ORDER BY created DESC LIMIT ?", (limit,))
    for r in rows:
        r["feedback"] = json.loads(r["feedback"] or "{}")
    return rows


# ---------- 数据维护（设置页） ----------

def restore_builtin() -> int:
    """把删掉的内置素材加回来"""
    n = q("SELECT COUNT(*) n FROM removed")[0]["n"]
    tx("DELETE FROM removed")
    kv_set("builtin_version", 0)
    with _lock:
        _seed(db())
    return n


def clear_ai_cache() -> int:
    """删掉还没用过的 AI 出题缓存（改写题、翻译题），之后会按新设置重新出"""
    n = q("SELECT COUNT(*) n FROM tasks")[0]["n"] + q("SELECT COUNT(*) n FROM trans WHERE used=0")[0]["n"]
    tx("DELETE FROM tasks")
    tx("DELETE FROM trans WHERE used=0")
    return n


def reset_progress() -> int:
    """清空复习进度和练习记录，素材本身保留"""
    n = q("SELECT COUNT(*) n FROM materials WHERE state IS NOT NULL")[0]["n"]
    tx("UPDATE materials SET state=NULL")
    tx("DELETE FROM log")
    return n


# ---------- 学习记录导出 / 导入 ----------

BACKUP_FORMAT = "chuanwen-backup/1"


def export_backup() -> dict:
    mats = []
    for m in list_materials():
        mats.append({k: m[k] for k in ("kind", "form", "zh", "examples", "topics", "register", "alts", "note", "source",
                                       "starred", "user_note", "added", "state")})
    topics = [{k: t[k] for k in ("slug", "en", "zh", "glyph", "prompts")} for t in list_topics() if not t["builtin"]]
    return {"format": BACKUP_FORMAT, "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "materials": mats,
            "topics": topics, "removed": [r["norm"] for r in q("SELECT norm FROM removed")],
            "log": q("SELECT day, mid, mode, grade, at FROM log"), "drills": q("SELECT topic, prompt, text, feedback, created FROM drills")}


def import_backup(data: dict) -> dict:
    """合并导入：新素材加进来；已有的保留练得更多的那份进度；收藏和批注取并集"""
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise ValueError("不是川文的学习记录文件")
    have_topics = topic_slugs()
    for t in data.get("topics", []):
        if t.get("slug") and t["slug"] not in have_topics:
            tx("INSERT INTO topics(slug, en, zh, glyph, prompts, builtin, ord) VALUES(?,?,?,?,?,0,?)",
               (t["slug"], t.get("en", ""), t.get("zh", ""), t.get("glyph", "文"),
                json.dumps(t.get("prompts", []), ensure_ascii=False), int(time.time())))
    added = updated = 0
    for m in data.get("materials", []):
        if not m.get("kind") or not m.get("form"):
            continue
        mid, new = add_material(m, m.get("source") or "import")
        cur = get_material(mid)
        if new:
            added += 1
        theirs, mine = m.get("state"), cur["state"]

        def reps(st):
            return st.get("reps", 0) if st else -1
        if reps(theirs) > reps(mine):
            tx("UPDATE materials SET state=? WHERE id=?", (json.dumps(theirs), mid))
            updated += 0 if new else 1
        if m.get("starred"):
            tx("UPDATE materials SET starred=1 WHERE id=?", (mid,))
        if m.get("user_note") and not cur["user_note"]:
            tx("UPDATE materials SET user_note=? WHERE id=?", (m["user_note"], mid))
    for n in data.get("removed", []):
        tx("INSERT OR IGNORE INTO removed VALUES(?)", (n,))
    return {"added": added, "updated": updated}
