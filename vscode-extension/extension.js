/* human-vs-ai VS Code 扩展：对当前文档一键分析 + Webview 报告面板。
 *
 * 与 CLI/网页版的关系：引擎直接复用 web/engine.js（与 Python 引擎由
 * tools/check_web_consistency.py 守护逐字段一致），规则 JSON 由
 * tools/build_vscode.py 从同一 YAML 源注入——四端（CLI/网页/VS Code/Obsidian）
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
const { esc, fmt, hiSentence, sealHtml, scoreNoteRow, oodHtml, paraHeatHtml, hintsHtml,
        buildGroups, SEV_NAME, PROFILE_META, DISCLAIMER, ADVICE_FOOTER } = require("./render.js");

/* 扩展专用：命中句在编辑器里画波浪线的严重级配色（webview 内用 CSS 变量，
   编辑器装饰必须给实色；hint 档不画装饰） */
const SEV_COLOR = { high: "#B3351F", medium: "#9C7414", low: "#1D4E5F" };

/* 报告 HTML：结构与 CLI/网页版同一份内容（统计摘要 → 逐条发现 → 弱命中 → 免责），
   样式对齐网页版；颜色走 --vscode-* 主题变量（VS Code 会给 webview body
   挂 vscode-light / vscode-dark 类），暗色主题下不再白底刺眼。 */
function renderReportHtml(fileName, profile, result) {
  const s = result.stats;
  const parts = [];

  parts.push(`<div class="stats">
    ${sealHtml(result.score)}
    ${scoreNoteRow(result.score_note)}
    <div class="row">规模：<b>${s.n_paragraphs}</b> 段 · <b>${s.n_sentences}</b> 句 · <b>${s.n_chars}</b> 字</div>
    ${s.n_sentences < 8 ? "" : `<div class="row">节奏：句长 CV <b>${fmt(s.sentence_cv)}</b> · 段长 CV <b>${fmt(s.para_len_cv)}</b></div>
    <div class="row">词汇：TTR <b>${fmt(s.ttr)}</b> · 连接词密度 <b>${fmt(s.conn_density)}</b>${s.conn_density === s.conn_density ? " 条/句" : ""} · 4-gram 重复率 <b>${fmt(s.ngram_repeat)}</b></div>`}
    ${oodHtml(result.ood)}
    ${paraHeatHtml(result)}
  </div>`);

  const F = result.findings;
  const bySev = { high: [], medium: [], low: [] };
  F.forEach(f => bySev[f.severity].push(f));
  const dist = ["high", "medium", "low"].filter(sv => bySev[sv].length)
    .map(sv => `${SEV_NAME[sv]} ${bySev[sv].length}`).join(" · ");
  parts.push(`<div class="summary">${F.length ? `发现 ${F.length} 处（${dist}）` : "未发现模板化写作"}</div>`);

  /* 同句多规则聚成一张卡（与网页/Obsidian/CLI 同口径）——按严重级逐卡平铺
     会让同一句话引用多次、解释重复，长文里尤其吵 */
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
    const matchArr = [...new Set(f.matches)];
    parts.push(`<div class="found sev-${f.severity}">
      <div class="mg-head"><span class="mg-dot"></span><span class="mg-kind">${SEV_NAME[f.severity]}</span><span class="rid">${esc(f.rule_id)}</span><span class="rname">${esc(f.rule_name)}</span><span class="loc">全文</span></div>
      ${matchArr.length ? `<div class="match">命中：<code>${esc(matchArr.join("、"))}</code></div>` : ""}
      ${why ? `<div class="why">${esc(f.explanation.trim())}</div>${f.suggestion ? `<div class="tip">→ ${esc(f.suggestion.trim())}</div>` : ""}` : ""}
    </div>`);
  });

  parts.push(hintsHtml(result.hints));

  parts.push(`<div class="disclaimer">${DISCLAIMER}</div>`);

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
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
  --accent-soft: rgba(29, 78, 95, 0.12);
  --accent-line: rgba(29, 78, 95, 0.34);
  --accent-deep: #123a47;
  --mark: rgba(184, 70, 46, 0.12);
  --sev-high: #B3351F;
  --sev-medium: #9C7414;
  --sev-low: #1D4E5F;
  --mono: 'JetBrains Mono', ui-monospace, 'Cascadia Mono', 'Consolas', monospace;
  overflow-wrap: anywhere;
}
body.vscode-dark, body.vscode-high-contrast {
  --sev-high: #E06A50;
  --sev-medium: #CFA23A;
  --sev-low: #74b4c7;
  --accent-soft: rgba(116, 180, 199, 0.16);
  --accent-line: rgba(116, 180, 199, 0.4);
  --accent-deep: #9ad0e0;
  --mark: rgba(224, 106, 80, 0.16);
  --chip: rgba(255, 255, 255, 0.06);
  --soft: rgba(255, 255, 255, 0.05);
}
b { font-variant-numeric: tabular-nums; }
@keyframes riseIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.found, .hints, .advice, .counts, .disclaimer { animation: riseIn .26s cubic-bezier(.2,.7,.3,1) backwards; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
.stats { padding-bottom: 12px; border-bottom: 1px solid var(--hairline); }
.stats .row { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.stats .row + .row { margin-top: 2px; }
.row.score { display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }
.seal {
  flex: none; display: inline-flex; flex-direction: column; align-items: center;
  justify-content: center; padding: 6px 12px; border: 1.5px solid currentColor;
  border-radius: 3px; transform: rotate(-4deg); font-family: var(--mono); line-height: 1;
}
.seal .n { font-size: 24px; font-weight: 700; }
.seal .u { font-size: 8.5px; letter-spacing: 0.3em; margin-top: 3px; }
.seal.none { border-style: dashed; color: var(--ink-3); }
.seal.high { color: var(--sev-high); }
.seal.medium { color: var(--sev-medium); }
.seal.low { color: var(--sev-low); }
.mono-num { font-family: var(--mono); }
.score-main .t { font-weight: 650; font-size: 14px; }
.score-main .sub { display: block; font-size: 10.5px; color: var(--ink-3); margin-top: 2px; }
.score-main .ci { white-space: nowrap; }
.stats .row.ood-note { font-size: 11px; color: var(--sev-high); margin-top: 6px; }
.ood-note::before { content: '※ '; }
.stats .row.heat-note { font-size: 11px; color: var(--ink-3); margin-top: 6px; }
.ph b { font-weight: 650; }
.ph-high { color: var(--sev-high); }
.ph-medium { color: var(--accent-deep); }
.ph-sep { color: var(--hairline); }
.summary { padding: 12px 0 4px; font-weight: 650; }
.found {
  background: var(--chip); border: 1px solid var(--hairline);
  border-left-width: 2.5px; border-radius: 0 3px 3px 0;
  padding: 7px 10px 8px; margin-bottom: 8px;
}
.found.sev-high { border-left-color: var(--sev-high); }
.found.sev-medium { border-left-color: var(--sev-medium); }
.found.sev-low { border-left-color: var(--sev-low); }
.mg-head { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; flex-wrap: wrap; }
.mg-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.sev-high .mg-dot { background: var(--sev-high); }
.sev-medium .mg-dot { background: var(--sev-medium); }
.sev-low .mg-dot { background: var(--sev-low); }
.mg-kind { font-size: 10.5px; font-weight: 600; }
.sev-high .mg-kind { color: var(--sev-high); }
.sev-medium .mg-kind { color: var(--sev-medium); }
.sev-low .mg-kind { color: var(--sev-low); }
.rid { font-family: var(--mono, Consolas); font-size: 10.5px; color: var(--ink-2); }
.rname { font-weight: 600; font-size: 12px; }
.found .head .loc, .loc { color: var(--ink-3); font-weight: 400; font-size: 10.5px; margin-left: auto; font-family: var(--mono, Consolas); }
blockquote { margin: 6px 0 4px; padding: 2px 0 2px 12px; border-left: 2px solid var(--hairline); color: var(--ink-2); }
.match { font-size: 12px; color: var(--ink-3); margin: 2px 0 6px; }
.match code { background: var(--accent-soft, var(--chip)); border: 1px solid var(--accent-line, var(--chip)); padding: 0 4px; border-radius: 2px; }
.why { margin: 3px 0; }
.tip { color: var(--sev-low); margin-top: 3px; }
mark {
  background-color: var(--mark);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='7' height='4'%3E%3Cpath d='M0 3q1.75 -2.4 3.5 0t3.5 0' fill='none' stroke='%23b8462e' stroke-opacity='.8' stroke-width='1'/%3E%3C/svg%3E");
  background-repeat: repeat-x; background-position: 0 100%; background-size: 7px 4px;
  color: inherit; border-radius: 1px; padding: 0 1px;
}
.hints { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--hairline); }
.hints summary.t { cursor: pointer; user-select: none; list-style: none; }
.hints summary.t::-webkit-details-marker { display: none; }
.hints summary.t::before { content: '▸ '; }
.hints[open] summary.t::before { content: '▾ '; }
.hints .h { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: var(--soft); font-size: 10.5px;
              color: var(--ink-3); border-radius: 3px; }
.docname { font-size: 10.5px; color: var(--ink-3); padding-bottom: 8px; }
</style></head>
<body><div class="docname">${esc(fileName)} · ${esc((PROFILE_META[profile] || [profile])[0])}</div>${parts.join("")}</body></html>`;
}

function renderAdviceHtml(fileName, result, sourceText) {
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
  /* 清理稿：删/改建议机械落地后的草稿（传入原文才有）——webview 无脚本，
     用可选中纯文本 <pre> 呈现，默认折叠 */
  let draftBlock = "";
  if (typeof sourceText === "string" && HvARewrite) {
    const draft = HvARewrite.applyRewrite(sourceText, A);
    if (draft.trim()) {
      draftBlock = `<details class="draftbox"><summary>清理稿（草稿 · 选中即可复制）</summary>` +
        `<pre>${esc(draft)}</pre>` +
        `<div class="draft-note">只落地了删行与换候选；带「→ 方向」的条目要人来改。</div></details>`;
    }
  }
  const footer = `<div class="disclaimer">${ADVICE_FOOTER}</div>`;

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
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
@keyframes riseIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.found, .hints, .advice, .counts, .disclaimer { animation: riseIn .26s cubic-bezier(.2,.7,.3,1) backwards; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
.docname { font-size: 10.5px; color: var(--ink-3); padding-bottom: 8px; }
.counts { padding: 6px 0 12px; font-size: 12px; color: var(--ink-2); border-bottom: 1px solid var(--hairline); }
.advice { padding: 10px 0; border-bottom: 1px solid var(--hairline); }
.tag { display: inline-block; min-width: 18px; text-align: center; font-size: 10.5px; font-weight: 600;
       border-radius: 3px; padding: 1px 5px; margin-right: 8px;
       border: 1px solid currentColor; background: transparent; color: var(--ink-2); }
.advice.del .tag { color: var(--sev-high); }
.advice.chg .tag { color: var(--sev-medium); }
.advice.keep .tag { color: var(--sev-low); }
.taste { font-size: 10.5px; color: var(--ink-3); margin-left: 6px; }
.why { color: var(--ink-2); margin: 4px 0 0 26px; }
.cand { color: var(--sev-low); margin: 3px 0 0 26px; }
.dir { color: var(--ink-3); margin: 3px 0 0 26px; font-size: 12px; }
.disclaimer { margin-top: 18px; padding: 10px 14px; background: var(--soft); font-size: 10.5px;
              color: var(--ink-3); border-radius: 3px; }
.draftbox { margin-top: 14px; border: 1px solid var(--hairline); border-radius: 3px; }
.draftbox summary { cursor: pointer; user-select: none; padding: 7px 10px; font-size: 12px;
                    font-weight: 600; color: var(--ink-2); list-style: none; }
.draftbox summary::before { content: '▸ '; }
.draftbox[open] summary::before { content: '▾ '; }
.draftbox summary::-webkit-details-marker { display: none; }
.draftbox pre { margin: 0; padding: 10px 12px; border-top: 1px solid var(--hairline);
                white-space: pre-wrap; overflow-wrap: anywhere;
                font-family: var(--vscode-editor-font-family, Consolas); font-size: 12.5px;
                line-height: 1.7; background: var(--soft); }
.draft-note { padding: 6px 10px; font-size: 10.5px; color: var(--ink-3);
              border-top: 1px solid var(--hairline); }
</style></head>
<body><div class="docname">${esc(fileName)} · 我的口味</div>${counts}${rows}${draftBlock}${footer}</body></html>`;
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
    vscode.window.showErrorMessage("human-vs-ai：改写模块缺失，请重跑 build_vscode.py。");
    return;
  }
  const text = editor.document.getText();
  const result = HvARewrite.rewriteText(text, RULES.personal);
  const fileName = path.basename(editor.document.fileName);
  const panel = vscode.window.createWebviewPanel(
    "humanVsAiRewrite", "human-vs-ai 改写建议 · " + fileName,
    vscode.ViewColumn.Beside, { enableScripts: false }
  );
  panel.webview.html = renderAdviceHtml(fileName, result, text);
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

let lastProfile = null;   // 会话内记住上次场景，QuickPick 排最前

async function pickProfile(vscode) {
  const configured = vscode.workspace.getConfiguration("human-vs-ai").get("profile", "academic");
  const names = Object.keys(RULES).sort((a, b) =>
    (a === lastProfile ? -1 : b === lastProfile ? 1 : 0) || a.localeCompare(b));
  const items = names.map(p => ({
    label: (PROFILE_META[p] || [p])[0],
    description: (p === configured ? "设置默认 · " : "") + (PROFILE_META[p] || ["", ""])[1],
    profile: p,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: "选择场景",
  });
  return picked ? picked.profile : null;
}

async function analyzeActive() {
  const vscode = require("vscode");
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("human-vs-ai：先打开一个文本文件。");
    return;
  }
  const profile = await pickProfile(vscode);
  if (!profile) return;   // Esc 取消就是取消：不拿默认场景偷偷跑
  const rules = RULES[profile];
  if (!rules) {
    vscode.window.showErrorMessage(`human-vs-ai：未知场景 ${profile}（可用：${Object.keys(RULES).join("、")}）`);
    return;
  }
  lastProfile = profile;
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
    n ? `human-vs-ai：${fileName} 发现 ${n} 处` : `human-vs-ai：${fileName} 未发现模板化写作`,
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
