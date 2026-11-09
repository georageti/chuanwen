"""内置话题和素材：解析 data/builtin.txt（格式见文件开头）。改了素材文件要把 VERSION 加一，老用户才会补上新条目。"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

VERSION = 2
DATA = Path(__file__).resolve().parent.parent / "data"
PATHS = [DATA / "builtin.txt", DATA / "builtin_more.txt"]  # builtin_more.txt 是 AI 生成、校验过的扩充（scripts/expand_builtin.py）
KIND_OF = {"c": "collocation", "s": "pattern", "l": "linker", "i": "idea"}


class BuiltinError(ValueError):
    pass


def parse_example(text: str) -> tuple[str, list[str]]:
    """'Cities [ease congestion] by ...' → ('Cities ease congestion by ...', ['ease congestion'])"""
    pieces = re.findall(r"\[([^\]]+)\]", text)
    clean = re.sub(r"\[([^\]]+)\]", r"\1", text)
    return clean.strip(), [p.strip() for p in pieces if p.strip()]


@lru_cache(maxsize=1)
def load() -> dict:
    topics, materials = [], []
    by_slug: dict[str, dict] = {}
    lines = []
    for path in PATHS:
        if path.exists():
            lines += [(f"{path.name} 第 {n} 行", raw) for n, raw in enumerate(path.read_text("utf-8").splitlines(), 1)]
    cur = None
    for n, raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@topic"):
            f = [x.strip() for x in line[len("@topic"):].split("|")]
            if len(f) != 4:
                raise BuiltinError(f"{n}：@topic 需要 4 段")
            cur = by_slug.get(f[0])
            if cur is None:
                cur = by_slug[f[0]] = {"slug": f[0], "en": f[1], "zh": f[2], "glyph": f[3], "prompts": []}
                topics.append(cur)
            continue
        if cur is None:
            raise BuiltinError(f"{n}：素材前面要先有 @topic")
        f = [x.strip() for x in line.split("|")]
        tag = f[0]
        if tag == "p":
            if len(f) < 3:
                raise BuiltinError(f"{n}：作文题需要英文和中文")
            cur["prompts"].append({"en": f[1], "zh": f[2]})
            continue
        kind = KIND_OF.get(tag)
        if not kind:
            raise BuiltinError(f"{n}：不认识的类型 {tag}")
        if kind == "idea":
            if len(f) < 3:
                raise BuiltinError(f"{n}：论点需要英文和中文")
            materials.append({"kind": kind, "form": f[1], "zh": f[2], "topics": [cur["slug"]],
                              "examples": [{"en": f[1], "zh": f[2], "answer": []}], "alts": [], "note": ""})
            continue
        if len(f) < 5:
            raise BuiltinError(f"{n}：{kind} 需要至少 5 段")
        en, answer = parse_example(f[3])
        if not answer:
            raise BuiltinError(f"{n}：例句里没有 [挖空]")
        extra = f[5] if len(f) > 5 else ""
        m = {"kind": kind, "form": f[1], "zh": f[2], "topics": [cur["slug"]],
             "examples": [{"en": en, "zh": f[4], "answer": answer}], "alts": [], "note": ""}
        if kind == "pattern":
            m["note"] = extra
        elif extra:
            m["alts"] = [a.strip() for a in extra.split(";") if a.strip()]
        materials.append(m)
    return {"topics": topics, "materials": materials}


def to_line(m: dict) -> str:
    """素材 → builtin 文件里的一行（例句里给答案加回方括号）"""
    clean = lambda x: re.sub(r"\s+", " ", str(x)).replace("|", "/").strip()  # noqa: E731
    if m["kind"] == "idea":
        return f"i | {clean(m['form'])} | {clean(m['zh'])}"
    from .ai import make_cloze

    e = m["examples"][0]
    segs = make_cloze(e["en"], e["answer"]) or [{"t": e["en"]}]
    out = "".join(f"[{x['blank']}]" if "blank" in x else x["t"] for x in segs)
    tag = {"collocation": "c", "pattern": "s", "linker": "l"}[m["kind"]]
    extra = m.get("note", "") if m["kind"] == "pattern" else "; ".join(m.get("alts", []))
    line = f"{tag} | {clean(m['form'])} | {clean(m['zh'])} | {clean(out)} | {clean(e.get('zh', ''))}"
    return line + (f" | {clean(extra)}" if extra else "")
