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

  /* 构成列 HTML 版（指数印章 sub 行专用）：每项 nowrap，窄屏换行
     不拆"标签 数值"；纯文本版 componentsText 仍服务 Markdown 出口 */
  function compsHtml(components) {
    return Object.keys(components).map(f => {
      const v = components[f];
      return `<span class="ci">${SCORE_LABEL[f] || f} ${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)}</span>`;
    }).join(`<span class="ci-sep"> · </span>`);
  }

  /* 指数印章：分档颜色锚定校准语料的真人分位（>p90 高 / >p50 中 / 其余低）。
     够 8 句却没出分（无校准语料）的场景由各端用 scoreNoteRow 给一行原因 */
  function scoreNoteRow(note) {
    if (!note) return "";
    /* 虚线印章"—"：与指数印章同一形状语言，说明"这个位置本该有一个数"。
       被抑制（文种域外）与未校准（profile 无 scoring 段）两种情形共用 */
    return `<div class="row score"><span class="seal none" aria-hidden="true"><span class="n">—</span></span>` +
      `<span class="score-main"><span class="t">AI 味指数未出</span><span class="sub">${esc(note)}</span></span></div>`;
  }

  /* 指数印章（web/Obsidian/VS Code 三端同款）：mono + 大字距 + 档位色 + 斜放。
     分档读数直接说人话（"超过 90% 校准真人"），p50/p90 数字放悬浮提示 */
  function sealHtml(score) {
    if (!score) return "";
    const idx = score.index.toFixed(0);
    const band = score.index > score.human_p90 ? "high" : score.index > score.human_p50 ? "medium" : "low";
    const bandText = band === "high" ? "超过 90% 校准真人"
      : band === "medium" ? "超过半数校准真人" : "低于半数校准真人";
    return `<div class="row score">` +
      `<span class="seal ${band}"><span class="n">${idx}</span><span class="u">AI味指数</span></span>` +
      `<span class="score-main"><span class="t">${idx} / 100</span>` +
      `<span class="sub" title="校准语料真人分数：p50≈${score.human_p50}，p90≈${score.human_p90}">${bandText} · 构成：${compsHtml(score.components)}</span></span></div>`;
  }

  /* 域外提示：与 Python report._ood_lines 同构。按"文体/文种"两族分行——
     文言/诗行是形状超出语料域，印发/批复是文种超出系数校准域，提示语不同 */
  var OOD_NAME = {
    classical: "文言",
    verse: "等长对句诗行",
    "issuance-notice": "印发类",
    "approval-reply": "批复类",
  };
  var OOD_KIND = { classical: "文体", verse: "文体", "issuance-notice": "文种", "approval-reply": "文种" };
  var OOD_WHY = { "文体": "指数仅供参考", "文种": "本篇仅供参考" };
  function oodLines(ood) {
    if (!ood || !ood.length) return [];
    const groups = {};
    ood.forEach(k => {
      const kind = OOD_KIND[k] || "文体";
      (groups[kind] = groups[kind] || []).push(OOD_NAME[k] || k);
    });
    return ["文体", "文种"]
      .filter(kind => groups[kind])
      .map(kind => `${kind}域外（${groups[kind].join("、")}）：${OOD_WHY[kind]}`);
  }
  function oodHtml(ood) {
    /* ※ 前缀由 CSS .ood-note::before 补，HTML 里写死会显示两个 */
    return oodLines(ood).map(t => `<div class="row ood-note">${esc(t)}</div>`).join("");
  }

  /* 段落热度：混写文本里全篇一个分数必然失真，指出"哪几段最像 AI"。
     只列前 3 段（按密度降序，引擎已排）；无命中的段不出现。
     项上带 data-para/data-excerpt，交互端可监听点击在原稿中定位该段 */
  function paraHeatHtml(result) {
    const heat = ((result && result.para_heat) || []).slice(0, 3);
    if (!heat.length) return "";
    const items = heat.map(h =>
      `<span class="ph ph-${esc(h.level)}" data-para="${h.para}"` +
      ` data-excerpt="${esc(h.excerpt || "")}" role="button" title="点击定位原稿">¶${h.para + 1} <b class="mono-num">${h.density.toFixed(2)}</b></span>`
    ).join('<span class="ph-sep"> · </span>');
    return `<div class="row heat-note">段落热度：${items}</div>`;
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

  /* ---------- 报告组装与 Markdown 导出（web 与 Obsidian 共用；VS Code 的
     HTML 视图按严重级分组不走这里）。与 CLI render_markdown 同一份内容，
     改动任一拷贝前先看另外两处——一致性探针与冒烟测试都在盯着 ---------- */

  /* 同句多规则聚组（与 CLI _group_by_sentence 同口径），返回视图模型供
     屏幕渲染与 Markdown 导出共用——一份事实，两种出口 */
  function buildGroups(F) {
    const groups = [];
    const byKey = new Map();
    const docLevel = [];
    F.forEach(f => {
      if (f.para < 0) { docLevel.push(f); return; }
      const key = f.para + "\u0000" + f.sentence;
      if (!byKey.has(key)) {
        const g = { para: f.para, sentence: f.sentence, items: [] };
        byKey.set(key, g);
        groups.push(g);
      }
      byKey.get(key).items.push(f);
    });
    const sevRank = { high: 0, medium: 1, low: 2 };
    groups.sort((a, b) => {
      const ra = Math.min(...a.items.map(i => sevRank[i.severity]));
      const rb = Math.min(...b.items.map(i => sevRank[i.severity]));
      return (ra - rb) || (a.para - b.para);
    });
    docLevel.sort((a, b) => sevRank[a.severity] - sevRank[b.severity]);
    return { groups, docLevel, sevRank };
  }

  function statsRows(s) {
    const rows = [`规模：${s.n_paragraphs} 段 · ${s.n_sentences} 句 · ${s.n_chars} 字`];
    // 统计行只在样本够判定时展示（与 doc 规则的 min_sentences=8 同口径）：
    // 一两句话的文本里 CV 全是"—"、TTR 恒为 1，展示出来全是噪音
    if (s.n_sentences < 8) return rows;
    rows.push(`节奏：句长 CV ${fmt(s.sentence_cv)} · 段长 CV ${fmt(s.para_len_cv)}`);
    rows.push(`词汇：TTR ${fmt(s.ttr)} · 连接词密度 ${fmt(s.conn_density)}${s.conn_density === s.conn_density ? " 条/句" : ""} · 4-gram 重复率 ${fmt(s.ngram_repeat)}`);
    return rows;
  }

  /* 报告 → Markdown。label 由调用方给（web 用中文场景名，Obsidian 用
     profile id，保持各自既有输出）；域外提示行必须随行——caveat 复制
     出去不能丢。引擎字段是 score_note（v0.20.0 前误写 scoring_note，
     说明行在导出里从不出现——冒烟测试抓出的教训） */
  function reportToMarkdown(label, result) {
    const r = result;
    if (!r) return "";
    const L = [`# human-vs-ai 分析报告（${label}）`, "", "## 全文统计", ""];
    if (r.score) {
      L.push(`- AI 味指数：${r.score.index.toFixed(0)} / 100（风格分，不是 AI 概率）`);
      const comps = componentsText(r.score.components);
      if (comps) L.push(`- 构成：${comps}`);
    } else if (r.score_note) {
      L.push(`- AI 味指数：—（${r.score_note}）`);
    }
    oodLines(r.ood).forEach(t => L.push(`- ※ ${t}`));
    statsRows(r.stats).forEach(row => L.push(`- ${row}`));
    L.push("", `## 发现（${r.findings.length} 处）`, "");
    if (!r.findings.length) L.push("未发现模板化写作");
    const explained = new Set();
    const { groups, docLevel, sevRank } = buildGroups(r.findings);
    groups.forEach(g => {
      const top = g.items.reduce((acc, i) =>
        (sevRank[i.severity] < sevRank[acc.severity] ? i : acc), g.items[0]);
      // 重复句折叠后同一规则会出现几十次——标题去重（matches 本就已去重）
      const ids = [...new Set(g.items.map(i => i.rule_id))].join(" + ");
      const names = [...new Set(g.items.map(i => i.rule_name))].join(" + ");
      const matchArr = [...new Set(g.items.flatMap(i => i.matches))];
      L.push(`### [${SEV_NAME[top.severity]}] ${ids} ${names} · ¶${g.para + 1}`, "");
      if (g.sentence) L.push(`> ${g.sentence}`, "");
      if (matchArr.length) L.push(`**命中**：${matchArr.join("、")}`, "");
      const why = g.items.find(i => !explained.has(i.rule_id));
      g.items.forEach(i => explained.add(i.rule_id));
      if (why) {
        L.push(why.explanation.trim());
        if (why.suggestion) L.push("", `**建议**：${why.suggestion.trim()}`);
        L.push("");
      }
    });
    docLevel.forEach(f => {
      const why = !explained.has(f.rule_id);
      explained.add(f.rule_id);
      L.push(`### [${SEV_NAME[f.severity]}] ${f.rule_id} ${f.rule_name}${f.taste ? ` · ${f.taste}` : ""}（全文）`, "");
      if (f.matches.length) L.push(`**命中**：${[...new Set(f.matches)].join("、")}`, "");
      if (why) {
        L.push(f.explanation.trim());
        if (f.suggestion) L.push("", `**建议**：${f.suggestion.trim()}`);
        L.push("");
      }
    });
    if (r.hints && r.hints.length) {
      L.push(`## 弱命中（共 ${r.hints.length} 处）`, "");
      r.hints.slice(0, HINTS_MAX).forEach(h =>
        L.push(`- ${h.rule_id} ${h.rule_name}（¶${h.para + 1}）`));
      if (r.hints.length > HINTS_MAX) L.push(`- …等 ${r.hints.length} 处`);
      L.push("");
    }
    L.push("---", "", DISCLAIMER);
    return L.join("\n");
  }

  function adviceToMarkdown(adviceResult) {
    const A = adviceResult ? adviceResult.advices : null;
    if (!A) return "";
    const n = k => A.filter(a => a.action === k).length;
    const L = ["# human-vs-ai 改写建议（我的口味）", "",
      `共 ${A.length} 条 · 删 ${n("删")} · 改 ${n("改")} · 保留 ${n("保留")}`, ""];
    A.forEach(a => {
      L.push(`[${a.action}]${a.taste.length ? " " + a.taste.join("/") : ""} ${a.text}`);
      if (a.reason) L.push(`  ${a.reason}`);
      if (a.candidate) L.push(`  → ${a.candidate}`);
      else if (a.direction) L.push(`  → ${a.direction}`);
      L.push("");
    });
    L.push("---", "", ADVICE_FOOTER);
    return L.join("\n");
  }

  return {
    esc: esc, fmt: fmt, hiSentence: hiSentence,
    componentsText: componentsText,
    sealHtml: sealHtml, scoreNoteRow: scoreNoteRow,
    oodHtml: oodHtml, oodLines: oodLines, paraHeatHtml: paraHeatHtml,
    hintsHtml: hintsHtml,
    buildGroups: buildGroups, statsRows: statsRows,
    reportToMarkdown: reportToMarkdown, adviceToMarkdown: adviceToMarkdown,
    SEV_NAME: SEV_NAME, PROFILE_META: PROFILE_META,
    HINTS_MAX: HINTS_MAX, DISCLAIMER: DISCLAIMER, ADVICE_FOOTER: ADVICE_FOOTER,
  };
});
