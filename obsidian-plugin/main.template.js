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

/* 渲染共享层（web/render.js）：转义/高亮/评分行/发现卡/建议行/常量。
   buildGroups 仅为本文件底部再导出保留（冒烟测试的兼容面） */
const { esc, sealHtml, scoreNoteRow, oodHtml, paraHeatHtml, hintsHtml,
        findingsHtml, adviceRowsHtml, statsRows,
        reportToMarkdown, adviceToMarkdown,
        PROFILE_META, DISCLAIMER, ADVICE_FOOTER, buildGroups } = HvARender;

/* ================= 报告组装（统计块本端排布；发现卡走共享层） ================= */

function renderReportHtml(profile, result) {
  const parts = [];
  parts.push(`<div class="stats">${sealHtml(result.score)}${scoreNoteRow(result.score_note)}${statsRows(result.stats).map(r => `<div class="row">${esc(r)}</div>`).join("")}${oodHtml(result.ood)}${paraHeatHtml(result)}</div>`);

  parts.push(findingsHtml(result));
  parts.push(hintsHtml(result.hints));
  parts.push(`<div class="disclaimer">${DISCLAIMER}</div>`);
  return parts.join("");
}

function renderAdviceHtml(result, sourceText) {
  const A = result.advices;
  const n = k => A.filter(a => a.action === k).length;
  const parts = [`<div class="counts"><span>共 <b>${A.length}</b> 条 · 删 <b>${n("删")}</b> · 改 <b>${n("改")}</b> · 保留 <b>${n("保留")}</b></span></div>`];
  parts.push(adviceRowsHtml(A));
  /* 清理稿：删/改建议机械落地后的草稿（传入原文才有）——折叠呈现 */
  if (typeof sourceText === "string" && HvARewrite.applyRewrite) {
    const draft = HvARewrite.applyRewrite(sourceText, A);
    if (draft.trim()) {
      parts.push(`<details class="draftbox"><summary class="t">清理稿</summary>` +
        `<pre>${esc(draft)}</pre></details>`);
    }
  }
  parts.push(`<div class="disclaimer">${ADVICE_FOOTER}</div>`);
  return parts.join("");
}

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
      /* 改写建议只按 personal 出口径——旧数据/手改 data.json 可能存出
         mode=rewrite + profile=其他 的错配，恢复时归位 */
      if (this.mode === "rewrite") this.profile = "personal";
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
          <button class="hva-copy" disabled>复制</button>
        </div>
        <div class="hva-file"></div>
        <div class="hva-report"><div class="hva-empty">未打开笔记。</div></div>`;
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
          () => new Notice("已复制"),
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
        reportEl.innerHTML = `<div class="hva-empty">未打开笔记。</div>`;
        this.lastResult = null;
        this.contentEl.querySelector(".hva-copy").disabled = true;
        return;
      }
      if (!text.trim()) {
        reportEl.innerHTML = `<div class="hva-empty">笔记为空。</div>`;
        this.lastResult = null;
        this.contentEl.querySelector(".hva-copy").disabled = true;
        return;
      }
      const rules = RULES[this.profile];
      if (this.mode === "rewrite") {
        this.lastResult = HvARewrite.rewriteText(text, rules);
        reportEl.innerHTML = renderAdviceHtml(this.lastResult, text);
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
