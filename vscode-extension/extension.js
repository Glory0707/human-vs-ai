/* human-vs-ai VS Code 扩展：对当前文档一键分析 + Webview 报告面板。
 *
 * 与 CLI/网页版的关系：引擎直接复用 web/engine.js（与 Python 引擎由
 * tools/check_web_consistency.py 守护逐字段一致），规则 JSON 由
 * tools/build_vscode.py 从同一 YAML 源注入——三端（CLI/网页/插件）
 * 同一事实源，插件端不允许独立演化。
 *
 * 纯本地：无任何网络调用。
 * vscode 模块延迟到使用处 require——让报告渲染逻辑可被 node 冒烟测试
 * 直接加载（smoke-test.js），不为可测性引入构建步骤。
 */
const path = require("path");
const HvA = require("./engine.js");
let HvARewrite = null;
try { HvARewrite = require("./rewrite.js"); } catch (e) { HvARewrite = null; }
const RULES = require("./rules.json");

const PROFILE_LABEL = {
  academic: "学术", general: "问答", official: "公文", personal: "我的口味",
};

const SEV_COLOR = { high: "#B3261E", medium: "#9A6B00", low: "#0F766E", hint: "#8A8A86" };
const SEV_LABEL = { high: "高", medium: "中", low: "低", hint: "弱" };

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmt(v) {
  return typeof v !== "number" || isNaN(v) ? "—" : v.toFixed(2);
}

/* 报告 HTML：结构与 CLI/网页版同一份内容（统计摘要 → 逐条发现 → 弱命中 → 免责），
   样式对齐网页版（纯白纸、发丝线、severity 色点）。 */
function renderReportHtml(fileName, profile, result) {
  const s = result.stats;
  const parts = [];

  parts.push(`<div class="stats">
    <div class="row">规模：<b>${s.n_paragraphs}</b> 段 · <b>${s.n_sentences}</b> 句 · <b>${s.n_chars}</b> 字</div>
    <div class="row">节奏：句长 CV <b>${fmt(s.sentence_cv)}</b>（人类基线 ≈0.45，越低越平） · 段长 CV <b>${fmt(s.para_len_cv)}</b></div>
    <div class="row">词汇：TTR <b>${fmt(s.ttr)}</b> · 连接词密度 <b>${fmt(s.conn_density)}</b>${s.conn_density === s.conn_density ? " 条/句" : ""} · 4-gram 重复率 <b>${fmt(s.ngram_repeat)}</b></div>
  </div>`);

  const F = result.findings;
  const bySev = { high: [], medium: [], low: [] };
  F.forEach(f => bySev[f.severity].push(f));
  const sevName = { high: "高", medium: "中", low: "低" };
  const dist = ["high", "medium", "low"].filter(sv => bySev[sv].length)
    .map(sv => `${sevName[sv]} ${bySev[sv].length}`).join(" · ");
  parts.push(`<div class="summary">${F.length ? `发现 ${F.length} 处（${dist}）` : "未发现明显的模板化写作模式。"}</div>`);

  const explained = new Set();
  ["high", "medium", "low"].forEach(sev => {
    bySev[sev].sort((a, b) => a.para - b.para).forEach(f => {
      const sent = f.sentence.length > 66 ? f.sentence.slice(0, 63) + "…" : f.sentence;
      const loc = f.para >= 0 ? `¶${f.para + 1}` : "全文";
      // 同一规则的解释全文只讲一次——与 CLI/网页版口径一致
      const showWhy = !explained.has(f.rule_id);
      if (showWhy) explained.add(f.rule_id);
      parts.push(`<div class="found">
        <div class="head"><span class="dot" style="background:${SEV_COLOR[sev]}"></span>${sevName[sev]} · ${esc(f.rule_id)} ${esc(f.rule_name)}<span class="loc">${loc}</span></div>
        ${f.sentence ? `<blockquote>${esc(sent)}</blockquote>` : ""}
        ${f.matches.length ? `<div class="match">命中：<code>${esc([...new Set(f.matches)].join("、"))}</code></div>` : ""}
        ${showWhy ? `<div class="why">${esc(f.explanation.trim())}</div>${f.suggestion ? `<div class="tip">→ ${esc(f.suggestion.trim())}</div>` : ""}` : ""}
      </div>`);
    });
  });

  if (result.hints.length) {
    parts.push(`<div class="hints"><div class="t">另有 ${result.hints.length} 处孤立弱命中，仅供参考</div>` +
      result.hints.map(h => `<div class="h">· ${esc(h.rule_id)} ${esc(h.rule_name)}（¶${h.para + 1}）</div>`).join("") +
      `</div>`);
  }

  parts.push(`<div class="disclaimer">风格提示，不是 AI 判定；单条命中不构成证据。</div>`);

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; color: #1A1A18;
       font-size: 13px; line-height: 1.6; background: #FFFFFF; padding: 12px 18px 24px; }
b { font-variant-numeric: tabular-nums; }
.stats { padding-bottom: 12px; border-bottom: 1px solid #E5E5E3; }
.stats .row { font-size: 12px; color: #6E6E6A; }
.stats .row + .row { margin-top: 2px; }
.stats b { color: #1A1A18; }
.summary { padding: 12px 0 4px; font-weight: 600; }
.found { padding: 10px 0; border-bottom: 1px solid #E5E5E3; }
.found .head { font-weight: 600; }
.found .head .loc { color: #8A8A86; font-weight: 400; font-size: 10.5px; margin-left: 8px; }
blockquote { margin: 6px 0 4px; padding: 2px 0 2px 12px; border-left: 2px solid #E5E5E3; color: #6E6E6A; }
.match { font-size: 12px; color: #8A8A86; margin: 2px 0 6px; }
.match code { background: #F4F4F2; padding: 0 4px; border-radius: 2px; }
.why { margin: 3px 0; }
.tip { color: #0F766E; margin-top: 3px; }
.hints { margin-top: 14px; padding-top: 10px; border-top: 1px solid #E5E5E3; }
.hints .t { font-size: 12px; color: #8A8A86; font-weight: 600; margin-bottom: 4px; }
.hints .h { font-size: 12px; color: #8A8A86; }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: #FAFAF8; font-size: 10.5px;
              color: #8A8A86; border-radius: 3px; }
.docname { font-size: 10.5px; color: #8A8A86; padding-bottom: 8px; }
</style></head>
<body><div class="docname">${esc(fileName)} · ${esc(PROFILE_LABEL[profile] || profile)}</div>${parts.join("")}</body></html>`;
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
  const footer = `<div class="disclaimer">改写准则：重要数据和结论要保留；梗得人来补——只给规则化建议，不替你造梗。</div>`;

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; color: #1A1A18;
       font-size: 13px; line-height: 1.6; background: #FFFFFF; padding: 12px 18px 24px; }
b { font-variant-numeric: tabular-nums; }
.docname { font-size: 10.5px; color: #8A8A86; padding-bottom: 8px; }
.counts { padding: 6px 0 12px; font-size: 12px; color: #6E6E6A; border-bottom: 1px solid #E5E5E3; }
.advice { padding: 10px 0; border-bottom: 1px solid #E5E5E3; }
.tag { display: inline-block; min-width: 18px; text-align: center; font-size: 10.5px; font-weight: 600;
       border-radius: 2px; padding: 1px 5px; margin-right: 8px; background: #F4F4F2; color: #6E6E6A; }
.advice.del .tag { background: #FBE9E7; color: #B3261E; }
.advice.chg .tag { background: #FFF4E0; color: #9A6B00; }
.advice.keep .tag { background: #E6F4F2; color: #0F766E; }
.taste { font-size: 10.5px; color: #8A8A86; margin-left: 6px; }
.why { color: #6E6E6A; margin: 4px 0 0 26px; }
.cand { color: #0F766E; margin: 3px 0 0 26px; }
.dir { color: #8A8A86; margin: 3px 0 0 26px; font-size: 12px; }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: #FAFAF8; font-size: 10.5px;
              color: #8A8A86; border-radius: 3px; }
</style></head>
<body><div class="docname">${esc(fileName)} · personal（我的口味）</div>${counts}${rows}${footer}</body></html>`;
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
  const result = HvA.analyze(text, rules);
  const fileName = path.basename(editor.document.fileName);

  const panel = vscode.window.createWebviewPanel(
    "humanVsAiReport",
    `human-vs-ai · ${fileName}`,
    vscode.ViewColumn.Beside,
    { enableScripts: false }
  );
  panel.webview.html = renderReportHtml(fileName, profile, result);

  const n = result.findings.length;
  vscode.window.setStatusBarMessage(
    n ? `human-vs-ai：${fileName} 发现 ${n} 处` : `human-vs-ai：${fileName} 未发现明显模板化写作模式`,
    8000
  );
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("human-vs-ai.analyze", analyzeActive),
    vscode.commands.registerCommand("human-vs-ai.rewrite", rewriteActive)
  );
}

function deactivate() {}

module.exports = { activate, deactivate, renderReportHtml, renderAdviceHtml };
