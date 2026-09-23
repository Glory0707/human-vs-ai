/* human-vs-ai Obsidian 插件：当前笔记一键分析 + 侧边视图报告 + 改写建议。
 *
 * 与 CLI/网页/VS Code 扩展的关系：引擎直接复用 web/engine.js（与 Python
 * 引擎由 tools/check_web_consistency.py 守护逐字段一致），规则 JSON 由
 * tools/build_obsidian.py 从同一 YAML 源注入——四端同一事实源，本文件
 * 只承载 Obsidian 宿主逻辑（视图/命令/设置），渲染叶子复用 render.js。
 *
 * 纯本地：无任何网络调用。
 * UMD 三模块用局部 module 包装加载（构建时整段注入），obsidian 模块由
 * makePlugin(obsidian) 注入——报告组装与 Markdown 导出可被 node 冒烟
 * 测试直接加载（smoke-test.js），不为可测性引入构建步骤之外的魔法。
 */
const HvA = (function () {
  const module = { exports: {} };
  __ENGINE__
  return module.exports;
})();
const HvARewrite = (function () {
  const module = { exports: {} };
  __REWRITE__
  return module.exports;
})();
const HvARender = (function () {
  const module = { exports: {} };
  __RENDER__
  return module.exports;
})();
const RULES = __RULES_JSON__;
let SCORING = {};
try { SCORING = __SCORING_JSON__; } catch (e) { SCORING = {}; }
const VERSION = "__VERSION__";
const PROFILES = Object.keys(RULES);

/* 渲染共享层（web/render.js）：转义/高亮/评分行/常量 */
const { esc, fmt, hiSentence, sealHtml, oodHtml, paraHeatHtml, hintsHtml,
        componentsText, SEV_NAME, PROFILE_META,
        HINTS_MAX, DISCLAIMER, ADVICE_FOOTER } = HvARender;

/* ================= 报告组装（结构与 CLI/网页/VS Code 同一份内容） ================= */

function statsRows(s) {
  const rows = [`规模：${s.n_paragraphs} 段 · ${s.n_sentences} 句 · ${s.n_chars} 字`];
  if (s.n_sentences < 8) return rows;
  rows.push(`节奏：句长 CV ${fmt(s.sentence_cv)} · 段长 CV ${fmt(s.para_len_cv)}`);
  rows.push(`词汇：TTR ${fmt(s.ttr)} · 连接词密度 ${fmt(s.conn_density)}${s.conn_density === s.conn_density ? " 条/句" : ""} · 4-gram 重复率 ${fmt(s.ngram_repeat)}`);
  return rows;
}

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

function renderReportHtml(profile, result) {
  const parts = [];
  parts.push(`<div class="stats">${sealHtml(result.score)}${result.score_note ? `<div class="row score-note">AI 味指数 —（${esc(result.score_note)}）</div>` : ""}${statsRows(result.stats).map(r => `<div class="row">${esc(r)}</div>`).join("")}${oodHtml(result.ood)}${paraHeatHtml(result)}</div>`);

  const F = result.findings;
  const bySev = { high: [], medium: [], low: [] };
  F.forEach(f => bySev[f.severity].push(f));
  const dist = ["high", "medium", "low"].filter(sv => bySev[sv].length)
    .map(sv => `${SEV_NAME[sv]} ${bySev[sv].length}`).join(" · ");
  parts.push(`<div class="summary">${F.length ? `发现 ${F.length} 处（${dist}）` : "未发现模板化写作"}</div>`);

  const explained = new Set();
  const { groups, docLevel, sevRank } = buildGroups(F);
  groups.forEach(g => {
    const top = g.items.reduce((acc, i) =>
      (sevRank[i.severity] < sevRank[acc.severity] ? i : acc), g.items[0]);
    const ids = [...new Set(g.items.map(i => i.rule_id))].join(" + ");
    const names = [...new Set(g.items.map(i => i.rule_name))].join(" + ");
    const matchArr = [...new Set(g.items.flatMap(i => i.matches))];
    const why = g.items.find(i => !explained.has(i.rule_id));
    g.items.forEach(i => explained.add(i.rule_id));
    parts.push(`<div class="found sev-${top.severity}">
      <div class="mg-head"><span class="mg-dot"></span><span class="mg-kind">${SEV_NAME[top.severity]}</span><span class="rid">${esc(ids)}</span><span class="rname">${esc(names)}</span><span class="loc">¶${g.para + 1}</span></div>
      ${g.sentence ? `<blockquote>${hiSentence(g.sentence, matchArr)}</blockquote>` : ""}
      ${matchArr.length ? `<div class="match">命中：<code>${esc(matchArr.join("、"))}</code></div>` : ""}
      ${why ? `<div class="why">${esc(why.explanation.trim())}</div>${why.suggestion ? `<div class="tip">→ ${esc(why.suggestion.trim())}</div>` : ""}` : ""}
    </div>`);
  });
  docLevel.forEach(f => {
    const why = !explained.has(f.rule_id);
    explained.add(f.rule_id);
    parts.push(`<div class="found sev-${f.severity}">
      <div class="mg-head"><span class="mg-dot"></span><span class="mg-kind">${SEV_NAME[f.severity]}</span><span class="rid">${esc(f.rule_id)}</span><span class="rname">${esc(f.rule_name)}</span><span class="loc">全文</span></div>
      ${f.matches.length ? `<div class="match">命中：<code>${esc([...new Set(f.matches)].join("、"))}</code></div>` : ""}
      ${why ? `<div class="why">${esc(f.explanation.trim())}</div>${f.suggestion ? `<div class="tip">→ ${esc(f.suggestion.trim())}</div>` : ""}` : ""}
    </div>`);
  });

  parts.push(hintsHtml(result.hints));
  parts.push(`<div class="disclaimer">${DISCLAIMER}</div>`);
  return parts.join("");
}

function renderAdviceHtml(result) {
  const A = result.advices;
  const n = k => A.filter(a => a.action === k).length;
  const parts = [`<div class="counts"><span>共 <b>${A.length}</b> 条 · 删 <b>${n("删")}</b> · 改 <b>${n("改")}</b> · 保留 <b>${n("保留")}</b></span></div>`];
  const ICON = { "删": "del", "改": "chg", "保留": "keep" };
  A.forEach(a => {
    parts.push(`<div class="advice ${ICON[a.action] || "keep"}">
      <div class="line"><span class="tag">${a.action}</span>${esc(a.text)}${a.taste.length ? `<span class="taste">${esc(a.taste.join("/"))}</span>` : ""}</div>
      ${a.reason ? `<div class="why">${esc(a.reason)}</div>` : ""}
      ${a.candidate ? `<div class="cand">→ ${esc(a.candidate)}</div>` : (a.direction ? `<div class="dir">→ ${esc(a.direction)}</div>` : "")}
    </div>`);
  });
  parts.push(`<div class="disclaimer">${ADVICE_FOOTER}</div>`);
  return parts.join("");
}

/* 报告 Markdown 导出（与 CLI/网页同口径） */
function reportToMarkdown(profile, result) {
  const r = result;
  const L = [`# human-vs-ai 分析报告（${profile}）`, "", "## 全文统计", ""];
  if (r.score) {
    L.push(`- AI 味指数：${r.score.index.toFixed(0)} / 100（风格分，不是 AI 概率）`);
    const comps = componentsText(r.score.components);
    if (comps) L.push(`- 构成：${comps}`);
  } else if (r.scoring_note) {
    L.push(`- AI 味指数：—（${r.scoring_note}）`);
  }
  statsRows(r.stats).forEach(row => L.push(`- ${row}`));
  L.push("", `## 发现（${r.findings.length} 处）`, "");
  if (!r.findings.length) L.push("未发现模板化写作");
  const explained = new Set();
  const { groups, docLevel, sevRank } = buildGroups(r.findings);
  groups.forEach(g => {
    const top = g.items.reduce((acc, i) =>
      (sevRank[i.severity] < sevRank[acc.severity] ? i : acc), g.items[0]);
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
    L.push(`### [${SEV_NAME[f.severity]}] ${f.rule_id} ${f.rule_name}（全文）`, "");
    if (f.matches.length) L.push(`**命中**：${[...new Set(f.matches)].join("、")}`, "");
    if (why) {
      L.push(f.explanation.trim());
      if (f.suggestion) L.push("", `**建议**：${f.suggestion.trim()}`);
      L.push("");
    }
  });
  if (r.hints.length) {
    L.push(`## 弱命中（共 ${r.hints.length} 处）`, "");
    r.hints.slice(0, HINTS_MAX).forEach(h => L.push(`- ${h.rule_id} ${h.rule_name}（¶${h.para + 1}）`));
    if (r.hints.length > HINTS_MAX) L.push(`- …等 ${r.hints.length} 处`);
    L.push("");
  }
  L.push("---", "", DISCLAIMER);
  return L.join("\n");
}

function adviceToMarkdown(result) {
  const A = result.advices;
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

/* ================= Obsidian 宿主（由 makePlugin 注入 obsidian 模块） ================= */

function makePlugin(obsidian) {
  const { Plugin, ItemView, MarkdownView, Notice } = obsidian;
  const VIEW_TYPE = "human-vs-ai-view";
  const MODES = [["analyze", "检测"], ["rewrite", "改写建议"]];

  class HvAView extends ItemView {
    constructor(leaf, plugin) {
      super(leaf);
      this.plugin = plugin;
      this.mode = plugin.settings.mode || "analyze";
      this.profile = plugin.settings.profile || "academic";
      this.navigation = false;
    }
    getViewType() { return VIEW_TYPE; }
    getDisplayText() { return "human-vs-ai 分析"; }
    getIcon() { return "stamp"; }
    async onOpen() {
      const root = this.contentEl;
      root.empty();
      root.addClass("hva-root");
      root.innerHTML = `
        <div class="hva-head">
          <select class="hva-profiles">${PROFILES.map(p =>
            `<option value="${p}" ${p === this.profile ? "selected" : ""}>${(PROFILE_META[p] || [p])[0]}</option>`).join("")}</select>
          <span class="hva-modes">${MODES.map(([k, lbl]) =>
            `<button class="hva-mode ${k === this.mode ? "on" : ""}" data-m="${k}">${lbl}</button>`).join("")}</span>
          <button class="hva-rescan">重新分析</button>
          <button class="hva-copy" disabled>复制 Markdown</button>
        </div>
        <div class="hva-file"></div>
        <div class="hva-report"><div class="hva-empty">打开一篇笔记即可分析。</div></div>`;
      root.querySelector(".hva-profiles").onchange = (e) => {
        this.profile = e.target.value;
        this.plugin.settings.profile = this.profile;
        this.plugin.saveData(this.plugin.settings);
        this.analyze();
      };
      root.querySelectorAll(".hva-mode").forEach(b => {
        b.onclick = () => {
          this.mode = b.dataset.m;
          if (this.mode === "rewrite") this.profile = "personal";
          this.plugin.settings.profile = this.profile;
          this.plugin.settings.mode = this.mode;
          this.plugin.saveData(this.plugin.settings);
          root.querySelector(".hva-profiles").value = this.profile;
          root.querySelectorAll(".hva-mode").forEach(x => x.classList.toggle("on", x === b));
          this.analyze();
        };
      });
      root.querySelector(".hva-rescan").onclick = () => this.analyze();
      root.querySelector(".hva-copy").onclick = () => {
        const r = this.lastResult;
        if (!r) return;
        const md = this.mode === "rewrite" ? adviceToMarkdown(r) : reportToMarkdown(this.profile, r);
        navigator.clipboard.writeText(md).then(
          () => new Notice("已复制 Markdown"),
          () => new Notice("复制失败"));
      };
      this.analyze();
    }
    currentText() {
      const view = this.app.workspace.getActiveViewOfType(MarkdownView);
      return view ? view.editor.getValue() : null;
    }
    currentFileName() {
      const view = this.app.workspace.getActiveViewOfType(MarkdownView);
      return view ? (view.file ? view.file.name : "未命名") : null;
    }
    analyze() {
      const text = this.currentText();
      const fileEl = this.contentEl.querySelector(".hva-file");
      const reportEl = this.contentEl.querySelector(".hva-report");
      fileEl.textContent = this.currentFileName() || "";
      if (text == null) {
        reportEl.innerHTML = `<div class="hva-empty">当前没有打开的笔记。</div>`;
        this.lastResult = null;
        this.contentEl.querySelector(".hva-copy").disabled = true;
        return;
      }
      if (!text.trim()) {
        reportEl.innerHTML = `<div class="hva-empty">笔记是空的。</div>`;
        this.lastResult = null;
        this.contentEl.querySelector(".hva-copy").disabled = true;
        return;
      }
      const rules = RULES[this.profile];
      if (this.mode === "rewrite") {
        this.lastResult = HvARewrite.rewriteText(text, rules);
        reportEl.innerHTML = renderAdviceHtml(this.lastResult);
      } else {
        this.lastResult = HvA.analyze(text, rules, SCORING[this.profile] || null);
        reportEl.innerHTML = renderReportHtml(this.profile, this.lastResult);
      }
      this.contentEl.querySelector(".hva-copy").disabled = !this.lastResult;
    }
  }

  class HvAPlugin extends Plugin {
    async onload() {
      this.settings = Object.assign({ profile: "academic", mode: "analyze" },
        await this.loadData());
      this.registerView(VIEW_TYPE, (leaf) => new HvAView(leaf, this));
      this.addRibbonIcon("stamp", "human-vs-ai 分析", () => this.activateView());
      this.addCommand({
        id: "analyze-current",
        name: "分析当前文档",
        callback: () => this.activateView("analyze"),
      });
      this.addCommand({
        id: "rewrite-current",
        name: "改写建议（个人口味）",
        callback: () => this.activateView("rewrite"),
      });
      // 笔记修改后自动重析（800ms 防抖，视图开着才跑）
      this._modifyTimer = null;
      this.registerEvent(this.app.vault.on("modify", () => {
        clearTimeout(this._modifyTimer);
        this._modifyTimer = setTimeout(() => {
          const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
          if (leaf && leaf.view && leaf.view.analyze) leaf.view.analyze();
        }, 800);
      }));
      this.addSettingTab(new HvASettingTab(this.app, this));
    }
    async activateView(mode) {
      const { workspace } = this.app;
      let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
      if (!leaf) {
        leaf = workspace.getRightLeaf(false);
        await leaf.setViewState({ type: VIEW_TYPE, active: true });
      }
      workspace.revealLeaf(leaf);
      if (mode && leaf.view) {
        leaf.view.mode = mode;
        if (mode === "rewrite") leaf.view.profile = "personal";
        leaf.view.analyze();
      }
    }
    onunload() {
      this.app.workspace.detachLeavesOfType(VIEW_TYPE);
    }
  }

  class HvASettingTab extends obsidian.PluginSettingTab {
    constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
    display() {
      const { containerEl } = this;
      containerEl.empty();
      containerEl.createEl("h2", { text: "human-vs-ai 设置" });
      new obsidian.Setting(containerEl)
        .setName("默认场景")
        .setDesc("打开分析视图时使用的场景")
        .addDropdown(dd => {
          PROFILES.forEach(p => dd.addOption(p, (PROFILE_META[p] || [p])[0]));
          dd.setValue(this.plugin.settings.profile)
            .onChange(v => { this.plugin.settings.profile = v; this.plugin.saveData(this.plugin.settings); });
        });
    }
  }

  return { HvAPlugin, HvAView, HvASettingTab };
}

/* 视图打开时把"改写建议只按 personal"的纪律显示出来——视图头部的场景
   下拉在改写模式下会被强制为 personal（activateView / 模式切换处已处理）。 */

module.exports = { VERSION, PROFILES, renderReportHtml, renderAdviceHtml,
                   reportToMarkdown, adviceToMarkdown, makePlugin,
                   sealHtml, statsRows, buildGroups };
