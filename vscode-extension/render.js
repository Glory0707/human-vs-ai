/* human-vs-ai 报告渲染共享层——网页版（template.html）与 VS Code 扩展
 * （extension.js）共用的叶子函数。只放两端逐字一致的东西：转义、
 * 命中高亮、指数印章、弱命中块、共用常量；报告的组装结构（聚合、排序、
 * 布局）由各端自定。构建：build_web.py 注入网页，build_vscode.py 复制
 * 给扩展——与 engine.js 同一纪律，不许两端各自演化。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HvARender = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var SEV_NAME = { high: "高", medium: "中", low: "低" };
  var SCORE_LABEL = { hit_density: "规则", sentence_cv: "节奏", ttr: "词汇", ngram_repeat: "重复", conn_density: "连接词" };
  var PROFILE_META = {
    academic: ["学术", "论文、摘要、实验报告"],
    general: ["问答", "知乎、公众号、科普"],
    official: ["公文", "通知、意见、实施方案"],
    personal: ["我的口味", "界面文案、标题、提示语"],
    news: ["新闻", "新闻报道、资讯、通稿"],
    essay: ["作文", "高考作文、议论文、考场写作"],
    review: ["短评", "影评、书评、商品点评"],
  };
  /* 弱命中只是参考信息，长文里全量列出会淹没正文发现（与 CLI 同口径） */
  var HINTS_MAX = 12;
  var DISCLAIMER = "风格提示，不是 AI 判定。";
  var ADVICE_FOOTER = "重要数据和结论要保留；梗得人来补。";

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmt(v) {
    return typeof v !== "number" || isNaN(v) ? "—" : v.toFixed(2);
  }

  /* 原句命中区间高亮：按索引切原文再统一转义。先转义后拼串会被
     "mark" 这类英文命中词撞上已插入的标签，按区间切没有这个问题 */
  function hiSentence(sent, matches) {
    const spans = [];
    (matches || []).forEach(m => {
      if (!m) return;
      let from = 0, idx;
      while ((idx = sent.indexOf(m, from)) >= 0) {
        spans.push([idx, idx + m.length]);
        from = idx + m.length;
      }
    });
    if (!spans.length) return esc(sent);
    spans.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const merged = [];
    let last = spans[0].slice();
    for (let i = 1; i < spans.length; i++) {
      if (spans[i][0] <= last[1]) last[1] = Math.max(last[1], spans[i][1]);
      else { merged.push(last); last = spans[i].slice(); }
    }
    merged.push(last);
    let out = "", pos = 0;
    merged.forEach(r => {
      out += esc(sent.slice(pos, r[0])) + "<mark>" + esc(sent.slice(r[0], r[1])) + "</mark>";
      pos = r[1];
    });
    return out + esc(sent.slice(pos));
  }

  /* 构成列："规则 +12 · 节奏 −5"（贡献永远可拆解，不许当黑盒） */
  function componentsText(components) {
    return Object.keys(components).map(f => {
      const v = components[f];
      return `${SCORE_LABEL[f] || f} ${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}`;
    }).join(" · ");
  }

  /* 指数印章：分档颜色锚定校准语料的真人分位（>p90 高 / >p50 中 / 其余低）。
     够 8 句却没出分（无校准语料）的场景由各端用 scoreNoteRow 给一行原因 */
  function scoreNoteRow(note) {
    if (!note) return "";
    return `<div class="row score">AI 味指数 <span class="comp">—（${note}）</span></div>`;
  }

  /* 指数印章（web/Obsidian/VS Code 三端同款）：mono + 大字距 + 档位色 + 斜放 */
  function sealHtml(score) {
    if (!score) return "";
    const idx = score.index.toFixed(0);
    const band = score.index > score.human_p90 ? "high" : score.index > score.human_p50 ? "medium" : "low";
    return `<div class="row score">` +
      `<span class="seal ${band}"><span class="n">${idx}</span><span class="u">AI味指数</span></span>` +
      `<span class="score-main"><span class="t">${idx} / 100</span>` +
      `<span class="sub">真人 p50≈<span class="mono-num">${score.human_p50}</span> / p90≈<span class="mono-num">${score.human_p90}</span> · 构成：${componentsText(score.components)}</span></span></div>`;
  }

  function hintsHtml(hints) {
    if (!hints || !hints.length) return "";
    const shown = hints.slice(0, HINTS_MAX);
    const more = hints.length - shown.length;
    /* 折叠为 details：弱命中只是参考信息，默认收起不淹没正文发现 */
    return `<details class="hints"><summary class="t">另有 ${hints.length} 处弱命中</summary>` +
      shown.map(h => `<div class="h">· ${esc(h.rule_id)} ${esc(h.rule_name)}（¶${h.para + 1}）</div>`).join("") +
      (more ? `<div class="h">…等 ${more} 处</div>` : "") +
      `</details>`;
  }

  return {
    esc: esc, fmt: fmt, hiSentence: hiSentence,
    componentsText: componentsText, sealHtml: sealHtml, scoreNoteRow: scoreNoteRow,
    hintsHtml: hintsHtml,
    SEV_NAME: SEV_NAME, SCORE_LABEL: SCORE_LABEL, PROFILE_META: PROFILE_META,
    HINTS_MAX: HINTS_MAX, DISCLAIMER: DISCLAIMER, ADVICE_FOOTER: ADVICE_FOOTER,
  };
});
