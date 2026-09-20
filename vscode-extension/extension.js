/* human-vs-ai VS Code 扩展：对当前文档一键分析 + Webview 报告面板。
 *
 * 与 CLI/网页版的关系：引擎直接复用 web/engine.js（与 Python 引擎由
 * tools/check_web_consistency.py 守护逐字段一致），规则 JSON 由
 * tools/build_vscode.py 从同一 YAML 源注入——三端（CLI/网页/插件）
 * 同一事实源，插件端不允许独立演化。
 *
 * 纯本地：无任何网络调用。
 * vscode 模块延迟到使用处 require——让报告渲染与"发现→文档定位"逻辑
 * 可被 node 冒烟测试直接加载（smoke-test.js），不为可测性引入构建步骤。
 */
const path = require("path");
const HvA = require("./engine.js");
let HvARewrite = null;
try { HvARewrite = require("./rewrite.js"); } catch (e) { HvARewrite = null; }
const RULES = require("./rules.json");
let SCORING = {};
try { SCORING = require("./scoring.json"); } catch (e) { SCORING = {}; }
/* 渲染共享层（web/render.js，build_vscode.py 复制）：转义/高亮/评分行/常量 */
const { esc, fmt, hiSentence, scoreRow, scoreNoteRow, hintsHtml,
        SEV_NAME, PROFILE_META, DISCLAIMER, ADVICE_FOOTER } = require("./render.js");

/* 扩展专用：命中句在编辑器里画波浪线的严重级配色（webview 内用 CSS 变量，
   编辑器装饰必须给实色；hint 档不画装饰） */
const SEV_COLOR = { high: "#B3261E", medium: "#9A6B00", low: "#0F766E" };

/* 报告 HTML：结构与 CLI/网页版同一份内容（统计摘要 → 逐条发现 → 弱命中 → 免责），
   样式对齐网页版；颜色走 --vscode-* 主题变量（VS Code 会给 webview body
   挂 vscode-light / vscode-dark 类），暗色主题下不再白底刺眼。 */
function renderReportHtml(fileName, profile, result) {
  const s = result.stats;
  const parts = [];

  parts.push(`<div class="stats">
    ${scoreRow(result.score)}
    ${scoreNoteRow(result.score_note)}
    <div class="row">规模：<b>${s.n_paragraphs}</b> 段 · <b>${s.n_sentences}</b> 句 · <b>${s.n_chars}</b> 字</div>
    ${s.n_sentences < 8 ? "" : `<div class="row">节奏：句长 CV <b>${fmt(s.sentence_cv)}</b> · 段长 CV <b>${fmt(s.para_len_cv)}</b></div>
    <div class="row">词汇：TTR <b>${fmt(s.ttr)}</b> · 连接词密度 <b>${fmt(s.conn_density)}</b>${s.conn_density === s.conn_density ? " 条/句" : ""} · 4-gram 重复率 <b>${fmt(s.ngram_repeat)}</b></div>`}
  </div>`);

  const F = result.findings;
  const bySev = { high: [], medium: [], low: [] };
  F.forEach(f => bySev[f.severity].push(f));
  const dist = ["high", "medium", "low"].filter(sv => bySev[sv].length)
    .map(sv => `${SEV_NAME[sv]} ${bySev[sv].length}`).join(" · ");
  parts.push(`<div class="summary">${F.length ? `发现 ${F.length} 处（${dist}）` : "未发现明显的模板化写作模式。"}</div>`);

  const explained = new Set();
  ["high", "medium", "low"].forEach(sev => {
    bySev[sev].sort((a, b) => a.para - b.para).forEach(f => {
      const loc = f.para >= 0 ? `¶${f.para + 1}` : "全文";
      // 同一规则的解释全文只讲一次——与 CLI/网页版口径一致
      const showWhy = !explained.has(f.rule_id);
      if (showWhy) explained.add(f.rule_id);
      const matchArr = [...new Set(f.matches)];
      parts.push(`<div class="found">
        <div class="head"><span class="dot" style="background:${SEV_COLOR[sev]}"></span>${SEV_NAME[sev]} · ${esc(f.rule_id)} ${esc(f.rule_name)}<span class="loc">${loc}</span></div>
        ${f.sentence ? `<blockquote>${hiSentence(f.sentence, matchArr)}</blockquote>` : ""}
        ${matchArr.length ? `<div class="match">命中：<code>${esc(matchArr.join("、"))}</code></div>` : ""}
        ${showWhy ? `<div class="why">${esc(f.explanation.trim())}</div>${f.suggestion ? `<div class="tip">→ ${esc(f.suggestion.trim())}</div>` : ""}` : ""}
      </div>`);
    });
  });

  parts.push(hintsHtml(result.hints));

  parts.push(`<div class="disclaimer">${DISCLAIMER}</div>`);

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
body {
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 13px; line-height: 1.6;
  background: var(--vscode-editor-background, #FFFFFF);
  color: var(--vscode-editor-foreground, #1A1A18);
  padding: 12px 18px 24px;
  --hairline: var(--vscode-editorWidget-border, #E5E5E3);
  --ink-2: var(--vscode-descriptionForeground, #6E6E6A);
  --ink-3: var(--vscode-descriptionForeground, #8A8A86);
  --chip: var(--vscode-textCodeBlock-background, #F4F4F2);
  --soft: var(--vscode-textBlockQuote-background, #FAFAF8);
  --mark: rgba(154, 107, 0, 0.20);
  overflow-wrap: anywhere;
}
body.vscode-dark, body.vscode-high-contrast {
  --sev-high: #E5706A;
  --sev-medium: #D0A238;
  --sev-low: #45B8A8;
  --mark: rgba(208, 162, 56, 0.30);
}
b { font-variant-numeric: tabular-nums; }
.stats { padding-bottom: 12px; border-bottom: 1px solid var(--hairline); }
.stats .row { font-size: 12px; color: var(--ink-2); }
.stats .row + .row { margin-top: 2px; }
.stats .row.score b.s-high { color: var(--sev-high); }
.stats .row.score b.s-medium { color: var(--sev-medium); }
.stats .row.score b.s-low { color: var(--sev-low); }
.stats .row.score .comp { color: var(--ink-3); }
.stats .row.score b { color: inherit; }
.summary { padding: 12px 0 4px; font-weight: 600; }
.found { padding: 10px 0; border-bottom: 1px solid var(--hairline); }
.found .head { font-weight: 600; }
.found .head .loc { color: var(--ink-3); font-weight: 400; font-size: 10.5px; margin-left: 8px; }
blockquote { margin: 6px 0 4px; padding: 2px 0 2px 12px; border-left: 2px solid var(--hairline); color: var(--ink-2); }
.match { font-size: 12px; color: var(--ink-3); margin: 2px 0 6px; }
.match code { background: var(--chip); padding: 0 4px; border-radius: 2px; }
.why { margin: 3px 0; }
.tip { color: var(--sev-low); margin-top: 3px; }
mark { background: var(--mark); color: inherit; border-radius: 2px; padding: 0 1px; }
.hints { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--hairline); }
.hints .t { font-size: 12px; color: var(--ink-3); font-weight: 600; margin-bottom: 4px; }
.hints .h { font-size: 12px; color: var(--ink-3); }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: var(--soft); font-size: 10.5px;
              color: var(--ink-3); border-radius: 3px; }
.docname { font-size: 10.5px; color: var(--ink-3); padding-bottom: 8px; }
</style></head>
<body><div class="docname">${esc(fileName)} · ${esc((PROFILE_META[profile] || [profile])[0])}</div>${parts.join("")}</body></html>`;
}

function renderAdviceHtml(fileName, result) {
  const A = result.advices;
  const n = k => A.filter(a => a.action === k).length;
  const META = { "删": ["del", "删"], "改": ["chg", "改"], "保留": ["keep", "留"] };
  const rows = A.map(a => {
    const [cls, label] = META[a.action] || ["keep", "?"];
    const taste = a.taste && a.taste.length
      ? `<span class="taste">${esc(a.taste.join("/"))}</span>` : "";
    return `<div class="advice ${cls}">
      <div class="line"><span class="tag">${label}</span>${esc(a.text)}${taste}</div>
      ${a.reason ? `<div class="why">${esc(a.reason)}</div>` : ""}
      ${a.candidate ? `<div class="cand">→ ${esc(a.candidate)}</div>` : ""}
      ${a.direction ? `<div class="dir">→ ${esc(a.direction)}</div>` : ""}
    </div>`;
  }).join("");
  const counts = `<div class="counts">共 <b>${A.length}</b> 条 · 删 <b>${n("删")}</b> · 改 <b>${n("改")}</b> · 保留 <b>${n("保留")}</b></div>`;
  const footer = `<div class="disclaimer">${ADVICE_FOOTER}</div>`;

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
body {
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 13px; line-height: 1.6;
  background: var(--vscode-editor-background, #FFFFFF);
  color: var(--vscode-editor-foreground, #1A1A18);
  padding: 12px 18px 24px;
  --hairline: var(--vscode-editorWidget-border, #E5E5E3);
  --ink-2: var(--vscode-descriptionForeground, #6E6E6A);
  --ink-3: var(--vscode-descriptionForeground, #8A8A86);
  --soft: var(--vscode-textBlockQuote-background, #FAFAF8);
  overflow-wrap: anywhere;
}
body.vscode-dark, body.vscode-high-contrast {
  --sev-high: #E5706A;
  --sev-medium: #D0A238;
  --sev-low: #45B8A8;
}
b { font-variant-numeric: tabular-nums; }
.docname { font-size: 10.5px; color: var(--ink-3); padding-bottom: 8px; }
.counts { padding: 6px 0 12px; font-size: 12px; color: var(--ink-2); border-bottom: 1px solid var(--hairline); }
.advice { padding: 10px 0; border-bottom: 1px solid var(--hairline); }
.tag { display: inline-block; min-width: 18px; text-align: center; font-size: 10.5px; font-weight: 600;
       border-radius: 2px; padding: 1px 5px; margin-right: 8px;
       background: var(--vscode-textCodeBlock-background, #F4F4F2); color: var(--ink-2); }
.advice.del .tag { background: rgba(179, 38, 30, 0.14); color: var(--sev-high); }
.advice.chg .tag { background: rgba(154, 107, 0, 0.14); color: var(--sev-medium); }
.advice.keep .tag { background: rgba(15, 118, 110, 0.12); color: var(--sev-low); }
.taste { font-size: 10.5px; color: var(--ink-3); margin-left: 6px; }
.why { color: var(--ink-2); margin: 4px 0 0 26px; }
.cand { color: var(--sev-low); margin: 3px 0 0 26px; }
.dir { color: var(--ink-3); margin: 3px 0 0 26px; font-size: 12px; }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: var(--soft); font-size: 10.5px;
              color: var(--ink-3); border-radius: 3px; }
</style></head>
<body><div class="docname">${esc(fileName)} · personal（我的口味）</div>${counts}${rows}${footer}</body></html>`;
}

/* 发现 → 文档偏移：句子级发现按段落序在原文里顺序定位（报告按严重级排序，
   文档序才反映真实位置）。清洗过的句子若在原文找不到（Markdown 剥离、
   星号强调等），退化为前 10 字前缀定位；再找不到就跳过——装饰是锦上添花，
   绝不能因为它报错。node 冒烟直测本函数。 */
function locateFindings(docText, findings) {
  const out = [];
  let from = 0;
  findings
    .filter(f => f.para >= 0 && f.sentence)
    .slice()
    .sort((a, b) => a.para - b.para)
    .forEach(f => {
      const probes = [f.sentence];
      if (f.sentence.length > 10) probes.push(f.sentence.slice(0, 10));
      for (const base of [from, 0]) {
        for (const probe of probes) {
          const idx = docText.indexOf(probe, base);
          if (idx >= 0) {
            out.push({ start: idx, end: idx + probe.length, severity: f.severity });
            from = idx + probe.length;
            return;
          }
        }
      }
    });
  return out;
}

/* 装饰句柄跟随命令重建：上一次的先 dispose，重复运行不泄漏 */
let activeDecorationTypes = [];

function applyDecorations(editor, located, colorBy) {
  try {
    const vscode = require("vscode");
    activeDecorationTypes.forEach(t => {
      try { editor.setDecorations(t, []); t.dispose(); } catch (e) { /* 已释放 */ }
    });
    activeDecorationTypes = [];
    Object.keys(colorBy).forEach(sev => {
      const ranges = located
        .filter(l => l.severity === sev)
        .map(l => new vscode.Range(
          editor.document.positionAt(l.start), editor.document.positionAt(l.end)));
      if (!ranges.length) return;
      const type = vscode.window.createTextEditorDecorationType({
        textDecoration: "underline wavy " + colorBy[sev],
        overviewRulerColor: colorBy[sev],
        overviewRulerLane: vscode.OverviewRulerLane.Right,
      });
      activeDecorationTypes.push(type);
      editor.setDecorations(type, ranges);
    });
  } catch (e) { /* 装饰是增强，失败不影响报告面板 */ }
}

function rewriteActive() {
  const vscode = require("vscode");
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("human-vs-ai：先打开一个文本文件。");
    return;
  }
  if (!HvARewrite) {
    vscode.window.showErrorMessage("human-vs-ai：改写模块缺失（请重跑 build_vscode.py 同步 rewrite.js）。");
    return;
  }
  const text = editor.document.getText();
  const result = HvARewrite.rewriteText(text, RULES.personal);
  const fileName = path.basename(editor.document.fileName);
  const panel = vscode.window.createWebviewPanel(
    "humanVsAiRewrite", "human-vs-ai 改写建议 · " + fileName,
    vscode.ViewColumn.Beside, { enableScripts: false }
  );
  panel.webview.html = renderAdviceHtml(fileName, result);
  // 删/改条目同步画到编辑器里：红色待删、黄色待改
  const located = locateFindings(text, result.advices
    .filter(a => a.action === "删" || a.action === "改")
    .map((a, i) => ({ para: i, sentence: a.text, severity: a.action === "删" ? "high" : "medium" })));
  applyDecorations(editor, located, { high: SEV_COLOR.high, medium: SEV_COLOR.medium });
  const nDel = result.advices.filter(a => a.action === "删").length;
  const nChg = result.advices.filter(a => a.action === "改").length;
  vscode.window.setStatusBarMessage(
    `human-vs-ai：${fileName} 改写建议 删 ${nDel} · 改 ${nChg}`, 8000
  );
}

function analyzeActive() {
  const vscode = require("vscode");
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("human-vs-ai：先打开一个文本文件。");
    return;
  }
  const profile = vscode.workspace.getConfiguration("human-vs-ai").get("profile", "academic");
  const rules = RULES[profile];
  if (!rules) {
    vscode.window.showErrorMessage(`human-vs-ai：未知场景 ${profile}（可用：${Object.keys(RULES).join("、")}）`);
    return;
  }
  const text = editor.document.getText();
  const result = HvA.analyze(text, rules, SCORING[profile] || null);
  const fileName = path.basename(editor.document.fileName);

  const panel = vscode.window.createWebviewPanel(
    "humanVsAiReport",
    `human-vs-ai · ${fileName}`,
    vscode.ViewColumn.Beside,
    { enableScripts: false }
  );
  panel.webview.html = renderReportHtml(fileName, profile, result);
  applyDecorations(editor, locateFindings(text, result.findings), SEV_COLOR);

  const n = result.findings.length;
  vscode.window.setStatusBarMessage(
    n ? `human-vs-ai：${fileName} 发现 ${n} 处` : `human-vs-ai：${fileName} 未发现明显模板化写作模式`,
    8000
  );
}

function activate(context) {
  const vscode = require("vscode");
  context.subscriptions.push(
    vscode.commands.registerCommand("human-vs-ai.analyze", analyzeActive),
    vscode.commands.registerCommand("human-vs-ai.rewrite", rewriteActive)
  );
}

function deactivate() {}

module.exports = { activate, deactivate, renderReportHtml, renderAdviceHtml, locateFindings };
