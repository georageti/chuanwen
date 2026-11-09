"""用 AI 批量扩充内置素材，写到 data/builtin_more.txt（覆盖）。生成后要人工抽查，再把 builtin.py 的 VERSION 加一。

用法：.venv/bin/python -I scripts/expand_builtin.py
读取川文设置里的 AI（默认数据目录，或 CHUANWEN_HOME）。每条都过自动校验：例句里的 [方括号] 必须对得上、
有中文、搭配 2-7 个词、和已有素材不重复。"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chuanwen import ai, builtin, store  # noqa: E402

NEW_TOPICS = [
    ("housing", "Housing & Architecture", "住房与建筑", "住"),
    ("food", "Food & Diet", "饮食", "食"),
    ("gender", "Gender Equality", "性别平等", "等"),
    ("language", "Language Learning", "语言学习", "语"),
    ("charity", "Charity & Volunteering", "慈善与志愿", "善"),
    ("energy", "Energy", "能源", "能"),
    ("poverty", "Poverty & Development", "贫困与发展", "贫"),
    ("internet", "Internet & Privacy", "网络与隐私", "网"),
    ("leisure", "Leisure & Lifestyle", "休闲生活", "闲"),
    ("history", "History & Tradition", "历史与传统", "史"),
    ("children", "Childhood & Parenting", "儿童成长", "童"),
]


def main() -> None:
    cfg = store.load_config()
    reason = ai.unavailable_reason(cfg)
    if reason:
        sys.exit(reason)
    base = builtin.load()
    topics = [dict(t) for t in base["topics"]] + [{"slug": s, "en": e, "zh": z, "glyph": g, "prompts": []} for s, e, z, g in NEW_TOPICS]
    have = {t["slug"]: [m["form"] for m in base["materials"] if t["slug"] in m["topics"]] for t in topics}
    seen = {store.norm(m["kind"], m["form"]) for m in base["materials"]}

    jobs = []  # (话题, 要多少)
    for t in topics:
        if t["slug"] == "general":
            jobs += [(t, {"pattern": 28}), (t, {"pattern": 28}), (t, {"linker": 18}), (t, {"linker": 18})]
        elif any(t["slug"] == s for s, *_ in NEW_TOPICS):
            jobs += [(t, {"collocation": 30, "idea": 12, "prompt": 4})]
        else:
            jobs += [(t, {"collocation": 24, "idea": 10, "prompt": 2})]

    def run(job):
        t, counts = job
        for attempt in range(3):
            try:
                return t, ai.expand(t, have[t["slug"]], cfg, counts)
            except ai.AIError as e:
                print(f"  {t['slug']} 第 {attempt + 1} 次失败：{e}", flush=True)
                time.sleep(2)
        return t, {"materials": [], "prompts": [], "dropped": 0}

    t0 = time.time()
    results: dict[str, dict] = {t["slug"]: {"materials": [], "prompts": []} for t in topics}
    dropped = dupes = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for t, r in pool.map(run, jobs):
            dropped += r["dropped"]
            for m in r["materials"]:
                n = store.norm(m["kind"], m["form"])
                if n in seen:
                    dupes += 1
                    continue
                seen.add(n)
                results[t["slug"]]["materials"].append(m)
            results[t["slug"]]["prompts"] += r["prompts"]
            print(f"  {t['slug']:<14} +{len(r['materials'])}（丢弃 {r['dropped']}）", flush=True)

    print("审校中…", flush=True)
    removed_total = 0

    def rev(slug):
        mats, removed = ai.review(results[slug]["materials"], cfg)
        return slug, mats, removed
    with ThreadPoolExecutor(max_workers=6) as pool:
        for slug, mats, removed in pool.map(rev, [t["slug"] for t in topics if results[t["slug"]]["materials"]]):
            results[slug]["materials"] = mats
            removed_total += removed
            print(f"  {slug:<14} 审校删掉 {removed}，剩 {len(mats)}", flush=True)

    lines = ["# 川文内置素材扩充：AI 生成（scripts/expand_builtin.py），经自动校验和人工抽查。格式同 builtin.txt。", ""]
    total = 0
    for t in topics:
        r = results[t["slug"]]
        if not r["materials"] and not r["prompts"]:
            continue
        lines.append(f"@topic {t['slug']} | {t['en']} | {t['zh']} | {t['glyph']}")
        lines += [f"p | {p['en'].replace('|', '/')} | {p['zh'].replace('|', '/')}" for p in r["prompts"]]
        order = {"collocation": 0, "pattern": 1, "linker": 2, "idea": 3}
        for m in sorted(r["materials"], key=lambda m: order[m["kind"]]):
            lines.append(builtin.to_line(m))
            total += 1
        lines.append("")
    out = builtin.DATA / "builtin_more.txt"
    out.write_text("\n".join(lines), "utf-8")
    print(f"写入 {out}：{total} 条，校验丢弃 {dropped}，重复丢弃 {dupes}，审校删掉 {removed_total}，用时 {time.time() - t0:.0f} 秒")


if __name__ == "__main__":
    main()
