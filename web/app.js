/* 川文网页：原生 JS，按地址栏 # 后面的路径切换页面 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const main = () => $("#main");

const KIND = { collocation: "搭配", pattern: "句型", idea: "论点素材", linker: "衔接" };
const KIND_EN = { collocation: "Collocation", pattern: "Pattern", idea: "Idea", linker: "Linker" };
const CLOZE = new Set(["collocation", "pattern", "linker"]);

const ICON = {
  star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>',
  starOn: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.6"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>',
  speak: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  note: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5 4h14v16H5zM9 9h6M9 13h6M9 17h3"/></svg>',
  pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m4 20 4-1 11-11-3-3L5 16zM14 6l3 3"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 4v12M6 11l6 6 6-6M5 20h14"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 5v14M5 12h14"/></svg>',
};

// ---------- 通用 ----------

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body || opts.form ? "POST" : "GET"), headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  Object.assign(init.headers, opts.headers || {});
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new Error((data && data.error) || `出错了（${r.status}）`);
  return data;
}

let toastTimer;
function toast(msg, isErr) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); document.body.appendChild(t); }
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), isErr ? 5000 : 2600);
}

function fail(e) { toast(e.message || String(e), true); }

function loading(msg) { return `<div class="loading">${esc(msg)}</div>`; }

const pad = (n, w = 2) => String(n).padStart(w, "0");

// 设置（启动时从服务器读一次，改设置后更新）
let CFG = { study: { max_tries: 3, auto_speak: true }, drill: { min_words: 20 }, translate: { length: "mix", show_hints: true },
  voice: { name: "en-US-AriaNeural", rate: -5 }, ui: { font: "normal", motion: true, serif: true, input: "sans" } };
async function loadCfg() {
  try { CFG = await api("/api/settings"); } catch { /* 用默认值 */ }
  applyUi();
}
function applyUi() {
  const d = document.documentElement.dataset;
  d.font = CFG.ui.font || "normal";
  d.motion = CFG.ui.motion === false ? "off" : "on";
  d.serif = CFG.ui.serif === false ? "off" : "on";
  d.input = CFG.ui.input || "sans";
  pickVoice();
}

// 朗读：和川听同一套微软神经网络语音（服务器合成并缓存）；选了“浏览器自带”或者合成失败时才用浏览器的语音
let voice = null;
function pickVoice() {
  if (!("speechSynthesis" in window)) return;
  const vs = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.startsWith("en"));
  voice = vs.find((v) => /Samantha|Ava|Allison|Daniel|Karen|Serena/.test(v.name)) || vs.find((v) => v.lang === "en-US") || vs[0] || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

function browserSpeak(text, rate) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = "en-US";
  u.rate = Math.max(0.5, Math.min(1.5, 1 + (rate ?? -5) / 100));
  speechSynthesis.speak(u);
}

function ttsUrl(text, opts = {}) {
  const q = new URLSearchParams({ text });
  if (opts.voice) q.set("voice", opts.voice);
  if (opts.rate !== undefined) q.set("rate", opts.rate);
  return "/api/tts?" + q;
}

let playing = null;
function stopSpeak() {
  if (playing) { playing.pause(); playing = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function speak(text, opts = {}) {
  if (!text) return;
  stopSpeak();
  const name = opts.voice || (CFG.voice && CFG.voice.name);
  const rate = opts.rate ?? (CFG.voice && CFG.voice.rate);
  if (name === "browser") { browserSpeak(text, rate); return; }
  const a = new Audio(ttsUrl(text, opts));
  playing = a;
  let fell = false;
  const fallback = () => { if (!fell && playing === a) { fell = true; browserSpeak(text, rate); } };
  a.onerror = fallback;
  a.play().catch((e) => { if (e && e.name !== "AbortError") fallback(); });
}

// 提前让服务器合成好，点朗读时就是秒开
function prefetchAudio(text) {
  if (!text || (CFG.voice && CFG.voice.name === "browser")) return;
  fetch(ttsUrl(text)).catch(() => {});
}

// 在例句里标出答案（按顺序查找，不分大小写）
function highlight(en, answer) {
  if (!answer || !answer.length) return esc(en);
  let out = "", pos = 0;
  const low = en.toLowerCase();
  for (const p of answer) {
    const i = low.indexOf(p.toLowerCase(), pos);
    if (i < 0) return esc(en);
    out += esc(en.slice(pos, i)) + `<mark>${esc(en.slice(i, i + p.length))}</mark>`;
    pos = i + p.length;
  }
  return out + esc(en.slice(pos));
}

// "[ease congestion] in cities" → {en, answer}
function parseBrackets(text) {
  const answer = [...text.matchAll(/\[([^\]]+)\]/g)].map((m) => m[1].trim()).filter(Boolean);
  return { en: text.replace(/\[([^\]]+)\]/g, "$1").trim(), answer };
}

let TOPICS = [];
async function loadTopics() { TOPICS = await api("/api/topics"); return TOPICS; }
const topicOf = (slug) => TOPICS.find((t) => t.slug === slug);
const topicZh = (slug) => (topicOf(slug) || {}).zh || slug;

function topicOptions(selected, allLabel = "全部话题") {
  return `<option value="">${allLabel}</option>` + TOPICS.map((t) => `<option value="${t.slug}" ${t.slug === selected ? "selected" : ""}>${esc(t.zh)} · ${esc(t.en)}</option>`).join("");
}

function kindOptions(selected) {
  return `<option value="">全部类型</option>` + Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${k === selected ? "selected" : ""}>${v}</option>`).join("");
}

function modal(html, onMount) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal fade-in">${html}</div>`;
  document.body.appendChild(back);
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  if (onMount) onMount(back.firstElementChild, close);
  return close;
}

// 编辑一条素材（划选加入、手动添加都用这个）
function editMaterial(cand, { title = "加入素材库", withExample = false } = {}) {
  return new Promise((resolve) => {
    const sel = new Set(cand.topics || []);
    let kind = cand.kind || "collocation";
    const ex = (cand.examples || [])[0];
    modal(`
      <span class="label">${esc(title)}</span>
      <h3 class="h3" style="margin:6px 0 18px">${esc(cand.form || "新素材")}</h3>
      <div class="form-grid">
        <label><span class="label">类型</span><div class="chips" data-kinds>${Object.entries(KIND).map(([k, v]) => `<button type="button" class="chip ${k === kind ? "on" : ""}" data-k="${k}">${v}</button>`).join("")}</div></label>
        <label><span class="label">素材（可复用的形式，空位写 sth / sb，句型写 X / Y）</span><input class="field" name="form" value="${esc(cand.form || "")}"></label>
        <label><span class="label">中文意思</span><input class="field" name="zh" value="${esc(cand.zh || "")}" placeholder="可以不填"></label>
        ${withExample ? `<label><span class="label">例句（用 [方括号] 标出要挖空的部分；可不填）</span><textarea class="field" name="example" style="min-height:80px" placeholder="Governments should invest in broadband to [bridge the digital divide]."></textarea></label>
        <label><span class="label">例句中文</span><input class="field" name="exzh" placeholder="可以不填"></label>` : ex ? `<div><span class="label">例句</span><p class="serif" style="margin:6px 0 0;font-size:17px">${highlight(ex.en, ex.answer)}</p>${ex.zh ? `<p class="dim" style="margin:4px 0 0;font-size:13px">${esc(ex.zh)}</p>` : ""}</div>` : ""}
        <label><span class="label">话题（可多选）</span><div class="chips" data-topics>${TOPICS.map((t) => `<button type="button" class="chip ${sel.has(t.slug) ? "on" : ""}" data-t="${t.slug}">${esc(t.zh)}</button>`).join("")}</div></label>
        <label><span class="label">用法提示</span><input class="field" name="note" value="${esc(cand.note || "")}" placeholder="可以不填"></label>
      </div>
      <div class="foot"><button class="btn line" data-cancel>取消</button><button class="btn primary" data-ok>保存</button></div>`,
    (box, close) => {
      box.querySelector("[data-kinds]").onclick = (e) => {
        const b = e.target.closest("[data-k]"); if (!b) return;
        kind = b.dataset.k;
        $$("[data-k]", box).forEach((x) => x.classList.toggle("on", x === b));
      };
      box.querySelector("[data-topics]").onclick = (e) => {
        const b = e.target.closest("[data-t]"); if (!b) return;
        sel.has(b.dataset.t) ? sel.delete(b.dataset.t) : sel.add(b.dataset.t);
        b.classList.toggle("on");
      };
      box.querySelector("[data-cancel]").onclick = () => { close(); resolve(null); };
      box.querySelector("[data-ok]").onclick = () => {
        const form = box.querySelector("[name=form]").value.trim();
        if (!form) { toast("素材内容不能为空", true); return; }
        let examples = cand.examples || [];
        if (withExample) {
          const raw = box.querySelector("[name=example]").value.trim();
          if (raw) {
            const p = parseBrackets(raw);
            examples = [{ en: p.en, zh: box.querySelector("[name=exzh]").value.trim(), answer: kind === "idea" ? [] : p.answer }];
          } else if (kind === "idea") {
            examples = [{ en: form, zh: box.querySelector("[name=zh]").value.trim(), answer: [] }];
          }
        } else if (kind === "idea") {
          examples = examples.map((e) => ({ ...e, answer: [] }));
        }
        close();
        resolve({ ...cand, kind, form, zh: box.querySelector("[name=zh]").value.trim(), note: box.querySelector("[name=note]").value.trim(), topics: [...sel], examples });
      };
      setTimeout(() => box.querySelector("[name=form]").focus(), 30);
    });
  });
}

// ---------- 路由 ----------

const routes = {
  "": home, library, docs, doc, practice, recall, rewrite, drill, translate, settings,
};

let routeToken = 0;
async function render() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const [name, arg] = path.split("/");
  const params = new URLSearchParams(query || "");
  const fn = routes[name] || home;
  $$(".top [data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === name || (name === "doc" && a.dataset.nav === "docs") || (["recall", "rewrite", "drill", "translate"].includes(name) && a.dataset.nav === "practice")));
  const token = ++routeToken;
  window.onkeydown = null;
  stopSpeak();
  main().innerHTML = loading("加载中…");
  try {
    if (!TOPICS.length) await Promise.all([loadTopics(), loadCfg()]);
    await fn(arg ? decodeURIComponent(arg) : "", params, () => token === routeToken);
    window.scrollTo({ top: 0 });
  } catch (e) {
    if (token === routeToken) main().innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", render);

// ---------- 首页 ----------

async function home() {
  const [s] = await Promise.all([api("/api/stats"), loadTopics()]);
  const quota = Math.max(0, s.dailyNew - s.learnedToday);
  const todayN = s.due + Math.min(quota, s.new);
  const lines = [
    { label: "recall", text: `due ${s.due} · new ${Math.min(quota, s.new)} · library ${s.materials}`, href: "#/recall" },
    { label: "rewrite", text: s.aiReady ? "用库里的搭配升级平淡的句子" : "需要先在设置里填 AI Key", href: s.aiReady ? "#/rewrite" : "#/settings" },
    { label: "drill", text: `${s.drills} 篇 · 选一个话题写一段`, href: "#/drill" },
    { label: "translate", text: `${s.translations} 题 · 看中文写英文，AI 给更地道的说法`, href: "#/translate" },
  ];
  const max = Math.max(1, ...s.history.map((d) => d.n));
  main().innerHTML = `
  <section class="hero fade-in">
    <div>
      <span class="label">#英语写作语料本 · writing corpus</span>
      <h1 class="display" style="margin-top:18px">Words That<br>Carry Weight</h1>
      <p class="lead">把好文章拆成你自己的写作素材：好搭配、句型、论点和衔接。先记住，再改写，最后在话题作文里用出来。</p>
      <div class="actions">
        <a class="btn primary" href="#/recall">今天的复习 · ${todayN}</a>
        <a class="btn ghost" href="#/docs">${ICON.plus}导入文章</a>
      </div>
      <div class="term">
        <span class="label">今天 · today</span>
        <div class="term-tabs">${lines.map((l, i) => `<button data-i="${i}" class="${i ? "" : "on"}"><span>${l.label}</span></button>`).join("")}</div>
        <div class="term-body"><span id="termline">${esc(lines[0].text)}</span><a id="termgo" href="${lines[0].href}">→ go</a></div>
      </div>
      ${s.aiReady ? "" : `<div class="notice" style="max-width:520px">还没有配置 AI：内置的 ${s.materials} 条素材可以直接练回忆；拆文章、改写批改、话题演练需要先到 <a href="#/settings">设置</a> 填 DeepSeek 或 Claude 的 Key。</div>`}
    </div>
    <div class="hero-art"><canvas data-art="hero" data-glyph="文"></canvas></div>
  </section>

  <section class="section">
    <div class="stats">
      <div class="stat"><span class="label">素材 · library</span><span class="num">${s.materials}</span></div>
      <div class="stat"><span class="label">我收集的 · mine</span><span class="num">${s.mine}</span></div>
      <div class="stat"><span class="label">到期 · due</span><span class="num">${s.due}</span></div>
      <div class="stat"><span class="label">已掌握 · mastered</span><span class="num">${s.mastered}<small>/ ${s.materials - s.new}</small></span></div>
    </div>
    <div style="margin-top:28px"><span class="label">近 14 天练习次数 · 今天 ${s.today}</span>
      <div class="bars">${s.history.map((d, i) => `<i class="${i === s.history.length - 1 ? "today" : ""}" style="height:${Math.max(2, (d.n / max) * 100)}%" title="${d.day}：${d.n}"></i>`).join("")}</div>
    </div>
  </section>

  <section class="section">
    <div class="section-head"><h2 class="h2">Topics<sup>${TOPICS.length}</sup></h2><a class="btn line small" href="#/library">打开素材库 ${ICON.arrow}</a></div>
    <div class="topics">${TOPICS.map((t, i) => `
      <a class="topic" href="#/library/${t.slug}">
        <div class="art"><canvas data-art="glyph" data-glyph="${esc(t.glyph)}" data-seed="${i}"></canvas></div>
        <span class="label">#${pad(i + 1)} · ${esc(t.en)}</span>
        <div class="h3">${esc(t.zh)}</div>
        <p>${t.count} 条素材</p>
      </a>`).join("")}</div>
  </section>

  <section class="section">
    <div class="section-head"><h2 class="h2">Practice<sup>4</sup></h2></div>
    ${modesHtml(s)}
  </section>`;
  const tabs = $$(".term-tabs button");
  tabs.forEach((b) => b.onclick = () => {
    tabs.forEach((x) => x.classList.toggle("on", x === b));
    const l = lines[+b.dataset.i];
    $("#termline").textContent = l.text;
    $("#termgo").href = l.href;
  });
  Art.paint(main());
}

function modesHtml(s) {
  const quota = Math.max(0, s.dailyNew - s.learnedToday);
  return `<div class="modes">
    <a class="mode" href="#/recall"><div class="art"><canvas data-art="glyph" data-glyph="忆" data-seed="31"></canvas></div>
      <span class="label">#1 recall</span><div class="h3">回忆</div>
      <p>看中文和例句，把挖掉的搭配、句型、衔接拼出来；论点素材看中文想英文。按 FSRS 安排复习。</p>
      <div class="foot"><span class="label">due ${s.due} · new ${Math.min(quota, s.new)}</span>${ICON.arrow}</div></a>
    <a class="mode" href="#/rewrite"><div class="art"><canvas data-art="glyph" data-glyph="改" data-seed="32"></canvas></div>
      <span class="label">#2 rewrite</span><div class="h3">改写升级</div>
      <p>给你一句平淡的英文，用指定的搭配或句型把它升级，AI 批改、给出修改和范句。</p>
      <div class="foot"><span class="label">${s.aiReady ? "ai ready" : "需要 AI"}</span>${ICON.arrow}</div></a>
    <a class="mode" href="#/drill"><div class="art"><canvas data-art="glyph" data-glyph="演" data-seed="33"></canvas></div>
      <span class="label">#3 drill</span><div class="h3">话题演练</div>
      <p>选一个话题，系统从库里挑出相关素材，你写一段，AI 标出用上了哪些、哪里不地道，并估分。</p>
      <div class="foot"><span class="label">${s.drills} 篇</span>${ICON.arrow}</div></a>
    <a class="mode" href="#/translate"><div class="art"><canvas data-art="glyph" data-glyph="译" data-seed="34"></canvas></div>
      <span class="label">#4 translate</span><div class="h3">话题翻译</div>
      <p>按话题给中文，有长句、好搭配、地道说法，你写英文；AI 评价准确和地道程度，给出改正、更地道的写法和别的选择。</p>
      <div class="foot"><span class="label">${s.translations} 题${s.aiReady ? "" : " · 没 AI 用素材例句"}</span>${ICON.arrow}</div></a>
  </div>`;
}

// ---------- 素材库 ----------

const libState = { kind: "", q: "", starred: false, mine: false };

async function library(slug) {
  await loadTopics();
  const all = await api("/api/materials" + (slug ? `?topic=${encodeURIComponent(slug)}` : ""));
  const t = topicOf(slug);
  main().innerHTML = `
  <div class="lib fade-in">
    <aside class="side">
      <span class="label">话题 · topics</span>
      <a href="#/library" class="${slug ? "" : "on"}"><span>全部</span><span></span></a>
      ${TOPICS.map((x) => `<a href="#/library/${x.slug}" class="${x.slug === slug ? "on" : ""}"><span>${esc(x.zh)}</span><span>${x.count}</span></a>`).join("")}
      <span class="label">自建 · custom</span>
      <a href="#" data-addtopic><span>+ 新话题</span><span></span></a>
    </aside>
    <div>
      <div class="section-head" style="margin-bottom:0">
        <div><span class="label">${t ? `#${esc(t.en)}` : "#library"}</span><h2 class="h2" style="margin-top:8px">${t ? esc(t.zh) : "Library"}</h2></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${t ? `<a class="btn blue small" href="#/drill/${t.slug}">话题演练 ${ICON.arrow}</a>` : ""}
          ${t ? `<button class="btn line small" data-expand>✦ AI 扩充</button>` : ""}
          <button class="btn line small" data-add>${ICON.plus}添加素材</button>
          <a class="btn line small" data-export href="#">${ICON.down}导出 Markdown</a>
          <button class="btn line small" data-print>打印</button>
          ${t && !t.builtin ? `<button class="btn line small" data-deltopic>删除话题</button>` : ""}
        </div>
      </div>
      <div class="toolbar">
        <div class="tabs" data-kinds>
          <button data-k="" class="${libState.kind ? "" : "on"}">全部<small>${all.length}</small></button>
          ${Object.entries(KIND).map(([k, v]) => `<button data-k="${k}" class="${libState.kind === k ? "on" : ""}">${v}<small>${all.filter((m) => m.kind === k).length}</small></button>`).join("")}
        </div>
        <input class="search" placeholder="搜索英文、中文或批注" value="${esc(libState.q)}">
        <label class="check"><input type="checkbox" data-starred ${libState.starred ? "checked" : ""}>收藏</label>
        <label class="check"><input type="checkbox" data-mine ${libState.mine ? "checked" : ""}>我收集的</label>
      </div>
      <div class="cards" id="cards"></div>
    </div>
  </div>`;

  const list = () => all.filter((m) => (!libState.kind || m.kind === libState.kind)
    && (!libState.starred || m.starred) && (!libState.mine || m.source !== "builtin")
    && (!libState.q || [m.form, m.zh, m.user_note, ...(m.examples || []).map((e) => e.en)].join(" ").toLowerCase().includes(libState.q.toLowerCase())));

  const exportHref = () => {
    const p = new URLSearchParams();
    if (slug) p.set("topic", slug);
    if (libState.kind) p.set("kind", libState.kind);
    if (libState.starred) p.set("starred", "true");
    if (libState.mine) p.set("source", "mine");
    return "/api/export/markdown?" + p;
  };

  const draw = () => {
    const rows = list();
    $("[data-export]").href = exportHref();
    $("#cards").innerHTML = rows.length ? rows.map(cardHtml).join("") : `<div class="empty">这里还没有素材。<br>去“文章”导入一篇范文，或点“添加素材”。</div>`;
  };
  draw();

  $("[data-kinds]").onclick = (e) => {
    const b = e.target.closest("[data-k]"); if (!b) return;
    libState.kind = b.dataset.k;
    $$("[data-k]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  };
  $(".search").oninput = (e) => { libState.q = e.target.value; draw(); };
  $("[data-starred]").onchange = (e) => { libState.starred = e.target.checked; draw(); };
  $("[data-mine]").onchange = (e) => { libState.mine = e.target.checked; draw(); };
  $("[data-print]").onclick = () => window.print();
  $("[data-addtopic]").onclick = (e) => { e.preventDefault(); addTopic(); };
  if ($("[data-deltopic]")) $("[data-deltopic]").onclick = async () => {
    if (!confirm(`删除话题“${t.zh}”？素材本身不会删除。`)) return;
    await api(`/api/topics/${t.slug}`, { method: "DELETE" }).catch(fail);
    await loadTopics(); location.hash = "#/library";
  };
  if ($("[data-expand]")) $("[data-expand]").onclick = () => expandTopic(t, libState.kind);
  $("[data-add]").onclick = async () => {
    const m = await editMaterial({ kind: libState.kind || "collocation", topics: slug ? [slug] : [] }, { title: "添加素材", withExample: true });
    if (!m) return;
    try {
      const r = await api("/api/materials", { body: { items: [m] } });
      toast(r.added ? "已加入素材库" : "库里已有这条，例句已合并");
      render();
    } catch (e) { fail(e); }
  };

  $("#cards").onclick = async (e) => {
    const card = e.target.closest(".card"); if (!card) return;
    const id = +card.dataset.id;
    const m = all.find((x) => x.id === id);
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "star") {
      m.starred = m.starred ? 0 : 1;
      api(`/api/materials/${id}`, { method: "PATCH", body: { starred: !!m.starred } }).catch(fail);
      card.outerHTML = cardHtml(m);
    } else if (act === "speak") {
      speak((m.examples[0] || {}).en || m.form);
    } else if (act === "note") {
      const box = card.querySelector(".unote");
      box.classList.toggle("hidden");
      if (!box.classList.contains("hidden")) box.focus();
    } else if (act === "rewrite") {
      location.hash = `#/rewrite?id=${id}`;
    } else if (act === "del") {
      if (!confirm(`从素材库删除“${m.form}”？`)) return;
      await api(`/api/materials/${id}`, { method: "DELETE" }).catch(fail);
      all.splice(all.indexOf(m), 1);
      draw();
    }
  };
  $("#cards").addEventListener("focusout", (e) => {
    if (!e.target.classList.contains("unote")) return;
    const id = +e.target.closest(".card").dataset.id;
    const m = all.find((x) => x.id === id);
    if (m.user_note === e.target.value) return;
    m.user_note = e.target.value;
    api(`/api/materials/${id}`, { method: "PATCH", body: { user_note: m.user_note } }).then(() => toast("批注已保存")).catch(fail);
  });
}

function cardHtml(m) {
  const ex = (m.examples || [])[0];
  const st = m.state;
  const status = st ? (st.s >= 60 ? "已掌握" : `下次 ${st.due.slice(5)}`) : "新";
  return `<article class="card ${m.kind}" data-id="${m.id}">
    <div class="row"><span class="label">#${pad(m.id, 3)} · <span class="tag k-${m.kind}">${KIND[m.kind]}</span> ${m.topics.slice(0, 2).map((t) => esc(topicZh(t))).join(" · ")}</span><span class="label">${status}</span></div>
    <h3 class="form">${esc(m.form)}</h3>
    ${m.zh ? `<div class="meaning">${esc(m.zh)}</div>` : ""}
    ${ex && m.kind !== "idea" ? `<p class="ex">${highlight(ex.en, ex.answer)}</p>${ex.zh ? `<div class="exzh">${esc(ex.zh)}</div>` : ""}` : ""}
    ${m.alts && m.alts.length ? `<div class="alts"><b>≈</b> ${m.alts.map(esc).join(" / ")}</div>` : ""}
    ${m.note ? `<div class="alts">${esc(m.note)}</div>` : ""}
    ${m.user_note ? `<div class="meaning" style="color:var(--blue-hi)">✎ ${esc(m.user_note)}</div>` : ""}
    <textarea class="unote hidden" placeholder="写点批注：在哪篇作文里用过、容易错在哪…">${esc(m.user_note || "")}</textarea>
    <div class="tools">
      <button class="iconbtn ${m.starred ? "on" : ""}" data-act="star" title="收藏">${m.starred ? ICON.starOn : ICON.star}</button>
      <button class="iconbtn" data-act="speak" title="朗读">${ICON.speak}</button>
      <button class="iconbtn" data-act="note" title="批注">${ICON.note}</button>
      ${CLOZE.has(m.kind) ? `<button class="iconbtn" data-act="rewrite" title="用它做改写练习">${ICON.pen}</button>` : ""}
      <span class="spacer"></span>
      <span class="label">${{ builtin: "内置", doc: "来自文章", ai: "AI 扩充" }[m.source] || "手动"}</span>
      <button class="iconbtn" data-act="del" title="删除">${ICON.trash}</button>
    </div>
  </article>`;
}

// AI 给话题再出一批素材：先看、再挑、再加入
function expandTopic(t, kind) {
  const what = kind ? KIND[kind] : (t.slug === "general" ? "句型和衔接" : "搭配和论点");
  modal(`<span class="label">AI 扩充 · ${esc(t.en)}</span>
    <h3 class="h3" style="margin:6px 0 6px">${esc(t.zh)}：再来一批${esc(what)}</h3>
    <p class="muted" style="margin:0 0 14px;font-size:14px">AI 生成后会再复核一遍中文和近义说法，不会和库里已有的重复。勾掉不想要的，再加入素材库。</p>
    <div id="xp">${loading("AI 正在生成并复核，约 20 秒…")}</div>
    <div class="foot"><button class="btn line" data-cancel>取消</button><button class="btn primary" data-ok disabled>加入素材库</button></div>`,
  async (box, close) => {
    box.style.width = "min(760px,100%)";
    box.querySelector("[data-cancel]").onclick = close;
    let items = [];
    const sel = new Set();
    try {
      items = (await api(`/api/topics/${t.slug}/expand`, { body: { kind } })).items;
    } catch (e) {
      box.querySelector("#xp").innerHTML = `<div class="notice">${esc(e.message)}</div>`;
      return;
    }
    items.forEach((_, i) => sel.add(i));
    const ok = box.querySelector("[data-ok]");
    const sync = () => {
      $$(".item", box).forEach((b) => b.classList.toggle("sel", sel.has(+b.dataset.i)));
      ok.disabled = !sel.size;
      ok.textContent = sel.size ? `加入 ${sel.size} 条` : "加入素材库";
    };
    box.querySelector("#xp").innerHTML = `<div style="display:grid;gap:8px">${items.map((m, i) => {
      const ex = m.examples[0];
      return `<button class="item" data-i="${i}" style="align-items:flex-start;width:100%">
        <span class="box" style="margin-top:4px"></span>
        <span style="display:grid;gap:3px"><span><span class="tag k-${m.kind}">${KIND[m.kind]}</span> <span class="f">${esc(m.form)}</span> <span class="z">${esc(m.zh)}</span></span>
        ${m.kind !== "idea" ? `<span class="serif dim" style="font-size:15px">${highlight(ex.en, ex.answer)}</span>` : ""}
        ${m.alts && m.alts.length ? `<span class="mono dim" style="font-size:11px">≈ ${m.alts.map(esc).join(" / ")}</span>` : ""}</span></button>`;
    }).join("")}</div>`;
    sync();
    box.querySelector("#xp").onclick = (e) => {
      const b = e.target.closest(".item"); if (!b) return;
      const i = +b.dataset.i;
      sel.has(i) ? sel.delete(i) : sel.add(i);
      sync();
    };
    ok.onclick = async () => {
      try {
        const r = await api("/api/materials", { body: { source: "ai", items: items.filter((_, i) => sel.has(i)) } });
        close(); toast(`加入了 ${r.added} 条`); render();
      } catch (e) { fail(e); }
    };
  });
}

function addTopic() {
  modal(`<span class="label">新话题</span><h3 class="h3" style="margin:6px 0 18px">自建话题</h3>
    <div class="form-grid">
      <label><span class="label">中文名</span><input class="field" name="zh" placeholder="比如：食品安全"></label>
      <label><span class="label">English</span><input class="field" name="en" placeholder="Food Safety"></label>
    </div>
    <div class="foot"><button class="btn line" data-cancel>取消</button><button class="btn primary" data-ok>建立</button></div>`,
  (box, close) => {
    box.querySelector("[data-cancel]").onclick = close;
    box.querySelector("[data-ok]").onclick = async () => {
      try {
        const t = await api("/api/topics", { body: { zh: box.querySelector("[name=zh]").value, en: box.querySelector("[name=en]").value } });
        close(); await loadTopics(); location.hash = `#/library/${t.slug}`;
      } catch (e) { fail(e); }
    };
    setTimeout(() => box.querySelector("[name=zh]").focus(), 30);
  });
}

// ---------- 文章 ----------

async function docs() {
  const [list, s] = await Promise.all([api("/api/docs"), api("/api/stats")]);
  main().innerHTML = `
  <div class="fade-in">
    <div class="section-head"><div><span class="label">#import</span><h2 class="h2" style="margin-top:8px">Articles<sup>${list.length}</sup></h2></div></div>
    ${s.aiReady ? "" : `<div class="notice">${esc(s.aiReason)}：导入后只会分句，不会自动拆素材。你可以在句子里划选文字手动加入。<a href="#/settings">去设置</a></div>`}
    <div class="import">
      <div class="drop" id="drop">
        <span class="label">粘贴英文 · 或把 Word / TXT / Markdown 拖进这个框</span>
        <input class="field" id="dtitle" placeholder="标题（可不填）">
        <textarea class="field" id="dtext" placeholder="把范文、外刊段落、雅思范文粘贴到这里…"></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <button class="btn primary" id="go">${s.aiReady ? "拆素材" : "导入"} ${ICON.arrow}</button>
          <label class="btn line">选择文件<input type="file" id="dfile" accept=".docx,.txt,.md,.markdown" hidden></label>
          <span class="label" id="dmsg"></span>
        </div>
      </div>
      <div>
        <span class="label">AI 会逐句阅读，挑出四类素材</span>
        <div style="display:grid;gap:14px;margin-top:16px">
          ${[["collocation", "好搭配：curb carbon emissions、a pressing concern"], ["pattern", "句型骨架：It is not X but Y that …"], ["idea", "论点素材：可以直接搬进作文的论据和例子"], ["linker", "衔接：That said / By the same token"]].map(([k, d]) => `<div><span class="tag k-${k}">${KIND[k]}</span><p class="muted" style="margin:6px 0 0;font-size:14px">${d}</p></div>`).join("")}
        </div>
      </div>
    </div>
    <div class="section" style="margin-top:64px">
      <span class="label">已导入</span>
      <div class="doclist" style="margin-top:12px">
        ${list.length ? list.map((d) => `<a class="docrow" href="#/doc/${d.id}">
          <span class="label">#${pad(d.id, 3)}</span>
          <div style="min-width:0"><div class="h3">${esc(d.title)}</div><span class="label">${new Date(d.created * 1000).toLocaleDateString()} · ${d.chars} 字符 · ${d.mode === "ai" ? `AI 找到 ${d.found} 条` : "未拆分"} · 已收 ${d.saved}</span></div>
          ${ICON.arrow.replace("<svg", '<svg width="18" height="18"')}
        </a>`).join("") : `<div class="empty">还没有导入文章</div>`}
      </div>
    </div>
  </div>`;

  const send = async (fd) => {
    $("#go").disabled = true;
    $("#dmsg").textContent = s.aiReady ? "AI 正在逐句阅读，长文大约要 1 分钟…" : "正在分句…";
    try {
      const r = await api("/api/docs", { form: fd });
      location.hash = `#/doc/${r.id}`;
    } catch (e) { fail(e); $("#go").disabled = false; $("#dmsg").textContent = ""; }
  };
  $("#go").onclick = () => {
    const text = $("#dtext").value.trim();
    if (!text) { toast("先粘贴一段英文，或选择文件", true); return; }
    const fd = new FormData(); fd.append("text", text); fd.append("title", $("#dtitle").value.trim());
    send(fd);
  };
  const sendFile = (f) => { const fd = new FormData(); fd.append("file", f); fd.append("title", $("#dtitle").value.trim()); send(fd); };
  $("#dfile").onchange = (e) => e.target.files[0] && sendFile(e.target.files[0]);
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) sendFile(f); };
}

async function doc(id) {
  const d = await api(`/api/docs/${id}`);
  const selected = new Set();
  const items = [];
  d.sents.forEach((s) => s.items.forEach((it, j) => { it._key = `${s.sid}:${j}`; items.push(it); }));
  const found = items.length, saved = items.filter((i) => i.saved).length;

  const sentHtml = (s) => {
    // 把素材位置标在句子里（重叠的只标第一个）
    const marks = [];
    s.items.forEach((it) => (it.spans || []).forEach(([a, b]) => {
      if (!marks.some((m) => a < m.b && b > m.a)) marks.push({ a, b, it });
    }));
    marks.sort((x, y) => x.a - y.a);
    let html = "", pos = 0;
    for (const m of marks) {
      html += esc(s.text.slice(pos, m.a)) + `<mark class="k-${m.it.kind}" data-key="${m.it._key}" title="${esc(KIND[m.it.kind] + "：" + m.it.form)}">${esc(s.text.slice(m.a, m.b))}</mark>`;
      pos = m.b;
    }
    html += esc(s.text.slice(pos));
    return `<div class="sent" data-sid="${s.sid}">
      <span class="label">#${pad(s.sid + 1)}</span>
      <div><div class="en">${html}</div>${s.zh ? `<div class="tr">${esc(s.zh)}</div>` : ""}
        ${s.items.length ? `<div class="items">${s.items.map((it) => `<button class="item ${it.saved ? "saved" : ""}" data-key="${it._key}">
          <span class="box"></span><span class="tag k-${it.kind}">${KIND[it.kind]}</span><span class="f">${esc(it.form)}</span><span class="z">${esc(it.zh)}</span></button>`).join("")}</div>` : ""}
      </div></div>`;
  };

  main().innerHTML = `
  <div class="fade-in">
    <a class="label" href="#/docs" style="text-decoration:none">← 文章</a>
    <div class="section-head" style="margin-top:14px">
      <div style="min-width:0;max-width:900px"><span class="label">#${pad(d.id, 3)} · ${d.sents.length} 句 · ${d.mode === "ai" ? `AI 找到 ${found} 条 · 已收 ${saved}` : "未拆分"}</span>
        <h2 class="h2" style="margin-top:8px;font-size:clamp(32px,4vw,56px)">${esc(d.title)}</h2></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn line small" data-remine>用 AI ${d.mode === "ai" ? "重新拆" : "拆素材"}</button>
        <button class="btn line small" data-del>删除文章</button>
      </div>
    </div>
    ${d.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("")}
    <div class="legend" style="margin:8px 0 6px">${Object.entries(KIND).map(([k, v]) => `<span><i style="background:${{ collocation: "#8f8fff", pattern: "#f2f2f2", idea: "#ffb27a", linker: "#7ad7ff" }[k]}"></i>${v}</span>`).join("")}<span>· 在句子里划选任意文字，可以手动加入</span></div>
    <div class="sents">${d.sents.map(sentHtml).join("")}</div>
    ${found ? `<div class="sticky-bar"><span class="mono" id="selcount">还没选</span>
      <span style="display:flex;gap:8px"><button class="btn ghost small" data-all>全选未收</button><button class="btn primary small" data-save disabled>加入素材库</button></span></div>` : ""}
  </div>`;

  const sync = () => {
    $$(".item").forEach((b) => b.classList.toggle("sel", selected.has(b.dataset.key)));
    if ($("#selcount")) {
      $("#selcount").textContent = selected.size ? `已选 ${selected.size} 条` : `共 ${found} 条，已收 ${items.filter((i) => i.saved).length}`;
      $("[data-save]").disabled = !selected.size;
    }
  };
  sync();
  const toggle = (key) => {
    const it = items.find((i) => i._key === key);
    if (!it || it.saved) return;
    selected.has(key) ? selected.delete(key) : selected.add(key);
    sync();
  };
  $(".sents").addEventListener("click", (e) => {
    const b = e.target.closest(".item, mark[data-key]");
    if (b && window.getSelection().isCollapsed) toggle(b.dataset.key);
  });
  if ($("[data-all]")) $("[data-all]").onclick = () => { items.filter((i) => !i.saved).forEach((i) => selected.add(i._key)); sync(); };
  if ($("[data-save]")) $("[data-save]").onclick = async () => {
    const chosen = items.filter((i) => selected.has(i._key));
    try {
      const r = await api("/api/materials", { body: { doc_id: d.id, items: chosen } });
      chosen.forEach((i) => { i.saved = true; });
      $$(".item").forEach((b) => { const it = items.find((i) => i._key === b.dataset.key); b.classList.toggle("saved", !!it.saved); });
      selected.clear(); sync();
      toast(`加入了 ${r.added} 条${chosen.length > r.added ? `，${chosen.length - r.added} 条库里已有（例句已合并）` : ""}`);
    } catch (e) { fail(e); }
  };
  $("[data-del]").onclick = async () => {
    if (!confirm("删除这篇文章？已经加入素材库的素材会保留。")) return;
    await api(`/api/docs/${d.id}`, { method: "DELETE" }).catch(fail);
    location.hash = "#/docs";
  };
  $("[data-remine]").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "AI 正在阅读…";
    try { await api(`/api/docs/${d.id}/mine`, { method: "POST" }); render(); }
    catch (err) { fail(err); e.target.disabled = false; e.target.textContent = "用 AI 拆素材"; }
  };

  // 划选文字 → 加入素材
  let pop = null;
  const hidePop = () => { if (pop) { pop.remove(); pop = null; } };
  document.addEventListener("mousedown", (e) => { if (pop && !pop.contains(e.target)) hidePop(); }, { once: false });
  $(".sents").addEventListener("mouseup", () => {
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel.toString().replace(/\s+/g, " ").trim();
      hidePop();
      if (!text || sel.rangeCount === 0) return;
      const en = sel.anchorNode && sel.anchorNode.parentElement && sel.anchorNode.parentElement.closest(".en");
      if (!en) return;
      const sid = +en.closest(".sent").dataset.sid;
      const sentence = d.sents.find((s) => s.sid === sid).text;
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      pop = document.createElement("div");
      pop.className = "popover";
      pop.innerHTML = `<button>${ICON.plus.replace("<svg", '<svg width="12" height="12"')} 加入素材</button>`;
      pop.style.left = `${rect.left + window.scrollX}px`;
      pop.style.top = `${rect.top + window.scrollY - 44}px`;
      document.body.appendChild(pop);
      pop.querySelector("button").onclick = async () => {
        pop.querySelector("button").textContent = "分析中…";
        try {
          const r = await api("/api/pick", { body: { selection: text, sentence, doc_id: d.id } });
          hidePop();
          if (!r.ai && r.reason) toast(`${r.reason}，请自己选类型、填中文`);
          const m = await editMaterial(r.candidate);
          if (!m) return;
          const res = await api("/api/materials", { body: { doc_id: d.id, items: [m] } });
          toast(res.added ? "已加入素材库" : "库里已有这条，例句已合并");
        } catch (e) { hidePop(); fail(e); }
      };
    }, 10);
  });
}

// ---------- 练习入口 ----------

const prState = { topic: "", kind: "" };

async function practice() {
  const s = await api("/api/stats");
  main().innerHTML = `
  <div class="fade-in">
    <div class="section-head"><div><span class="label">#practice</span><h2 class="h2" style="margin-top:8px">Practice<sup>4</sup></h2></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <select class="field" id="ptopic" style="width:auto">${topicOptions(prState.topic)}</select>
        <select class="field" id="pkind" style="width:auto">${kindOptions(prState.kind)}</select>
      </div>
    </div>
    <p class="muted" style="margin:-16px 0 30px">上面选的话题对“回忆”“改写”“翻译”生效，类型对“回忆”生效。每天新素材 ${s.dailyNew} 条，可以在设置里改。</p>
    ${modesHtml(s)}
    <div class="section" style="margin-top:72px"><span class="label">演练记录</span><div id="drills" style="margin-top:12px">${loading("")}</div></div>
  </div>`;
  $("#ptopic").onchange = (e) => { prState.topic = e.target.value; };
  $("#pkind").onchange = (e) => { prState.kind = e.target.value; };
  Art.paint(main());
  const list = await api("/api/drills");
  $("#drills").innerHTML = list.length ? list.map((x) => `<div class="docrow" style="grid-template-columns:70px minmax(0,1fr) auto">
      <span class="score" style="font-size:40px">${x.feedback.band || "–"}</span>
      <div style="min-width:0"><div class="muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.prompt)}</div>
      <span class="label">${esc(topicZh(x.topic))} · ${new Date(x.created * 1000).toLocaleString()} · ${x.feedback.words || ""} 词 · 用上 ${(x.feedback.used || []).length} 条</span></div>
      <button class="iconbtn" data-view="${x.id}" title="查看">${ICON.arrow}</button></div>`).join("") : `<div class="empty">还没有演练记录</div>`;
  $("#drills").onclick = (e) => {
    const b = e.target.closest("[data-view]"); if (!b) return;
    const x = list.find((y) => y.id === +b.dataset.view);
    modal(`<span class="label">${esc(topicZh(x.topic))} · band ${x.feedback.band}</span><p class="serif" style="font-size:18px">${esc(x.prompt)}</p>
      <div class="fb-block" style="margin-top:12px"><h4>你写的</h4><div class="improved" style="font-family:var(--body);font-style:normal;font-size:15px">${esc(x.text)}</div></div>
      ${feedbackHtml(x.feedback)}<div class="foot"><button class="btn line" data-cancel>关闭</button></div>`,
    (box, close) => { box.style.width = "min(820px,100%)"; box.querySelector("[data-cancel]").onclick = close; });
  };
}

// ---------- 回忆 ----------

function normAns(s) {
  return String(s).toLowerCase().replace(/[’‘]/g, "'").replace(/[.,;:!?"“”()]/g, "").replace(/\s+/g, " ").trim();
}

async function recall(_arg, params, alive) {
  const scope = params.get("scope") || "auto";
  const plan = await api("/api/recall/plan", { body: { topic: prState.topic, kind: prState.kind, scope } });
  if (!alive()) return;
  const filt = [prState.topic && topicZh(prState.topic), prState.kind && KIND[prState.kind]].filter(Boolean).join(" · ");
  if (!plan.ids.length) {
    main().innerHTML = `<div class="done fade-in"><span class="label">#recall ${filt ? "· " + esc(filt) : ""}</span>
      <h2 class="h2" style="margin:14px 0">All Caught Up</h2>
      <p class="muted">${plan.new && !plan.quota ? "今天的新素材额度用完了，到期的也复习完了。" : "这个范围里没有要复习的素材。"}</p>
      <div class="ctrl" style="justify-content:center">
        ${plan.new ? `<a class="btn primary" href="#/recall?scope=new">再学一组新素材</a>` : ""}
        <a class="btn ghost" href="#/rewrite">去做改写</a><a class="btn line" href="#/practice">换个范围</a></div></div>`;
    return;
  }
  const ids = plan.ids;
  const cards = [];
  const results = [];
  let idx = 0;

  const ensure = async (i) => {
    if (cards[i]) return;
    const start = Math.floor(i / 20) * 20;
    const got = await api("/api/recall/cards", { body: { ids: ids.slice(start, start + 20) } });
    got.forEach((c, j) => { cards[start + j] = c; });
  };

  const finish = () => {
    const firstTry = results.filter((r) => r.ok).length;
    main().innerHTML = `<div class="done fade-in"><span class="label">#recall · 本轮结束</span>
      <h2 class="display" style="margin:14px 0">${firstTry}<span class="dim">/${results.length}</span></h2>
      <p class="muted">一次答对 ${firstTry} 条，其余已按掌握程度安排了复习。</p>
      <div class="ctrl" style="justify-content:center"><a class="btn primary" href="#/recall?r=${Date.now()}">再来一轮</a><a class="btn ghost" href="#/rewrite">去做改写</a><a class="btn line" href="#/">回首页</a></div></div>`;
  };

  const show = async () => {
    if (idx >= ids.length) { finish(); return; }
    await ensure(idx);
    if (!alive()) return;
    const c = cards[idx];
    if (!c) { idx++; show(); return; }
    const st = { mistakes: 0, hinted: false, peeked: false, done: false };
    const head = `<div class="practice-top"><span class="label">#recall · ${pad(idx + 1)} / ${pad(ids.length)} ${filt ? "· " + esc(filt) : ""}</span>
      <span class="label"><span class="tag k-${c.kind}">${c.kindName}</span> ${c.isNew ? '<span class="tag blue">新</span>' : ""} ${c.topics.slice(0, 2).map(topicZh).join(" · ")}</span></div>
      <div class="progress"><i style="width:${(idx / ids.length) * 100}%"></i></div>`;

    if (c.type === "cloze") {
      let k = 0;
      const sentence = c.segments.map((s) => s.blank !== undefined
        ? `<input data-i="${k}" data-ans="${esc(c.answer[k++])}" style="width:${Math.max(3, s.blank.length + 1)}ch" autocomplete="off" autocapitalize="off" spellcheck="false">`
        : esc(s.t)).join("");
      main().innerHTML = `<div class="stage fade-in">${head}
        <span class="label">把挖掉的部分写出来</span>
        <div class="meaning-big">${esc(c.zh || "（没有中文释义）")}</div>
        <div class="cloze">${sentence}</div>
        <div class="hintline" id="hint"></div>
        <div class="ctrl">
          <button class="btn primary" id="check">检查 <span class="kbd" style="color:inherit;border-color:currentColor">↵</span></button>
          <button class="btn line small" id="h-zh">整句中文</button>
          <button class="btn line small" id="h-first">首字母</button>
          <button class="btn line small" id="h-peek">看答案</button>
          <span class="spacer"></span><button class="iconbtn" id="say" title="朗读整句">${ICON.speak}</button>
        </div>
        <div id="result"></div></div>`;
      const inputs = $$(".cloze input");
      inputs[0].focus();
      prefetchAudio(c.sentence);
      const showResult = (ok) => {
        st.done = true;
        inputs.forEach((inp) => { inp.disabled = true; });
        results.push({ id: c.id, ok });
        api("/api/recall/answer", { body: { id: c.id, mistakes: st.mistakes, peeked: st.peeked, hinted: st.hinted } }).catch(fail);
        $("#result").innerHTML = `<div class="result fade-in">
          <span class="label">${ok ? (st.mistakes ? "答对了（错过一次，记为模糊）" : st.hinted ? "答对了（用了提示，记为模糊）" : "一次答对") : "这次记为忘记，很快会再出现"}</span>
          <div class="form">${esc(c.form)}</div>
          <div class="serif" style="font-size:19px">${highlight(c.sentence, c.answer)}</div>
          ${c.sentenceZh ? `<div class="dim" style="font-size:14px">${esc(c.sentenceZh)}</div>` : ""}
          ${c.alts.length ? `<div class="alts mono dim" style="font-size:12px">≈ ${c.alts.map(esc).join(" / ")}</div>` : ""}
          ${c.note ? `<div class="muted" style="font-size:14px">${esc(c.note)}</div>` : ""}
        </div>`;
        $("#check").innerHTML = `下一个 <span class="kbd" style="color:inherit;border-color:currentColor">↵</span>`;
        $("#hint").textContent = "";
        $("#result").scrollIntoView({ behavior: "smooth", block: "nearest" });
        if (CFG.study.auto_speak !== false) speak(c.sentence);
      };
      const check = () => {
        if (st.done) { idx++; show(); return; }
        let all = true;
        inputs.forEach((inp) => {
          const good = normAns(inp.value) === normAns(inp.dataset.ans);
          inp.classList.toggle("ok", good);
          inp.classList.toggle("bad", !good);
          if (!good) all = false;
        });
        if (all) { showResult(true); return; }
        st.mistakes++;
        const maxTries = CFG.study.max_tries || 3;
        if (st.mistakes >= maxTries) {
          st.peeked = true;
          inputs.forEach((inp) => { inp.value = inp.dataset.ans; inp.classList.remove("bad"); inp.classList.add("ok"); });
          showResult(false);
        } else {
          $("#hint").textContent = `不对，还可以再试 ${maxTries - st.mistakes} 次`;
          const bad = inputs.find((i) => i.classList.contains("bad"));
          bad && bad.select();
        }
      };
      $("#check").onclick = check;
      $("#say").onclick = () => speak(c.sentence);
      $("#h-zh").onclick = () => { st.hinted = true; $("#hint").textContent = c.sentenceZh || "这句没有中文翻译"; };
      $("#h-first").onclick = () => { st.hinted = true; inputs.forEach((i) => { i.placeholder = i.dataset.ans.split(" ").map((w) => w[0] + "…").join(" "); }); };
      $("#h-peek").onclick = () => {
        if (st.done) return;
        st.peeked = true;
        inputs.forEach((inp) => { inp.value = inp.dataset.ans; inp.classList.add("ok"); });
        showResult(false);
      };
      window.onkeydown = (e) => {
        if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); check(); }
      };
    } else {
      // 论点素材：看中文想英文，自己打分
      main().innerHTML = `<div class="stage fade-in">${head}
        <span class="label">用英文说出这个${c.kind === "idea" ? "论点" : "表达"}</span>
        <div class="meaning-big" style="font-size:26px">${esc(c.zh || c.form)}</div>
        <textarea class="answer-box" placeholder="可以先写下来再看答案（不写也行）"></textarea>
        <div class="ctrl"><button class="btn primary" id="reveal">看答案 <span class="kbd" style="color:inherit;border-color:currentColor">↵</span></button></div>
        <div id="result"></div></div>`;
      $(".answer-box").focus();
      prefetchAudio(c.form);
      let revealed = false;
      const rate = (g) => {
        results.push({ id: c.id, ok: g === 3 });
        api("/api/recall/answer", { body: { id: c.id, grade: g } }).catch(fail);
        idx++; show();
      };
      const reveal = () => {
        if (revealed) return;
        revealed = true;
        $(".answer-box").blur();
        $(".answer-box").readOnly = true;
        $("#reveal").remove();
        $("#result").innerHTML = `<div class="result fade-in"><span class="label">参考表达</span>
          <div class="flash-answer">${esc(c.form)}</div>
          ${c.sentence && c.sentence !== c.form ? `<div class="serif dim" style="font-size:17px">${esc(c.sentence)}</div>` : ""}
          <div class="grade-btns"><button class="btn line" data-g="1">忘了 <span class="kbd">1</span></button><button class="btn line" data-g="2">模糊 <span class="kbd">2</span></button><button class="btn primary" data-g="3">记得 <span class="kbd" style="color:inherit;border-color:currentColor">3</span></button></div></div>`;
        $$("[data-g]").forEach((b) => b.onclick = () => rate(+b.dataset.g));
        if (CFG.study.auto_speak !== false) speak(c.form);
      };
      $("#reveal").onclick = reveal;
      window.onkeydown = (e) => {
        if (!revealed && e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); reveal(); }
        else if (revealed && ["1", "2", "3"].includes(e.key)) { e.preventDefault(); rate(+e.key); }
      };
    }
  };
  show();
}

// ---------- 改写升级 ----------

const rwState = { exclude: [] };

async function rewrite(_arg, params, alive) {
  const s = await api("/api/stats");
  if (!s.aiReady) {
    main().innerHTML = `<div class="done fade-in"><span class="label">#rewrite</span><h2 class="h2" style="margin:14px 0">Needs AI</h2>
      <p class="muted">改写练习要 AI 出题和批改：${esc(s.aiReason)}。</p><div class="ctrl" style="justify-content:center"><a class="btn primary" href="#/settings">去设置</a><a class="btn line" href="#/recall">先练回忆</a></div></div>`;
    return;
  }
  const fixedId = params.get("id");
  main().innerHTML = `<div class="stage">${loading("AI 正在出题…")}</div>`;
  let t;
  try {
    t = await api("/api/rewrite/next", { body: { topic: prState.topic, id: fixedId ? +fixedId : null, exclude: rwState.exclude.slice(-30) } });
  } catch (e) {
    if (alive()) main().innerHTML = `<div class="done"><p class="muted">${esc(e.message)}</p><a class="btn line" href="#/practice">返回</a></div>`;
    return;
  }
  if (!alive()) return;
  rwState.exclude.push(t.material.id);
  const m = t.material;
  main().innerHTML = `<div class="stage fade-in">
    <div class="practice-top"><span class="label">#rewrite ${prState.topic ? "· " + esc(topicZh(prState.topic)) : ""}</span>
      <span class="label"><span class="tag k-${m.kind}">${KIND[m.kind]}</span> ${m.topics.slice(0, 2).map(topicZh).join(" · ")}</span></div>
    <span class="label">用这个${KIND[m.kind]}</span>
    <div><div class="target"><b>${esc(m.form)}</b><span>${esc(m.zh)}</span></div></div>
    <span class="label">把这句话升级</span>
    <p class="plain">${esc(t.plain)}</p>
    <p class="dim" style="margin:0 0 22px">${esc(t.zh)}</p>
    <textarea class="answer-box" id="ans" placeholder="写出升级后的句子…"></textarea>
    <div class="ctrl"><button class="btn primary" id="sub">提交批改 <span class="kbd" style="color:inherit;border-color:currentColor">⌘↵</span></button>
      <button class="btn line small" id="skip">换一题</button><span class="spacer"></span>
      ${fixedId ? `<a class="btn line small" href="#/rewrite">随机出题</a>` : ""}</div>
    <div id="result"></div></div>`;
  $("#ans").focus();
  let checked = false;
  const next = () => { location.hash = `#/rewrite?r=${Date.now()}`; };
  $("#skip").onclick = next;
  const submit = async () => {
    if (checked) { next(); return; }
    const answer = $("#ans").value.trim();
    if (!answer) { toast("先写一句", true); return; }
    $("#sub").disabled = true; $("#sub").textContent = "批改中…";
    try {
      const r = await api("/api/rewrite/check", { body: { taskId: t.taskId, answer } });
      checked = true;
      $("#ans").disabled = true;
      $("#result").innerHTML = `<div class="result fade-in">
        <div style="display:flex;gap:18px;align-items:flex-end;flex-wrap:wrap"><span class="score">${r.score}<span class="dim" style="font-size:32px">/3</span></span>
          <span class="label" style="padding-bottom:10px">${r.used ? "用上了目标表达" : "没有用上目标表达"}</span></div>
        <div class="muted">${esc(r.comment)}</div>
        ${r.corrected && r.corrected !== answer ? `<div><span class="label">修改后</span><div class="serif" style="font-size:19px;margin-top:4px">${esc(r.corrected)}</div></div>` : ""}
        <div><span class="label">范句</span><div class="serif" style="font-size:19px;margin-top:4px">${esc(r.model)}</div></div>
      </div>`;
      $("#sub").disabled = false;
      $("#sub").innerHTML = `下一题 <span class="kbd" style="color:inherit;border-color:currentColor">⌘↵</span>`;
      speak(r.corrected || answer);
    } catch (e) { fail(e); $("#sub").disabled = false; $("#sub").textContent = "提交批改"; }
  };
  $("#sub").onclick = submit;
  window.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); } };
}

// ---------- 话题演练 ----------

function formWords(form) {
  return form.toLowerCase().replace(/[’]/g, "'").replace(/\.\.\.|…/g, " ").split(/[\s,]+/)
    .map((w) => w.replace(/[^a-z'-]/g, "")).filter((w) => w && !["sb", "sth", "one's", "oneself", "x", "y", "a", "an", "the", "of", "to", "in", "and"].includes(w));
}
function looksUsed(form, text) {
  const words = formWords(form);
  if (!words.length) return false;
  const low = " " + text.toLowerCase().replace(/[’]/g, "'") + " ";
  return words.every((w) => new RegExp("(^|[^a-z])" + w.slice(0, Math.max(3, w.length - 2)).replace(/[-']/g, "\\$&")).test(low));
}

function feedbackHtml(fb) {
  return `<div class="feedback">
    <div class="fb-block"><div style="display:flex;gap:20px;align-items:flex-end;flex-wrap:wrap"><span class="score">${fb.band}</span><span class="label" style="padding-bottom:10px">估计 band · ${fb.words || ""} 词</span></div>
      <p class="muted" style="margin:10px 0 0">${esc(fb.summary)}</p></div>
    ${fb.used && fb.used.length ? `<div class="fb-block"><h4>用上的素材 · ${fb.used.length}</h4>${fb.used.map((u) => `<div class="issue"><div><span class="tag ${u.ok ? "blue" : ""}">${u.ok ? "用得好" : "要改"}</span> <b>${esc(u.form || "")}</b></div><div class="serif">“${esc(u.quote)}”</div>${u.comment ? `<div class="dim" style="font-size:14px">${esc(u.comment)}</div>` : ""}</div>`).join("")}</div>` : ""}
    ${fb.alsoUsed && fb.alsoUsed.length ? `<div class="fb-block"><h4>还用到了库里的</h4><div class="chips">${fb.alsoUsed.map((m) => `<span class="tag k-${m.kind}">${esc(m.form)}</span>`).join("")}</div></div>` : ""}
    ${fb.issues && fb.issues.length ? `<div class="fb-block"><h4>主要问题 · ${fb.issues.length}</h4>${fb.issues.map((x) => `<div class="issue"><div><s>${esc(x.quote)}</s> → <ins>${esc(x.fix)}</ins></div><div class="dim" style="font-size:14px">${esc(x.why)}</div></div>`).join("")}</div>` : ""}
    ${fb.improved ? `<div class="fb-block"><h4>参考改写</h4><div class="improved">${esc(fb.improved)}</div></div>` : ""}
  </div>`;
}

async function drill(slug, params, alive) {
  if (!slug) {
    await loadTopics();
    main().innerHTML = `<div class="fade-in"><div class="section-head"><div><span class="label">#drill</span><h2 class="h2" style="margin-top:8px">Pick a Topic</h2></div></div>
      <div class="topics">${TOPICS.filter((t) => t.slug !== "general").map((t, i) => `<a class="topic" href="#/drill/${t.slug}">
        <div class="art"><canvas data-art="glyph" data-glyph="${esc(t.glyph)}" data-seed="${i + 1}"></canvas></div>
        <span class="label">#${pad(i + 1)} · ${esc(t.en)}</span><div class="h3">${esc(t.zh)}</div><p>${t.count} 条素材</p></a>`).join("")}</div></div>`;
    Art.paint(main());
    return;
  }
  const fresh = params.get("fresh") === "1";
  const d = await api(`/api/drill/start?topic=${encodeURIComponent(slug)}${fresh ? "&fresh=true" : ""}`);
  if (!alive()) return;
  const draftKey = `cw-draft-${slug}`;
  let draft = "";
  try { draft = localStorage.getItem(draftKey) || ""; } catch { /* 无痕模式 */ }
  main().innerHTML = `<div class="fade-in">
    <a class="label" href="#/drill" style="text-decoration:none">← 换话题</a>
    <div class="section-head" style="margin-top:14px"><div><span class="label">#drill · ${esc(d.topic.en)}</span><h2 class="h2" style="margin-top:8px">${esc(d.topic.zh)}</h2></div></div>
    ${d.aiReady ? "" : `<div class="notice">可以先写，但提交批改需要 AI：${esc(d.aiReason)}。<a href="#/settings">去设置</a></div>`}
    <div class="drill">
      <div>
        <div class="prompt-box"><span class="label">题目</span><div class="en" style="margin-top:8px">${esc(d.prompt.en)}</div><div class="dim" style="font-size:14px;margin-top:6px">${esc(d.prompt.zh)}</div>
          <div style="margin-top:12px"><button class="btn line small" id="another">换一题</button></div></div>
        <textarea class="answer-box" id="essay" placeholder="写一段（建议 120–250 词，至少 ${d.minWords} 词），试着用上右边的素材…">${esc(draft)}</textarea>
        <div class="ctrl"><button class="btn primary" id="sub" ${d.aiReady ? "" : "disabled"}>提交批改 <span class="kbd" style="color:inherit;border-color:currentColor">⌘↵</span></button><span class="label" id="wc"></span></div>
      </div>
      <aside class="sugg"><span class="label">可以用上的素材 · 写进去会亮起</span>
        ${d.suggestions.map((m) => `<div class="s" data-id="${m.id}" data-form="${esc(m.form)}" title="${esc(m.zh)}"><span class="f"><span class="tag k-${m.kind}" style="margin-right:6px">${KIND[m.kind].slice(0, 2)}</span>${esc(m.form)}</span><span class="z">${esc(m.kind === "idea" ? "" : m.zh)}</span></div>`).join("") || `<div class="empty">这个话题还没有素材</div>`}
      </aside>
    </div>
    <div id="fb"></div>
  </div>`;
  const update = () => {
    const text = $("#essay").value;
    const n = (text.match(/[A-Za-z'’-]+/g) || []).length;
    $("#wc").textContent = `${n} 词`;
    $$(".sugg .s").forEach((el) => el.classList.toggle("hit", looksUsed(el.dataset.form, text)));
    try { localStorage.setItem(draftKey, text); } catch { /* 无痕模式 */ }
  };
  $("#essay").oninput = update;
  update();
  // 题库里多道题时随机换；只有一道或想要新的，就让 AI 出
  $("#another").onclick = () => { location.hash = `#/drill/${slug}?${d.prompts.length > 1 && Math.random() < 0.6 ? "r=" + Date.now() : "fresh=1&r=" + Date.now()}`; };
  const submit = async () => {
    const text = $("#essay").value.trim();
    if ((text.match(/[A-Za-z]+/g) || []).length < d.minWords) { toast(`至少写 ${d.minWords} 个词再提交`, true); return; }
    $("#sub").disabled = true; $("#sub").textContent = "AI 批改中…";
    try {
      const fb = await api("/api/drill/check", { body: { topic: slug, prompt: d.prompt.en, text, ids: d.suggestions.map((m) => m.id) } });
      $("#fb").innerHTML = feedbackHtml(fb);
      try { localStorage.removeItem(draftKey); } catch { /* 无痕模式 */ }
      $("#fb").scrollIntoView({ behavior: "smooth", block: "start" });
      $("#sub").textContent = "已批改";
    } catch (e) { fail(e); $("#sub").disabled = false; $("#sub").textContent = "提交批改"; }
  };
  $("#sub").onclick = submit;
  window.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !$("#sub").disabled) { e.preventDefault(); submit(); } };
}

// ---------- 话题翻译 ----------

const LEN_NAME = { mix: "混合", short: "短句", medium: "中句", long: "长句" };
const trState = { length: "", exclude: [], excludeMaterials: [] };

// 按词对比，标出改动（删掉的划线，加上的下划线）
function wordDiff(a, b) {
  const A = a.split(/\s+/).filter(Boolean), B = b.split(/\s+/).filter(Boolean);
  const key = (w) => w.toLowerCase().replace(/[.,;:!?"“”]/g, "");
  const n = A.length, m = B.length;
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) {
    L[i][j] = key(A[i]) === key(B[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && key(A[i]) === key(B[j])) { out.push(esc(B[j])); i++; j++; }
    else if (i < n && (j >= m || L[i + 1][j] >= L[i][j + 1])) { out.push(`<del>${esc(A[i])}</del>`); i++; }  // 先划掉旧的
    else { out.push(`<ins>${esc(B[j])}</ins>`); j++; }  // 再给新的
  }
  return out.join(" ");
}

function translateFeedbackHtml(fb, answer) {
  const stars = (n) => `${n}<span class="dim" style="font-size:.5em">/5</span>`;
  return `<div class="feedback">
    <div class="fb-block">
      <div style="display:flex;gap:22px;align-items:flex-end;flex-wrap:wrap"><span class="score">${stars(fb.score)}</span>
        <div class="dims" style="padding-bottom:12px"><span>准确<b>${fb.accuracy}</b></span><span>地道<b>${fb.naturalness}</b></span></div></div>
      <p class="muted" style="margin:10px 0 0">${esc(fb.summary)}</p>
    </div>
    <div class="fb-block"><h4>你的译文 · 改正后</h4>
      <div class="diff">${fb.corrected && fb.corrected !== answer ? wordDiff(answer, fb.corrected) : esc(answer) + ' <span class="tag blue">不用改</span>'}</div></div>
    ${fb.better && fb.better.length ? `<div class="fb-block"><h4>更地道的写法</h4>${fb.better.map((b) => `<div class="better"><div class="serif">${esc(b)}</div>
      <button class="iconbtn" data-say="${esc(b)}" title="朗读">${ICON.speak}</button></div>`).join("")}</div>` : ""}
    ${fb.chunks && fb.chunks.length ? `<div class="fb-block"><h4>还可以这样说 · ${fb.chunks.length}</h4>${fb.chunks.map((c) => `<div class="chunk">
      <div class="yours">“${esc(c.yours)}”</div><div class="opts">${c.options.map((o) => `<span>${esc(o)}</span>`).join("")}</div>
      ${c.why ? `<div class="dim" style="font-size:14px">${esc(c.why)}</div>` : ""}</div>`).join("")}</div>` : ""}
    ${fb.good && fb.good.length ? `<div class="fb-block"><h4>写得好的地方</h4>${fb.good.map((g) => `<div class="issue"><div><ins>${esc(g.quote)}</ins></div><div class="dim" style="font-size:14px">${esc(g.why)}</div></div>`).join("")}</div>` : ""}
    ${fb.targetsUsed && fb.targetsUsed.length ? `<div class="fb-block"><h4>这道题考的库内素材</h4><div class="chips">${fb.targetsUsed.map((t) => `<span class="tag ${t.used && t.ok ? "blue" : ""}">${esc(t.form || "")} · ${t.used ? (t.ok ? "用对了" : "用了但不对") : "没用上"}</span>`).join("")}</div></div>` : ""}
    ${fb.expressions && fb.expressions.length ? `<div class="fb-block"><h4>值得收藏的表达</h4>${fb.expressions.map((e, i) => `<div class="expr">
      <div><span class="tag k-${e.kind}">${KIND[e.kind]}</span> <b>${esc(e.form)}</b> <span class="dim">${esc(e.zh)}</span>
      ${e.examples[0] ? `<div class="serif dim" style="font-size:15px;margin-top:4px">${highlight(e.examples[0].en, e.examples[0].answer)}</div>` : ""}</div>
      <button class="btn line small" data-save="${i}" ${e.saved ? "disabled" : ""}>${e.saved ? "已在库里" : "+ 加入素材库"}</button></div>`).join("")}</div>` : ""}
    ${fb.ref ? `<details class="fb-block"><summary class="label" style="cursor:pointer">出题时的参考译文</summary><div class="serif" style="font-size:18px;margin-top:10px">${esc(fb.ref)}</div></details>` : ""}
  </div>`;
}

function bindFeedback(root, fb) {
  root.addEventListener("click", async (e) => {
    const say = e.target.closest("[data-say]");
    if (say) { speak(say.dataset.say); return; }
    const b = e.target.closest("[data-save]");
    if (!b) return;
    const ex = fb.expressions[+b.dataset.save];
    try {
      const r = await api("/api/materials", { body: { source: "ai", items: [ex] } });
      b.disabled = true; b.textContent = r.added ? "已加入" : "已在库里";
    } catch (err) { fail(err); }
  });
}

async function translate(_arg, params, alive) {
  await loadTopics();
  const length = trState.length || CFG.translate.length || "mix";
  main().innerHTML = `<div class="stage">${loading("出题中…（AI 第一次出一批，约 15 秒）")}</div>`;
  let item;
  try {
    item = await api("/api/translate/next", { body: { topic: prState.topic, length, exclude: trState.exclude.slice(-50), excludeMaterials: trState.excludeMaterials.slice(-50) } });
  } catch (e) {
    if (alive()) main().innerHTML = `<div class="done"><p class="muted">${esc(e.message)}</p><a class="btn line" href="#/practice">返回</a></div>`;
    return;
  }
  if (!alive()) return;
  trState.exclude.push(item.id);
  item.targets.forEach((t) => trState.excludeMaterials.push(t.id));
  const hinted = { v: false };
  main().innerHTML = `<div class="stage fade-in">
    <div class="practice-top"><span class="label">#translate · ${esc(LEN_NAME[item.length] || "")} · ${esc(item.topicZh)}</span>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select class="field" id="ttopic" style="width:auto;padding:6px 10px">${topicOptions(prState.topic, "随机话题")}</select>
        <div class="chips len-chips">${Object.entries(LEN_NAME).map(([k, v]) => `<button class="chip ${k === length ? "on" : ""}" data-len="${k}">${v}</button>`).join("")}</div>
      </div></div>
    ${item.aiReady ? "" : `<div class="notice">还没配置 AI：题目来自素材库的例句，写完对照参考译文自己打分。配置 AI 后会出长句、地道表达的题，并给出详细批改。<a href="#/settings">去设置</a></div>`}
    <span class="label">把这段中文译成英文</span>
    <div class="zh-big ${item.length === "long" ? "long" : ""}">${esc(item.zh)}</div>
    ${item.hints.length ? `<div class="hints hidden" id="hints">${item.hints.map((h) => `<div><b>${esc(h.zh)}</b>${h.en.map(esc).join(" / ")}</div>`).join("")}</div>` : ""}
    <textarea class="answer-box" id="ans" placeholder="写出你的英文译文…" style="min-height:${item.length === "long" ? 160 : 110}px"></textarea>
    <div class="ctrl"><button class="btn primary" id="sub">${item.aiReady ? "提交批改" : "对照答案"} <span class="kbd" style="color:inherit;border-color:currentColor">⌘↵</span></button>
      ${item.hints.length ? `<button class="btn line small" id="hint">提示</button>` : ""}
      <button class="btn line small" id="skip">换一题</button><span class="spacer"></span></div>
    <div id="result"></div>
    <div class="section" style="margin-top:72px"><span class="label">最近的翻译</span><div id="hist" style="margin-top:12px"></div></div>
  </div>`;
  $("#ans").focus();
  const next = () => { location.hash = `#/translate?r=${Date.now()}`; };
  $("#skip").onclick = next;
  $("#ttopic").onchange = (e) => { prState.topic = e.target.value; next(); };
  $$("[data-len]").forEach((b) => b.onclick = () => { trState.length = b.dataset.len; next(); });
  if ($("#hint")) $("#hint").onclick = () => { hinted.v = true; $("#hints").classList.remove("hidden"); $("#hint").remove(); };
  let done = false;
  const submit = async () => {
    if (done) { next(); return; }
    const answer = $("#ans").value.trim();
    if ((answer.match(/[A-Za-z]+/g) || []).length < 3) { toast("先写出英文译文", true); return; }
    $("#sub").disabled = true; $("#sub").textContent = item.aiReady ? "AI 批改中…" : "…";
    try {
      const fb = await api("/api/translate/check", { body: { id: item.id, answer, hinted: hinted.v } });
      done = true;
      $("#ans").readOnly = true;
      if (fb.selfRate) {
        $("#result").innerHTML = `<div class="result fade-in"><span class="label">参考译文</span>
          <div class="serif" style="font-size:20px">${esc(fb.ref)}</div>
          ${fb.targets.map((t) => `<div><span class="tag ${t.used ? "blue" : ""}">${esc(t.form)}</span> <span class="dim">${esc(t.zh)} · ${t.used ? "你用上了" : "你没用上"}</span></div>`).join("")}
          <span class="label" style="margin-top:8px">和参考译文比，你觉得自己译得——</span>
          <div class="grade-btns"><button class="btn line" data-g="1">差得远 <span class="kbd">1</span></button><button class="btn line" data-g="2">意思对、不够地道 <span class="kbd">2</span></button><button class="btn primary" data-g="3">基本一样好 <span class="kbd" style="color:inherit;border-color:currentColor">3</span></button></div></div>`;
        const rate = async (g) => { await api("/api/translate/rate", { body: { id: item.id, grade: g } }).catch(fail); next(); };
        $$("[data-g]").forEach((b) => b.onclick = () => rate(+b.dataset.g));
        window.onkeydown = (e) => { if (["1", "2", "3"].includes(e.key)) rate(+e.key); };
        $("#sub").remove();
      } else {
        $("#result").innerHTML = translateFeedbackHtml(fb, answer);
        bindFeedback($("#result"), fb);
        $("#sub").disabled = false;
        $("#sub").innerHTML = `下一题 <span class="kbd" style="color:inherit;border-color:currentColor">⌘↵</span>`;
        if (CFG.study.auto_speak !== false && fb.better[0]) speak(fb.better[0]);
        if (fb.better[1]) prefetchAudio(fb.better[1]);
      }
      $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { fail(e); $("#sub").disabled = false; $("#sub").textContent = item.aiReady ? "提交批改" : "对照答案"; }
  };
  $("#sub").onclick = submit;
  window.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); } };

  const hist = await api("/api/translate/history").catch(() => []);
  if (!alive() || !$("#hist")) return;
  $("#hist").innerHTML = hist.length ? hist.map((h) => `<div class="docrow" style="grid-template-columns:64px minmax(0,1fr) auto">
      <span class="score" style="font-size:36px">${h.feedback.selfRate ? "–" : h.feedback.score}</span>
      <div style="min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(h.zh)}</div>
      <span class="label">${esc(topicZh(h.topic))} · ${new Date(h.created * 1000).toLocaleString()}</span></div>
      <button class="iconbtn" data-view="${h.id}" title="查看">${ICON.arrow}</button></div>`).join("") : `<div class="empty">还没有翻译记录</div>`;
  $("#hist").onclick = (e) => {
    const b = e.target.closest("[data-view]"); if (!b) return;
    const h = hist.find((x) => x.id === +b.dataset.view);
    modal(`<span class="label">${esc(topicZh(h.topic))} · ${new Date(h.created * 1000).toLocaleString()}</span>
      <div class="zh-big" style="font-size:20px">${esc(h.zh)}</div>
      ${h.feedback.selfRate ? `<div class="fb-block"><h4>你写的</h4><div class="serif" style="font-size:18px">${esc(h.answer)}</div></div>
        <div class="fb-block" style="margin-top:12px"><h4>参考译文</h4><div class="serif" style="font-size:18px">${esc(h.feedback.ref)}</div></div>`
        : translateFeedbackHtml(h.feedback, h.answer)}
      <div class="foot"><button class="btn line" data-cancel>关闭</button></div>`,
    (box, close) => { box.style.width = "min(820px,100%)"; box.querySelector("[data-cancel]").onclick = close; bindFeedback(box, h.feedback); });
  };
}

// ---------- 设置 ----------

const EXAM_NAME = { ielts: "雅思", toefl: "托福", kaoyan: "考研", cet6: "六级", cet4: "四级" };
const EXAM_BAND = { ielts: "7", toefl: "100", kaoyan: "80", cet6: "550", cet4: "500" };

function chipRow(name, options, current) {
  return `<div class="chips" data-chips="${name}">${options.map(([v, label]) => `<button class="chip ${String(v) === String(current) ? "on" : ""}" data-v="${v}">${label}</button>`).join("")}</div>`;
}
function selectRow(name, values, current, suffix = "") {
  return `<select class="field" data-sel="${name}" style="width:auto">${values.map((v) => `<option value="${v}" ${String(v) === String(current) ? "selected" : ""}>${v}${suffix}</option>`).join("")}</select>`;
}
function toggleRow(name, on, label) {
  return `<label class="check" style="text-transform:none;letter-spacing:0;font-family:var(--body);font-size:14px"><input type="checkbox" data-tog="${name}" ${on ? "checked" : ""}>${label}</label>`;
}
// "study.daily_new" → {study: {daily_new: v}}
function patchOf(path, value) {
  const [a, b] = path.split(".");
  return { [a]: { [b]: value } };
}

async function settings() {
  const [cfg, info, voices] = await Promise.all([api("/api/settings"), api("/api/info"), api("/api/voices")]);
  CFG = cfg;
  const a = cfg.ai, st = cfg.study;
  const prov = [["deepseek", "DeepSeek（默认，国内直连）"], ["anthropic", "Claude"], ["openai", "其他兼容接口"], ["none", "不用 AI"]];
  const row = (label, control, help = "") => `<div class="set-row"><span>${label}${help ? `<small>${help}</small>` : ""}</span><div>${control}</div></div>`;
  main().innerHTML = `<div class="fade-in">
    <div class="section-head"><div><span class="label">#settings · 改了就自动保存（AI 来源和 Key 要点“保存”）</span><h2 class="h2" style="margin-top:8px">Settings</h2></div></div>
    <div class="settings">
      <section><span class="label">AI 来源 · 拆文章、改写、演练、翻译、AI 扩充都用它</span>
        <div class="chips" id="prov">${prov.map(([k, v]) => `<button class="chip ${a.provider === k ? "on" : ""}" data-p="${k}">${v}</button>`).join("")}</div>
        <div data-for="deepseek" class="form-grid">
          <label><span class="label">DeepSeek API Key（platform.deepseek.com 申请）</span><input class="field" name="deepseek.key" value="${esc(a.deepseek.key)}" placeholder="sk-…"></label>
          <div class="two"><label><span class="label">模型</span><input class="field" name="deepseek.model" value="${esc(a.deepseek.model)}"></label>
          <label><span class="label">接口地址</span><input class="field" name="deepseek.base" value="${esc(a.deepseek.base)}"></label></div>
        </div>
        <div data-for="anthropic" class="form-grid">
          <label><span class="label">Claude API Key（国内需要代理）</span><input class="field" name="anthropic.key" value="${esc(a.anthropic.key)}" placeholder="sk-ant-…"></label>
          <label><span class="label">模型</span><input class="field" name="anthropic.model" value="${esc(a.anthropic.model)}"></label>
        </div>
        <div data-for="openai" class="form-grid">
          <label><span class="label">API Key</span><input class="field" name="openai.key" value="${esc(a.openai.key)}"></label>
          <div class="two"><label><span class="label">模型名</span><input class="field" name="openai.model" value="${esc(a.openai.model)}" placeholder="moonshot-v1-8k"></label>
          <label><span class="label">接口地址（填到 /v1）</span><input class="field" name="openai.base" value="${esc(a.openai.base)}" placeholder="https://api.moonshot.cn/v1"></label></div>
        </div>
        <div data-for="none"><p class="muted" style="margin:0">不用 AI 时：内置素材可以练回忆；翻译用素材例句出题、自己对照打分；导入文章只分句，划选文字手动加素材。</p></div>
        <div class="ctrl" style="margin-top:6px"><button class="btn primary" id="save">保存</button><button class="btn line" id="test">测试连接</button><span class="label" id="testmsg"></span></div>
      </section>

      <section><span class="label">目标与批改 · 所有 AI 出题和批改都会按这里调整难度和口径</span>
        ${row("目标考试", chipRow("exam.target", Object.entries(EXAM_NAME), cfg.exam.target))}
        ${row("目标分数", `<input class="field" data-text="exam.band" value="${esc(cfg.exam.band)}" placeholder="默认 ${EXAM_BAND[cfg.exam.target] || "7"}" style="width:140px">`, "留空用默认：雅思 7 / 托福 100 / 考研 80 / 六级 550 / 四级 500")}
        ${row("批改严格程度", chipRow("ai.strictness", [["lenient", "宽松鼓励"], ["standard", "标准"], ["strict", "严格"]], a.strictness))}
        ${row("点评语言", chipRow("ai.feedback_lang", [["zh", "中文"], ["en", "英文"]], a.feedback_lang), "英文点评也算一种输入练习")}
      </section>

      <section><span class="label">每日学习</span>
        ${row("每天新素材", selectRow("study.daily_new", [0, 5, 10, 15, 20, 30, 50, 100], st.daily_new, " 条"), "设成 0 就只复习")}
        ${row("每轮最多", selectRow("study.session_size", [10, 20, 30, 50, 100, 200], st.session_size, " 条"))}
        ${row("新素材顺序", chipRow("study.new_order", [["mine_first", "自己收集的先"], ["rotate", "按话题轮流"], ["random", "随机"]], st.new_order))}
        ${row("回忆练哪几类", Object.entries(KIND).map(([k, v]) => toggleRow(`kind:${k}`, (st.kinds || []).includes(k), v)).join("&emsp;"), "练习页选了具体类型时以那里为准")}
      </section>

      <section><span class="label">回忆</span>
        ${row("错几次给答案", selectRow("study.max_tries", [1, 2, 3, 4, 5, 6], st.max_tries, " 次"))}
        ${row("答完自动朗读", toggleRow("study.auto_speak", st.auto_speak !== false, "回忆、翻译答完后读一遍"))}
      </section>

      <section><span class="label">话题演练 · 话题翻译</span>
        ${row("演练建议素材", selectRow("drill.suggestions", [6, 8, 10, 12, 16, 20, 24], cfg.drill.suggestions, " 条"))}
        ${row("演练最少词数", selectRow("drill.min_words", [10, 20, 50, 80, 120, 150, 200], cfg.drill.min_words, " 词"))}
        ${row("翻译题默认长度", chipRow("translate.length", Object.entries(LEN_NAME), cfg.translate.length), "翻译页上也能临时切换")}
        ${row("AI 每次出几道", selectRow("translate.batch", [3, 6, 9, 12], cfg.translate.batch, " 道"), "出一批缓存起来，后面的题不用等")}
        ${row("翻译题提示", toggleRow("translate.show_hints", cfg.translate.show_hints !== false, "显示“提示”按钮（用了提示，考到的素材最多算模糊）"))}
      </section>

      <section><span class="label">朗读 · 和川听同一套微软语音（免费、不用 Key，连不上时自动改用电脑自带语音）</span>
        ${row("声音", `<select class="field" id="voice" style="width:auto">${voices.map((v) => `<option value="${esc(v.name)}" ${v.name === cfg.voice.name ? "selected" : ""}>${esc(v.label)}</option>`).join("")}
          <option value="browser" ${cfg.voice.name === "browser" ? "selected" : ""}>电脑自带语音（不联网）</option></select>`)}
        ${row("语速", `<input type="range" min="-30" max="30" step="5" id="rate" value="${cfg.voice.rate}"> <span class="mono" id="ratev">${cfg.voice.rate > 0 ? "+" : ""}${cfg.voice.rate}%</span>`, "川听默认 -5%")}
        ${row("", `<button class="btn line small" id="listen">${ICON.speak}试听</button>`)}
      </section>

      <section><span class="label">界面</span>
        ${row("字号", chipRow("ui.font", [["normal", "标准"], ["large", "大"]], cfg.ui.font))}
        ${row("动画", toggleRow("ui.motion", cfg.ui.motion !== false, "页面切换和弹窗的淡入动画"))}
        ${row("例句字体", toggleRow("ui.serif", cfg.ui.serif !== false, "例句用衬线体（关掉改用无衬线）"))}
        ${row("输入字体", chipRow("ui.input", [["sans", "系统字体（清楚）"], ["serif", "衬线"], ["mono", "等宽"]], cfg.ui.input || "sans"), "写答案、译文、作文时输入框里的字体")}
      </section>

      <section><span class="label">数据 · 存在这台电脑上</span>
        ${row("数据位置", `<span class="mono dim" style="font-size:12px;word-break:break-all">${esc(info.home)}</span>`)}
        <div class="ctrl" style="margin-top:0">
          <a class="btn line" href="/api/export/backup">${ICON.down}导出学习记录</a>
          <label class="btn line">导入学习记录<input type="file" id="imp" accept=".json" hidden></label>
          <a class="btn line" href="/api/export/wordbook">${ICON.down}导出到川词</a>
          <a class="btn line" href="/api/export/markdown?source=mine">${ICON.down}我的素材 Markdown</a>
        </div>
        <p class="muted" style="margin:0;font-size:14px">学习记录可以在另一台电脑的川文里导入，会合并、不覆盖。“导出到川词”只包含你自己收集的搭配和衔接。</p>
        <div class="ctrl" style="margin-top:4px">
          <button class="btn line small" data-mt="restore-builtin">恢复删掉的内置素材</button>
          <button class="btn line small" data-mt="clear-ai-cache">清除 AI 出题缓存</button>
          <button class="btn line small danger" data-mt="reset-progress">重置练习进度</button>
        </div>
        <p class="muted" style="margin:0;font-size:13px">改了目标考试或严格程度后，可以清除缓存，让改写题和翻译题按新设置重新出。重置进度会清空所有复习记录，素材和文章都保留。</p>
      </section>

      <section><span class="label">关于</span>
        ${row("版本", `<span class="mono">v${esc(info.version)}</span> <button class="btn line small" id="upd" style="margin-left:10px">检查更新</button> <span class="label" id="updmsg"></span>`)}
        ${row("项目主页", `<a href="${info.repo}" target="_blank" rel="noopener">${info.repo.replace("https://", "")}</a>`)}
        ${row("关闭川文", `<button class="btn line small" id="quit">关闭川文</button>`, "关闭后网页就不能用了，下次从“川文”重新打开")}
      </section>
    </div></div>`;

  const save = async (patch, quiet) => {
    try {
      CFG = await api("/api/settings", { body: patch });
      applyUi();
      if (!quiet) toast("已保存");
    } catch (e) { fail(e); }
  };

  // AI 来源
  let provider = a.provider;
  const showProv = () => $$("[data-for]").forEach((el) => el.classList.toggle("hidden", el.dataset.for !== provider));
  showProv();
  $("#prov").onclick = (e) => {
    const b = e.target.closest("[data-p]"); if (!b) return;
    provider = b.dataset.p;
    $$("#prov .chip").forEach((x) => x.classList.toggle("on", x === b));
    showProv();
  };
  const aiPatch = () => {
    const patch = { ai: { provider, deepseek: {}, anthropic: {}, openai: {} } };
    $$("input[name]").forEach((i) => { const [p, k] = i.name.split("."); patch.ai[p][k] = i.value.trim(); });
    return patch;
  };
  $("#save").onclick = () => save(aiPatch());
  $("#test").onclick = async () => {
    $("#testmsg").textContent = "测试中…";
    try {
      await save(aiPatch(), true);
      const r = await api("/api/settings/test", { method: "POST" });
      $("#testmsg").textContent = r.ok ? "连接正常" : r.error;
      if (r.ok) toast("AI 连接正常：" + r.sample.slice(0, 60)); else toast(r.error, true);
    } catch (e) { $("#testmsg").textContent = ""; fail(e); }
  };

  // 其余设置：改了就存
  $$("[data-chips]").forEach((box) => box.onclick = (e) => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    $$(".chip", box).forEach((x) => x.classList.toggle("on", x === b));
    save(patchOf(box.dataset.chips, b.dataset.v));
    if (box.dataset.chips === "exam.target") $("[data-text='exam.band']").placeholder = `默认 ${EXAM_BAND[b.dataset.v]}`;
  });
  $$("[data-sel]").forEach((el) => el.onchange = () => save(patchOf(el.dataset.sel, +el.value)));
  $$("[data-text]").forEach((el) => el.onchange = () => save(patchOf(el.dataset.text, el.value.trim())));
  $$("[data-tog]").forEach((el) => el.onchange = () => {
    if (el.dataset.tog.startsWith("kind:")) {
      const kinds = $$("[data-tog^='kind:']").filter((x) => x.checked).map((x) => x.dataset.tog.slice(5));
      if (!kinds.length) { el.checked = true; toast("至少留一类", true); return; }
      save({ study: { kinds } });
    } else save(patchOf(el.dataset.tog, el.checked));
  });

  // 朗读
  const sample = "There is no denying that a good habit, once formed, pays off in the long run.";
  $("#voice").onchange = (e) => { save({ voice: { name: e.target.value } }); speak(sample, { voice: e.target.value, rate: +$("#rate").value }); };
  $("#rate").oninput = (e) => { const v = +e.target.value; $("#ratev").textContent = `${v > 0 ? "+" : ""}${v}%`; };
  $("#rate").onchange = (e) => save({ voice: { rate: +e.target.value } });
  $("#listen").onclick = () => speak(sample, { voice: $("#voice").value, rate: +$("#rate").value });

  // 数据维护
  const MT = {
    "restore-builtin": ["把删掉的内置素材加回来？", (n) => `恢复了 ${n} 条`],
    "clear-ai-cache": ["清除还没用过的改写题和翻译题？之后会按现在的设置重新出。", (n) => `清除了 ${n} 道题`],
    "reset-progress": ["重置所有练习进度？所有素材都会变回“新”，复习记录清空。这一步不能撤销，建议先“导出学习记录”备份。", (n) => `已重置 ${n} 条素材的进度`],
  };
  $$("[data-mt]").forEach((b) => b.onclick = async () => {
    const [q, done] = MT[b.dataset.mt];
    if (!confirm(q)) return;
    if (b.dataset.mt === "reset-progress" && !confirm("再确认一次：真的要清空所有复习进度吗？")) return;
    try {
      const r = await api(`/api/maintenance/${b.dataset.mt}`, { method: "POST", headers: { "X-ChuanWen": "1" } });
      toast(done(r.n));
      if (b.dataset.mt === "restore-builtin") await loadTopics();
    } catch (e) { fail(e); }
  });
  $("#imp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/import/backup", { form: fd }); toast(`导入完成：新增 ${r.added} 条，更新进度 ${r.updated} 条`); } catch (err) { fail(err); }
  };

  // 关于
  $("#upd").onclick = async () => {
    $("#updmsg").textContent = "检查中…";
    try {
      const r = await fetch("https://api.github.com/repos/georageti/chuanwen/releases/latest");
      const j = await r.json();
      const latest = String(j.tag_name || "").replace(/^v/, "");
      const newer = latest && latest.localeCompare(info.version, undefined, { numeric: true }) > 0;
      $("#updmsg").innerHTML = newer ? `有新版 v${esc(latest)}：<a href="${esc(j.html_url)}" target="_blank" rel="noopener">去下载</a>（下载后解压，重新双击“安装川文”）` : "已经是最新版";
    } catch { $("#updmsg").textContent = "连不上 GitHub，稍后再试"; }
  };
  $("#quit").onclick = async () => {
    if (!confirm("关闭川文？关闭后这个网页就不能用了，下次从“川文”重新打开。")) return;
    await api("/api/quit", { method: "POST", headers: { "X-ChuanWen": "1" } }).catch(fail);
    main().innerHTML = `<div class="done"><h2 class="h2">See You</h2><p class="muted">川文已关闭，可以关掉这个页面了。</p></div>`;
  };
}

render();
