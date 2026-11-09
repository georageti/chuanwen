/* 程序生成的“蓝色版画”：网点 / 扫描线把一个汉字刻成图案，配合放射线和同心圆。
   用法：<canvas data-art="glyph" data-glyph="科" data-seed="3"></canvas>，然后 Art.paint(容器)。 */
(function () {
  const INK = "242,242,242";

  function rng(seed) {
    let a = (seed >>> 0) || 1;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hash(s) {
    let h = 2166136261;
    for (const c of String(s)) h = Math.imul(h ^ c.codePointAt(0), 16777619);
    return h >>> 0;
  }

  // 把字画到离屏画布，返回取样函数 (x, y) → 0..1
  function glyphMask(glyph, w, h, scale) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#fff";
    g.textAlign = "center"; g.textBaseline = "middle";
    const size = Math.min(w, h) * scale;
    g.font = `600 ${size}px "PingFang SC", "Hiragino Sans GB", "Songti SC", "Microsoft YaHei", sans-serif`;
    g.fillText(glyph, w / 2, h / 2 + size * 0.04);
    // 轻微模糊，网点边缘更柔和
    const blurred = document.createElement("canvas");
    blurred.width = w; blurred.height = h;
    const b = blurred.getContext("2d");
    b.filter = `blur(${Math.max(1, size / 90)}px)`;
    b.drawImage(c, 0, 0);
    const data = b.getImageData(0, 0, w, h).data;
    return (x, y) => {
      const xi = Math.max(0, Math.min(w - 1, x | 0)), yi = Math.max(0, Math.min(h - 1, y | 0));
      return data[(yi * w + xi) * 4] / 255;
    };
  }

  function setup(canvas) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(10, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(10, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h, dpr };
  }

  // 蓝底 + 扫描线网点刻出的字（话题卡片、练习模式卡片）
  function glyph(canvas) {
    const { ctx, w, h, dpr } = setup(canvas);
    const ch = canvas.dataset.glyph || "文";
    const r = rng(hash(ch) + Number(canvas.dataset.seed || 0));
    ctx.fillStyle = "#0000f2"; ctx.fillRect(0, 0, w, h);
    const mask = glyphMask(ch, w, h, 0.76);
    const cx = w * (0.35 + r() * 0.3), cy = h * (0.35 + r() * 0.3);
    const freq = 0.025 + r() * 0.03;
    const step = Math.max(3, Math.round(3.2 * dpr));
    const variant = Math.floor(r() * 3);
    for (let y = 0; y < h; y += step) {
      for (let x = 0; x < w; x += 2 * dpr) {
        const m = mask(x, y);
        const d = Math.hypot(x - cx, y - cy) / dpr;
        let field;
        if (variant === 0) field = 0.5 + 0.5 * Math.sin(d * freq * 6);           // 同心圆
        else if (variant === 1) field = 0.5 + 0.5 * Math.sin(Math.atan2(y - cy, x - cx) * 18); // 放射
        else field = 0.5 + 0.5 * Math.sin((x + y * 0.6) / dpr * freq * 4);       // 斜纹
        const v = Math.max(m * (0.5 + 0.45 * field), field * 0.2 + (r() - 0.5) * 0.06);  // 字里也带纹理，像刻出来的
        if (v < 0.05) continue;
        const th = Math.min(step - 0.5, v * step * 0.95);
        ctx.fillStyle = `rgba(${INK},${0.25 + v * 0.65})`;
        ctx.fillRect(x, y + (step - th) / 2, 2 * dpr - 0.4, th);
      }
    }
    // 细边角标
    ctx.strokeStyle = `rgba(${INK},.5)`; ctx.lineWidth = dpr;
    const k = 10 * dpr, p = 10 * dpr;
    [[p, p, 1, 1], [w - p, p, -1, 1], [p, h - p, 1, -1], [w - p, h - p, -1, -1]].forEach(([x, y, sx, sy]) => {
      ctx.beginPath(); ctx.moveTo(x, y + k * sy); ctx.lineTo(x, y); ctx.lineTo(x + k * sx, y); ctx.stroke();
    });
  }

  // 首页大图：放射线 + 方框 + 网点字
  function hero(canvas) {
    const { ctx, w, h, dpr } = setup(canvas);
    const ch = canvas.dataset.glyph || "文";
    const r = rng(7);
    const cx = w / 2, cy = h * 0.48;
    const R = Math.min(w, h) * 0.5;
    // 放射线（像版画里拉扯的绳索）
    for (let i = 0; i < 220; i++) {
      const a = r() * Math.PI * 2;
      const r0 = R * (0.18 + r() * 0.2), r1 = R * (0.75 + r() * 0.55);
      ctx.strokeStyle = `rgba(${INK},${0.12 + r() * 0.4})`;
      ctx.lineWidth = (0.4 + r() * 0.9) * dpr;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
      ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.stroke();
    }
    // 外圈刻度
    for (let i = 0; i < 180; i++) {
      const a = (i / 180) * Math.PI * 2;
      const len = i % 5 === 0 ? 16 : 7;
      ctx.strokeStyle = `rgba(${INK},${i % 5 === 0 ? 0.7 : 0.35})`;
      ctx.lineWidth = dpr * 0.8;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * R * 0.62, cy + Math.sin(a) * R * 0.62);
      ctx.lineTo(cx + Math.cos(a) * (R * 0.62 + len * dpr), cy + Math.sin(a) * (R * 0.62 + len * dpr));
      ctx.stroke();
    }
    // 方框
    const box = R * 0.95;
    ctx.strokeStyle = `rgba(${INK},.35)`; ctx.lineWidth = dpr;
    ctx.strokeRect(cx - box / 2, cy - box / 2, box, box);
    // 网点字：中心一块深色底，网点越亮越大
    ctx.fillStyle = "#19192f";
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.5, 0, Math.PI * 2); ctx.fill();
    const size = Math.round(R * 1.0);
    const mask = glyphMask(ch, size, size, 0.92);
    const ox = cx - size / 2, oy = cy - size / 2;
    const step = Math.max(4, Math.round(5 * dpr));
    for (let y = 0; y < size; y += step) {
      for (let x = (y / step) % 2 ? step / 2 : 0; x < size; x += step) {
        const d = Math.hypot(x - size / 2, y - size / 2) / (size / 2);
        if (d > 1) continue;
        const m = mask(x, y);
        const field = 0.5 + 0.5 * Math.sin(d * 34);
        const v = Math.max(m, field * 0.16 * (1 - d));
        if (v < 0.06) continue;
        ctx.fillStyle = m > 0.3 ? `rgba(${INK},${0.5 + m * 0.5})` : `rgba(80,80,255,${0.5 + v})`;
        ctx.beginPath();
        ctx.arc(ox + x, oy + y, (step / 2) * Math.min(1, v * 1.15), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    // 蓝色光晕
    const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 0.9);
    g.addColorStop(0, "rgba(0,0,242,0.20)"); g.addColorStop(1, "rgba(0,0,242,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
  }

  const painters = { glyph, hero };

  function paint(root) {
    (root || document).querySelectorAll("canvas[data-art]").forEach((c) => {
      const fn = painters[c.dataset.art];
      if (fn && c.clientWidth) fn(c);
    });
  }

  let timer;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => paint(document), 200);
  });
  document.fonts && document.fonts.ready.then(() => paint(document));

  window.Art = { paint };
})();
