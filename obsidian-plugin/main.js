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
  /* human-vs-ai 浏览器引擎——与 Python 包 human_vs_ai 同构的第二实现。
 *
 * 存在理由:纯前端网页版(file:// 打开即用,零安装零上传)。
 * 纪律:两个实现的输出必须逐字段一致——tools/check_web_consistency.py
 * 用固定语料对比 Python 与本文件的 findings/hints JSON,任何漂移都会被
 * 测试抓住。改动切分或统计逻辑时两端必须同步改。
 *
 * 口径说明:全文唯一切分口径是字级 2-gram(Python 端 v0.11.0 起同口径),
 * TTR 四端同数并纳入一致性对比。所有 doc 统计(D-UNIF/D-CONN/D-PARA/
 * D-NGRAM/D-DASH)都与分词器无关。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HvA = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var SENT_END = "。！？；…!?;";
  /* 句尾吸收集：边界标点后紧跟的闭引号/句点并入本句（与 Python
     segment.py 的 _SENT_END + ".””』」" 逐码点一致） */
  var SENT_TAIL = SENT_END + ".\u201d\u300f\u300d";
  var OPEN_Q_RE = /[“『「]/;
  var CLOSE_Q_RE = /[”』」]/;
  var PUNCT_RE = /[，。！？；：、…“”‘’《》（）()[\]【】,\.!\?;:"'—\-\s]/g;

  /* 码点长度：.length 数的是 UTF-16 码元，emoji/扩展区汉字（𠮷）一个占
     2——Python 的 len 数码点，所有"字数"统计必须走这里（对拍实证漂移）。
     孤立低代理：Python len 算 1 个码点，前面没有高代理配对时也要数 1 */
  function cpLength(s) {
    var n = 0;
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0xDC00 && c <= 0xDFFF) {
        var p = i > 0 ? s.charCodeAt(i - 1) : 0;
        if (p >= 0xD800 && p <= 0xDBFF) continue;
      }
      n++;
    }
    return n;
  }

  /* 与 Python segment.py 同构的 Markdown 解析：标题/代码丢弃，
     列表项与表格行内容保留（各成句，不触发独句段形状规则） */
  var FENCE_RE = /^\s*(?:```|~~~)/;
  var HEADING_RE = /^\s*#{1,6}(?:\s|$)/;
  var HR_RE = /^\s*(?:-\s*){2,}-?\s*$|^\s*(?:\*\s*){2,}\*?\s*$|^\s*_{3,}\s*$/;
  var SETEXT_RE = /^\s*=+\s*$/;
  var LIST_RE = /^\s*(?:[-*+]\s+|\d+[.、)](?=\s|\D))\s*(.*)$/;
  var CHECKBOX_RE = /^\s*\[[ xX]\]\s*/;
  var QUOTE_PREFIX_RE = /^\s*>+\s?/;
  var TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$/;

  function inlineClean(line) {
    return line
      .replace(/`([^`]*)`/g, "$1")
      .replace(/!\[([^\]]{0,300})\]\(([^)]{0,500})\)/g, "$1")
      .replace(/\[([^\]]{0,300})\]\(([^)]{0,500})\)/g, "$1")
      .replace(/(?:https?:\/\/|www\.)[^\s，。；！？、）)】」』]+/gi, "")
      .replace(/[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9._-]{1,63})+/g, "")
      .replace(/\*{1,3}(?!\s)([^*]{0,49}?[一-鿿][^*]{0,49}?)(?<!\s)\*{1,3}/g, "$1")
      .replace(/[ \t]{2,}/g, " ")
      .trim();
  }

  /* 行 → 有序单元 ["p"|"li"|"tr"|"b", 文本]。
     行边界集与 Python str.splitlines 对齐（\v \f \x1c-\x1e \x85
     \u2028 \u2029 也是行界；裸 \r 也拆）——段落内硬换行随后在
     groupBlocks 用 \n 重新拼接，两端段落文本逐字一致 */
  var LINE_BREAK_RE = /(?:\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029])/;
  function lineUnits(text) {
    var units = [];
    var inCode = false;
    var rawLines = text.split(LINE_BREAK_RE);
    for (var i = 0; i < rawLines.length; i++) {
      var s = rawLines[i].trim().replace(QUOTE_PREFIX_RE, "");
      if (FENCE_RE.test(s)) { inCode = !inCode; units.push(["b", ""]); continue; }
      if (inCode || !s) { units.push(["b", ""]); continue; }
      if (HEADING_RE.test(s) || HR_RE.test(s) || SETEXT_RE.test(s)) {
        units.push(["b", ""]);
        continue;
      }
      var lm = LIST_RE.exec(s);
      if (lm) {
        var item = lm[1].replace(CHECKBOX_RE, "").trim();
        units.push(item ? ["li", inlineClean(item)] : ["b", ""]);
        continue;
      }
      if (s.indexOf("|") >= 0) {
        if (TABLE_SEP_RE.test(s)) { units.push(["b", ""]); continue; }
        var cells = s.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|");
        var kept = [];
        for (var ci = 0; ci < cells.length; ci++) {
          var c = cells[ci].trim();
          if (c) kept.push(c);
        }
        var row = inlineClean(kept.join("，"));
        units.push(row ? ["tr", row] : ["b", ""]);
        continue;
      }
      units.push(["p", inlineClean(s)]);
    }
    return units;
  }

  /* 单元 → 块 [kind, units[]] */
  function groupBlocks(units) {
    var blocks = [];
    var pBuf = [];
    var run = null;
    function flushProse() {
      if (pBuf.length) { blocks.push(["para", [pBuf.join("\n")]]); pBuf = []; }
    }
    function flushRun() {
      if (run) { blocks.push(run); run = null; }
    }
    for (var i = 0; i < units.length; i++) {
      var kind = units[i][0], t = units[i][1];
      if (kind === "b") { flushProse(); flushRun(); }
      else if (kind === "li" || kind === "tr") {
        flushProse();
        var bk = kind === "li" ? "list" : "table";
        if (!run || run[0] !== bk) { flushRun(); run = [bk, []]; }
        run[1].push(t);
      } else {
        flushRun();
        pBuf.push(t);
      }
    }
    flushProse(); flushRun();
    return blocks;
  }

  /* 引号嵌套:与 Python 相同——开引号加深、闭引号减浅,深处句末标点不算边界 */
  function splitSentences(text) {
    var sents = [];
    var depth = 0, start = 0;
    var n = text.length;
    for (var i = 0; i < n; i++) {
      var ch = text[i];
      if (OPEN_Q_RE.test(ch)) { depth++; continue; }
      if (CLOSE_Q_RE.test(ch)) {
        if (depth > 0) depth--;
        continue;
      }
      /* ASCII 双引号按奇偶切换；ASCII 单引号不参与——英文所有格/缩写
         （it's）远比引语常见，拿它当引号会吞后续句末标点（与 Python 同步） */
      if (ch === "\u0022") {
        depth = depth ? 0 : 1;
        continue;
      }
      if (depth > 0) continue;
      if (SENT_END.indexOf(ch) >= 0) {
        var j = i + 1;
        while (j < n && SENT_TAIL.indexOf(text[j]) >= 0) j++;
        var body = text.slice(start, j).trim();
        if (body) sents.push({ text: body, para: 0 });
        start = j;
      }
    }
    var tail = text.slice(start).trim();
    if (tail) sents.push({ text: tail, para: 0 });
    return sents;
  }

  function splitDocument(text) {
    var grouped = groupBlocks(lineUnits(text));
    var out = [];
    for (var pi = 0; pi < grouped.length; pi++) {
      var kind = grouped[pi][0], units = grouped[pi][1];
      var sents = [];
      if (kind === "para") {
        sents = splitSentences(units[0]);
      } else {
        /* 列表/表格：每个条目独立分句，无句末标点也成句 */
        for (var ui = 0; ui < units.length; ui++) {
          var parts = splitSentences(units[ui]);
          for (var k = 0; k < parts.length; k++) sents.push(parts[k]);
        }
      }
      for (var si = 0; si < sents.length; si++) sents[si].para = pi;
      out.push({ kind: kind, sents: sents });
    }
    return out;
  }

  /* ---------- 统计 ---------- */

  function mean(xs) {
    if (!xs.length) return NaN;
    var s = 0;
    for (var i = 0; i < xs.length; i++) s += xs[i];
    return s / xs.length;
  }

  function cv(xs) {
    if (xs.length < 3) return NaN;
    var m = mean(xs);
    if (!m) return NaN;
    var v = 0;
    for (var i = 0; i < xs.length; i++) v += (xs[i] - m) * (xs[i] - m);
    v = v / (xs.length - 1);
    return Math.sqrt(v) / m;
  }

  function tokenize(text) {
    var cps = Array.from(text.replace(PUNCT_RE, ""));
    if (cps.length < 2) return cps;
    var out = [];
    for (var i = 0; i < cps.length - 1; i++) out.push(cps[i] + cps[i + 1]);
    return out;
  }

  function mattr(tokens, window) {
    window = window || 100;
    if (!tokens.length) return NaN;
    if (tokens.length <= window) {
      var set = {};
      var uniq = 0;
      for (var i = 0; i < tokens.length; i++) {
        if (!set[tokens[i]]) { set[tokens[i]] = 1; uniq++; }
      }
      return uniq / tokens.length;
    }
    /* 滚动窗口 O(n)，与 Python 端同构：键计数减到 0 时对象仍留键，
       distinct 必须手工加减（等价于逐窗建 set，逐位一致） */
    var counts = {};
    var distinct = 0;
    for (var w = 0; w < window; w++) {
      if (!counts[tokens[w]]) { counts[tokens[w]] = 1; distinct++; }
      else counts[tokens[w]]++;
    }
    var vals = [distinct / window];
    for (var s = window; s < tokens.length; s++) {
      var outT = tokens[s - window];
      counts[outT]--;
      if (!counts[outT]) distinct--;
      var inT = tokens[s];
      if (!counts[inT]) distinct++;
      counts[inT] = (counts[inT] || 0) + 1;
      vals.push(distinct / window);
    }
    return mean(vals);
  }

  function fourGramRepeat(text) {
    var cps = Array.from(text.replace(PUNCT_RE, ""));
    if (cps.length < 8) return 0.0;
    var grams = {};
    var total = 0;
    for (var i = 0; i + 4 <= cps.length; i++) {
      var g = cps[i] + cps[i + 1] + cps[i + 2] + cps[i + 3];
      grams[g] = (grams[g] || 0) + 1;
      total++;
    }
    if (!total) return 0.0;
    var repeated = 0;
    for (var key in grams) if (grams[key] > 1) repeated += grams[key] - 1;
    return repeated / total;
  }

  var DENSITY_PREFIXES = ["L-CONN", "O-STK"]; /* 与 Python _DENSITY_PREFIXES 同步 */

  function connectiveLexicon(rules) {
    var lex = {};
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      var hitPrefix = false;
      for (var pi = 0; pi < DENSITY_PREFIXES.length; pi++) {
        if (r.id.indexOf(DENSITY_PREFIXES[pi]) === 0) { hitPrefix = true; break; }
      }
      if (!hitPrefix) continue;
      var pats = r.patterns || [];
      for (var j = 0; j < pats.length; j++) {
        var p = pats[j];
        if (p.length <= 8 && !/[\[\]\(\)\{\}\*\+\?\.\|\\]/.test(p)) lex[p] = 1;
      }
    }
    return Object.keys(lex);
  }

  function computeDocStats(blocks, lexicon) {
    var allSents = [];
    for (var p = 0; p < blocks.length; p++)
      for (var s = 0; s < blocks[p].sents.length; s++) allSents.push(blocks[p].sents[s].text);
    var lens = allSents.map(function (t) { return cpLength(t.replace(PUNCT_RE, "")); });
    var paraLens = blocks.map(function (block) {
      var n = 0;
      for (var i = 0; i < block.sents.length; i++) n += cpLength(block.sents[i].text.replace(PUNCT_RE, ""));
      return n;
    });
    var fullText = allSents.join("");
    var tokens = tokenize(fullText);
    var stats = {
      n_paragraphs: blocks.length,
      n_sentences: allSents.length,
      n_chars: lens.reduce(function (a, b) { return a + b; }, 0),
      sentence_cv: cv(lens),
      para_len_cv: cv(paraLens),
      ttr: mattr(tokens),
      conn_density: NaN,
      ngram_repeat: fourGramRepeat(fullText),
      avg_sentence_len: mean(lens),
      dash_density: NaN,
      tokenizer: "char-2gram",
    };
    if (lexicon && lexicon.length) {
      var count = 0;
      for (var si = 0; si < allSents.length; si++)
        for (var li = 0; li < lexicon.length; li++) {
          var word = lexicon[li], idx = 0, from = 0;
          while ((idx = allSents[si].indexOf(word, from)) >= 0) { count++; from = idx + word.length; }
        }
      stats.conn_density = allSents.length ? count / allSents.length : NaN;
    }
    /* 破折号计数与 Python 同口径：非重叠"——"对数 + 落单的"—"。
       连跑三个以上时按 run 拆（"———"= 1 对 + 1 单），不能数 run 数——
       数 run 数会让 "———" 在两端各算各的（一致性检查实证过的漂移） */
    var nDouble = 0, nSingle = 0, run = 0;
    for (var ci = 0; ci <= fullText.length; ci++) {
      if (fullText[ci] === "—") { run++; continue; }
      if (run) { nDouble += Math.floor(run / 2); nSingle += run % 2; run = 0; }
    }
    stats.dash_density = allSents.length ? (nDouble + nSingle) / allSents.length : NaN;
    return stats;
  }

  /* ---------- 综合评分（与 Python compute_score 同构） ---------- */

  /* 严重级 → 加权密度系数（与 Python _SCORE_WEIGHT / fit_score.py 同步） */
  var SCORE_WEIGHT = { high: 3.0, medium: 2.0, low: 1.0 };
  /* scoring 段里的元字段，不是特征 */
  var SCORING_META = { corpus: 1, auroc: 1, auroc_holdout: 1, human_p50: 1, human_p90: 1 };

  /* 按正文字数选系数组（与 Python _pick_scoring 同构）：最后一个满足
     min_chars ≤ n_chars 的层生效；元字段始终取全局 */
  function pickScoring(scoring, nChars) {
    var tiers = scoring.tiers;
    if (!tiers || !tiers.length) return scoring;
    var picked = {};
    for (var k in scoring) if (scoring.hasOwnProperty(k)) picked[k] = scoring[k];
    for (var i = 0; i < tiers.length; i++) {
      var t = tiers[i];
      if (t.min_chars == null || nChars >= t.min_chars) {
        picked = {};
        for (var k2 in scoring)
          if (scoring.hasOwnProperty(k2) && !SCORING_META[k2] && k2 !== "tiers")
            picked[k2] = scoring[k2];
        for (var k3 in t) if (t.hasOwnProperty(k3) && k3 !== "min_chars") picked[k3] = t[k3];
      }
    }
    delete picked.tiers;
    return picked;
  }

  /* 规则特征用未门控加权密度（共现门控是逐句指控的纪律，文档级聚合
     保留幅度信息更有效）；TTR 直接用 stats.ttr——全文唯一切分口径是
     字级 2-gram，与 Python 端逐位一致。短文本（<8 句）不出分。 */
  function computeScore(stats, weightedHits, scoring) {
    if (!scoring) return null;
    if (stats.n_sentences < 8) return null;
    var picked = pickScoring(scoring, stats.n_chars);
    var z = picked.intercept;
    var components = {};
    var values = {
      hit_density: stats.n_sentences ? weightedHits / stats.n_sentences : 0,
      sentence_cv: stats.sentence_cv,
      ttr: stats.ttr,
      ngram_repeat: stats.ngram_repeat,
      conn_density: stats.conn_density,
    };
    for (var feat in picked) {
      if (SCORING_META[feat] || !picked.hasOwnProperty(feat)) continue;
      var v = values[feat];
      if (typeof v !== "number" || isNaN(v)) continue;
      components[feat] = picked[feat] * v;
      z += components[feat];
    }
    z = Math.max(Math.min(z, 30), -30);
    return {
      index: 100 / (1 + Math.exp(-z)),
      components: components,
      corpus: String(scoring.corpus || ""),
      auroc: typeof scoring.auroc === "number" ? scoring.auroc : NaN,
      human_p50: scoring.human_p50 || 0,
      human_p90: scoring.human_p90 || 0,
    };
  }

  /* ---------- 域外文体（文言/诗行）：与 Python ood.py 同构，判据与阈值一字不差 ---------- */

  var OOD_STRONG = "乎哉兮矣焉欤俟汝尓乃遂皆曰";

  function detectOod(sents) {
    var clean = [];
    for (var i = 0; i < sents.length; i++) clean.push(sents[i].text.replace(PUNCT_RE, ""));
    var cps = Array.from(clean.join(""));
    var n = cps.length;
    if (n < 80) return [];
    var cnt = {};
    for (var j = 0; j < n; j++) {
      var ch = cps[j];
      cnt[ch] = (cnt[ch] || 0) + 1;
    }
    function ratio(set) {
      var k = 0;
      for (var x = 0; x < set.length; x++) k += cnt[set[x]] || 0;
      return k / n;
    }
    var de = ratio("的地得"), strong = ratio(OOD_STRONG);

    var kinds = [];
    if (de < 0.010 && strong >= 0.008 && (cnt["了"] || 0) / n < 0.006) kinds.push("classical");
    var bal = 0, lens = {};
    for (var s = 0; s < sents.length; s++) {
      var parts = sents[s].text.split(/[，、；]/).filter(function (p) { return p.trim(); });
      var ls = parts.map(function (p) { return cpLength(p.replace(PUNCT_RE, "")); });
      var ok = parts.length === 2;
      for (var li = 0; li < ls.length; li++) if (ls[li] < 5 || ls[li] > 9) ok = false;
      if (ok) {
        bal++;
        for (var lj = 0; lj < ls.length; lj++) lens[ls[lj]] = true;
      }
    }
    var nLens = 0;
    for (var L in lens) if (lens.hasOwnProperty(L)) nLens++;
    if (bal >= 4 && sents.length && bal / sents.length >= 0.60 && nLens === 1) kinds.push("verse");
    return kinds;
  }

  /* ---------- 段落热度：与 Python compute_para_heat 同构 ---------- */

  function computeParaHeat(doc, findings, hints) {
    var weighted = {};
    function acc(f) {
      if (f.para >= 0) weighted[f.para] = (weighted[f.para] || 0) + (SCORE_WEIGHT[f.severity] || 1.0);
    }
    findings.forEach(acc);
    hints.forEach(acc);
    var heat = [];
    for (var pi = 0; pi < doc.length; pi++) {
      var n = doc[pi].sents.length;
      if (!n || weighted[pi] === undefined) continue;
      /* density 全精度：round 的半值行为两端不同（banker's vs half-up）；
         excerpt 按码点截 16 字（与 Py text[:16] 一致，防代理对拆开） */
      var density = weighted[pi] / n;
      var level = density >= 1.0 ? "high" : (density >= 0.5 ? "medium" : "low");
      var excerpt = Array.from(doc[pi].sents[0].text).slice(0, 16).join("");
      heat.push({ para: pi, n_sents: n, density: density, level: level, excerpt: excerpt });
    }
    heat.sort(function (a, b) { return b.density - a.density; });
    return heat;
  }

  /* ---------- 引擎 ---------- */

  /* 编译结果按规则数组引用缓存（网页端每次按键都调 analyze，
     同一份 RULES_BY_PROFILE 反复 new RegExp 纯属浪费；WeakMap 不阻止 GC） */
  var _compiled = typeof WeakMap !== "undefined" ? new WeakMap() : null;

  function compileRules(rules) {
    if (_compiled) {
      var cached = _compiled.get(rules);
      if (cached) return cached;
    }
    var out = rules.map(function (r) {
      var o = {};
      for (var k in r) o[k] = r[k];
      o._patterns = (r.patterns || []).map(function (p) {
        return new RegExp(p);
      });
      o.doc_threshold = r.doc_threshold == null ? NaN : r.doc_threshold;
      o.doc_tiers = (r.doc_tiers || []).map(function (t) {
        return [t[0] == null ? null : t[0], t[1]];
      });
      o.min_sentences = r.min_sentences == null ? 8 : r.min_sentences;
      return o;
    });
    if (_compiled) _compiled.set(rules, out);
    return out;
  }

  function analyze(text, rules, scoring) {
    rules = compileRules(rules);
    var doc = splitDocument(text);
    var findings = [], hints = [];
    var raw = {};
    var weightedHits = 0;

    function push(rule, f) {
      if (!raw[rule.id]) raw[rule.id] = [];
      raw[rule.id].push(f);
      weightedHits += SCORE_WEIGHT[f.severity] || 1.0;
    }

    for (var pi = 0; pi < doc.length; pi++) {
      var block = doc[pi];
      for (var si = 0; si < block.sents.length; si++) {
        var sent = block.sents[si];
        for (var ri = 0; ri < rules.length; ri++) {
          var rule = rules[ri];
          if (rule.scope !== "sentence") continue;
          var matches = [];
          for (var mi = 0; mi < rule._patterns.length; mi++) {
            var m = rule._patterns[mi].exec(sent.text);
            if (m) matches.push(m[0]);
          }
          if (matches.length) {
            push(rule, {
              rule_id: rule.id, rule_name: rule.name, severity: rule.severity,
              tier: rule.tier, para: pi, sentence: sent.text, matches: matches,
              explanation: rule.explanation || "", suggestion: rule.suggestion || "",
              taste: rule.taste || "",
            });
          }
        }
      }
      /* 独句总结段只看普通段：bullet/表格行天然又短又独立 */
      if (block.kind !== "para") continue;
      var para = block.sents;
      for (var ri2 = 0; ri2 < rules.length; ri2++) {
        var rule2 = rules[ri2];
        if (rule2.scope !== "shape") continue;
        if (rule2.doc_metric === "one_liner" && para.length === 1) {
          var sentLen = cpLength(para[0].text);
          if (sentLen > 40) continue;
          push(rule2, {
            rule_id: rule2.id, rule_name: rule2.name, severity: rule2.severity,
            tier: rule2.tier, para: pi, sentence: para[0].text,
            matches: ["独句段（" + sentLen + " 字）"],
            explanation: rule2.explanation || "", suggestion: rule2.suggestion || "",
            taste: rule2.taste || "",
          });
        }
      }
    }

    for (var rid in raw) {
      var hits = raw[rid];
      if (hits[0].severity === "low" && hits.length < 2) {
        hints = hints.concat(hits);
      } else {
        findings = findings.concat(hits);
      }
    }

    var stats = computeDocStats(
      doc, connectiveLexicon(rules)
    );
    for (var di = 0; di < rules.length; di++) {
      var drule = rules[di];
      if (drule.scope !== "doc" || !drule.doc_metric) continue;
      var value = stats[drule.doc_metric];
      if (typeof value !== "number" || isNaN(value)) continue;
      if (stats.n_sentences < drule.min_sentences) continue; /* 短文本统计不判 */
      /* 分档阈值：与 Python _doc_threshold 同构——按 n_chars 依次匹配
         chars<上限，未命中用兜底阈值 */
      var thr = drule.doc_threshold;
      for (var ti = 0; ti < drule.doc_tiers.length; ti++) {
        var lim = drule.doc_tiers[ti][0];
        if (lim === null || stats.n_chars < lim) { thr = drule.doc_tiers[ti][1]; break; }
      }
      var hit = drule.doc_compare === "below" ? value < thr : value > thr;
      if (hit) {
        findings.push({
          rule_id: drule.id, rule_name: drule.name, severity: drule.severity,
          tier: drule.tier, para: -1, sentence: "",
          matches: [drule.doc_metric + "=" + value.toFixed(3) + "（阈值 " + thr.toFixed(2) + "）"],
          explanation: drule.explanation || "", suggestion: drule.suggestion || "",
          taste: drule.taste || "",
        });
      }
    }

    var SEV = { high: 3, medium: 2, low: 1, hint: 0 };
    findings.sort(function (a, b) {
      return (SEV[b.severity] - SEV[a.severity]) || (a.para - b.para);
    });
    /* 域外文体：全文一遍 + 逐段一遍聚合（白话引用文言段时全文统计被
       稀释，逐段能抓到；与 Python analyze 同构） */
    var allSents = [];
    for (var oi = 0; oi < doc.length; oi++) {
      for (var oj = 0; oj < doc[oi].sents.length; oj++) allSents.push(doc[oi].sents[oj]);
    }
    var oodKinds = detectOod(allSents);
    for (var ok = 0; ok < doc.length; ok++) {
      var paraSents = doc[ok].sents;
      var paraKinds = detectOod(paraSents);
      for (var ok2 = 0; ok2 < paraKinds.length; ok2++) {
        if (oodKinds.indexOf(paraKinds[ok2]) < 0) oodKinds.push(paraKinds[ok2]);
      }
    }
    /* 够 8 句却没出分（该 profile 无 scoring 段）给一句原因；<8 句保持空 */
    var scoreNote = (!scoring && stats.n_sentences >= 8) ? "该文体未校准评分" : "";
    return { findings: findings, hints: hints, stats: stats,
             score: computeScore(stats, weightedHits, scoring || null),
             score_note: scoreNote, ood: oodKinds,
             para_heat: computeParaHeat(doc, findings, hints) };
  }

  return {
    analyze: analyze,
  };
});

  return module.exports;
})();
const HvARewrite = (function () {
  const module = { exports: {} };
  /* human-vs-ai 浏览器改写器——与 human_vs_ai/rewrite.py 同构的第二实现。
 *
 * 存在理由：网页版要在浏览器里直接给改写建议（零后端）。
 * 纪律：与 Python 端逐字段一致，由 tools/check_web_consistency.py 的
 * rewrite 探针守护（候选、方向、动作、口味编号全部对比）。
 *
 * 最高优先准则（R1，docs/taste_zhouao.md）：不是一味的删减，而是该多说时
 * 多说，该少说时少说，重要数据和结论要保留——所以含数字/结论的句子先进
 * 保留档，腔调规则不覆盖它。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HvARewrite = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var KEEP = "保留", REWRITE = "改", DELETE = "删";

  /* R1 重要内容判据（不含中文数词+量词——文案里的量词不是数据；
     数字显式列 ASCII+全角，与 Python 端字符类完全一致，JS 的 \d 不认全角） */
  var IMPORTANT_RE = /[0-9０-９]|%|％|[一二三四五六七八九十百千万]+(倍|万|亿|人天|分钟)|(结论|结果表明|数据显示|实测|验证|复现|报错|错误码|失败率|通过率|达标|未达标)/;
  /* R3 具体名词 / 梗 */
  var NOUN_RE = /(DDL|deadline|组会|参考文献|文献|论文|paper|Paper|accept|数据|导师|大佬|咖啡|午饭|午休|书桌|台灯|日历|邮件|报错|日志|版本|分支|草稿|推文|稿子)/;
  var MEME_RE = /(牛马|摸鱼|摆烂|连滚带爬|火葬场|狠人|卷王|躺平)/;
  /* 整句关怀腔：没有事实半句可留，截前半句仍是安慰——整句删，不截断 */
  var COMFORT_WHOLE_RE = /(你已经(很|够|超|挺|那么|这么)|你值得|好好(爱|善待|心疼)?自己|照顾好?自己|爱惜自己|犒劳(好)?自己|不要给自己.{0,4}(压力|负担)|(会一直|一直|永远)陪(着|在)?你|一切都会(好|过去))/;
  /* 空铺垫半句：截出来没有信息量 */
  var EMPTY_HEAD_RE = /^(无论|不管).{0,8}(如何|怎样|与否)$|^.{0,6}的日子里$/;

  function stripTrailingParen(text) {
    return text.replace(/[（(][^）)]*[）)]\s*$/, "").trim();
  }

  function splitHeads(body) {
    return body.split(/[，,]/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  /* 规则化改写：返回 [候选, 方向, 驱动规则 id] */
  function mechanicalCandidate(text, ruleIds) {
    var body = stripTrailingParen(text.replace(/[。！？～]+$/, "")).trim();
    var heads = splitHeads(body);
    var has = function (id) { return ruleIds.indexOf(id) >= 0; };

    var manual = null;
    for (var i = 0; i < ruleIds.length; i++) {
      if (ruleIds[i].indexOf("T-MANUAL") === 0) { manual = ruleIds[i]; break; }
    }
    if (manual) return ["", "整句删（本人口径：功能说明不保留、不压缩）", manual];
    if (has("T-VOICE-05")) return ["", "整句删；要表达关心就一句话，别加理由", "T-VOICE-05"];
    if (has("T-VOICE-01")) {
      var multiSent = /[。！？；…!?;]/.test(body);
      if (COMFORT_WHOLE_RE.test(body)) return ["", "整句删；纯关怀没有事实可留", "T-VOICE-01"];
      if (heads.length >= 2 && !EMPTY_HEAD_RE.test(heads[0])) {
        /* 多句段落不做截半句候选——那会把整段毁成第一个逗号前的碎片 */
        if (!multiSent) return [heads[0] + "。", "", "T-VOICE-01"];
        return ["", "删掉逗号后的劝慰半句，只留前半段事实", "T-VOICE-01"];
      }
      if (heads.length >= 2) return ["", "整句删；前半句是空铺垫", "T-VOICE-01"];
    }
    if (has("T-VOICE-02") && heads.length >= 2) {
      if (!/[。！？；…!?;]/.test(body)) return [heads[0] + "。", "", "T-VOICE-02"];
      return ["", "删升华半句，保留动作和事实", "T-VOICE-02"];
    }
    if (has("T-VOICE-07")) {
      var m = body.match(/^[^，,]{0,10}的你[，,]\s*(.+)$/);
      if (m && !/[。！？；…!?;]/.test(m[1])) return [m[1].trim() + "。", "", "T-VOICE-07"];
    }
    if (has("T-VOICE-11")) {
      /* 收束词可能出现在行中（多句段落）——全文移除，不是只看行首 */
      var stripped = body.replace(/(综上所述|总而言之|总的来说|由此可见|不得不说)[，,]?\s*/g, "");
      if (stripped.trim()) return [stripped + "。", "", "T-VOICE-11"];
      return ["", "整句删；只剩收束词没有结论", "T-VOICE-11"];
    }
    var slogan = ["T-VOICE-10", "T-VOICE-12", "T-VOICE-04"];
    for (var j = 0; j < slogan.length; j++) {
      if (has(slogan[j])) return ["", "整句删（口号/顺口溜/效率承诺没有信息量）", slogan[j]];
    }
    var hint = "删掉腔调半句，只留事实";
    if (!NOUN_RE.test(body) && !MEME_RE.test(body)) {
      hint += "；再补一个具体名词或梗来承载信息";
    }
    return ["", hint, ruleIds.length ? ruleIds[0] : ""];
  }

  /* 编译缓存：改写模式和检测模式一样逐键触发，同一份规则数组
     反复 new RegExp 是纯浪费（WeakMap 按引用缓存，不阻止 GC） */
  var _compiled = typeof WeakMap !== "undefined" ? new WeakMap() : null;

  function compile(rules) {
    if (_compiled) {
      var hit = _compiled.get(rules);
      if (hit) return hit;
    }
    var out = rules.map(function (r) {
      return {
        id: r.id, name: r.name, severity: r.severity, taste: r.taste || "",
        explanation: r.explanation || "", scope: r.scope,
        _patterns: (r.patterns || []).map(function (p) { return new RegExp(p); }),
      };
    });
    if (_compiled) _compiled.set(rules, out);
    return out;
  }

  function classifyLine(text, rules) {
    var line = String(text).trim();
    if (!line) return { text: text, action: KEEP, taste: [], rules: [], reason: "",
                        candidate: "", direction: "" };
    var hits = [];
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      if (r.scope !== "sentence") continue;
      for (var j = 0; j < r._patterns.length; j++) {
        if (r._patterns[j].test(line)) { hits.push(r); break; }
      }
    }

    if (IMPORTANT_RE.test(line)) {
      return {
        text: line, action: KEEP,
        taste: hits.map(function (h) { return h.taste; }).filter(Boolean),
        rules: hits.map(function (h) { return h.id; }),
        reason: "含数据/结论，保留",
        candidate: "", direction: "",
      };
    }
    if (!hits.length) {
      /* R3 只作提示不作判据：保留档本身不给理由，只在可能是文案时给一条
         轻提示——与 Python 端同口径（一致性探针守护）。 */
      if (NOUN_RE.test(line) || MEME_RE.test(line)) {
        return { text: line, action: KEEP, taste: [], rules: [],
                 reason: "", candidate: "", direction: "" };
      }
      return { text: line, action: KEEP, taste: [], rules: [],
               reason: "", candidate: "",
               direction: "若是文案，可补一个具体名词或梗" };
    }

    var ids = hits.map(function (h) { return h.id; });
    var tastes = hits.map(function (h) { return h.taste; }).filter(Boolean);
    var res = mechanicalCandidate(line, ids);
    var cand = res[0], direction = res[1], driver = res[2];
    /* 候选与原文等值＝没改：不许把原句当"改写建议"还给用户（与 Python 端同护栏） */
    if (cand && cand.replace(/[。！？～\s]/g, "") === line.replace(/[。！？～\s]/g, "")) {
      cand = "";
      if (!direction) direction = "删掉腔调半句，只留事实";
    }
    var manual = false;
    for (var k = 0; k < ids.length; k++) if (ids[k].indexOf("T-MANUAL") === 0) manual = true;
    var action;
    if (manual || (!cand && direction.indexOf("整句删") === 0)) action = DELETE;
    else if (cand) action = REWRITE;
    else {
      /* 码点长度：与 Python len 同口径（emoji/扩展区汉字一个算一个） */
      var cps = Array.from(stripTrailingParen(line));
      var hasSubstance = cps.length > 14;
      action = hasSubstance ? REWRITE : DELETE;
    }
    var top = null;
    for (var t = 0; t < hits.length; t++) if (hits[t].id === driver) { top = hits[t]; break; }
    if (!top) {
      var SEV = { high: 3, medium: 2, low: 1, hint: 0 };
      top = hits[0];
      for (var u = 1; u < hits.length; u++) {
        if ((SEV[hits[u].severity] || 0) > (SEV[top.severity] || 0)) top = hits[u];
      }
    }
    return {
      text: line, action: action, taste: tastes, rules: ids,
      reason: top.explanation.trim().split("。")[0] + "。",
      candidate: cand, direction: direction,
    };
  }

  var LEAD_MARKER_RE = /^\s*(?:[-*+]\s+|\d+[.、)](?=\s|\D))\s*/;

  function rewriteText(text, rules) {
    var compiled = compile(rules);
    var advices = [];
    /* 行边界集与 Python splitlines 对齐（engine.js LINE_BREAK_RE 同步） */
    var lines = String(text).split(/(?:\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029])/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim().replace(LEAD_MARKER_RE, "");
      if (!line) continue;
      advices.push(classifyLine(line, compiled));
    }
    return { advices: advices };
  }

  return { rewriteText: rewriteText };
});

  return module.exports;
})();
const HvARender = (function () {
  const module = { exports: {} };
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
    return `<div class="row score">AI 味指数 <span class="comp">—（${note}）</span></div>`;
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

  /* 域外文体提示：文言/诗行超出评测语料域，指数系统性虚高（与引擎 ood 同行） */
  var OOD_NAME = { classical: "文言", verse: "等长对句诗行" };
  function oodHtml(ood) {
    if (!ood || !ood.length) return "";
    const names = ood.map(k => OOD_NAME[k] || k).join("、");
    return `<div class="row ood-note">文体域外（${esc(names)}）：指数仅供参考</div>`;
  }

  /* 段落热度：混写文本里全篇一个分数必然失真，指出"哪几段最像 AI"。
     只列前 3 段（按密度降序，引擎已排）；无命中的段不出现。
     项上带 data-para/data-excerpt，交互端可监听点击在原稿中定位该段 */
  function paraHeatHtml(result) {
    const heat = ((result && result.para_heat) || []).slice(0, 3);
    if (!heat.length) return "";
    const items = heat.map(h =>
      `<span class="ph ph-${esc(h.level)}" data-para="${h.para}"` +
      ` data-excerpt="${esc(h.excerpt || "")}" role="button" title="点击在原稿中定位">¶${h.para + 1} <b class="mono-num">${h.density.toFixed(2)}</b></span>`
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

  return {
    esc: esc, fmt: fmt, hiSentence: hiSentence,
    componentsText: componentsText,
    sealHtml: sealHtml, scoreNoteRow: scoreNoteRow,
    oodHtml: oodHtml, paraHeatHtml: paraHeatHtml,
    hintsHtml: hintsHtml,
    SEV_NAME: SEV_NAME, PROFILE_META: PROFILE_META,
    HINTS_MAX: HINTS_MAX, DISCLAIMER: DISCLAIMER, ADVICE_FOOTER: ADVICE_FOOTER,
  };
});

  return module.exports;
})();
const RULES = {"academic": [{"id": "L-CONN-01", "name": "模板连接词", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["首先", "其次", "再者", "此外", "与此同时", "不仅如此", "值得注意的是", "值得一提的是", "由此可见", "不难看出", "综上所述", "总而言之", "一言以蔽之", "换言之", "更重要的是", "尤为重要的是", "众所周知"], "explanation": "这批词本身没有错，问题是密度：AI 把逻辑衔接当作每段必备的仪式，人类只在真需要转折/递进时才用。社科院语言所把“'首先其次总之'式虚假逻辑衔接”列为中文 AI 味的核心特征之一——衔接词在，但衔接的内容之间并没有真正的推导关系。单次出现只是提示，本文是否“衔接词病”看全文密度统计（D-CONN-01）", "suggestion": "删掉读一遍——句子之间的逻辑如果本来就通，连接词就是赘余；如果删掉就不通，说明缺的是论证不是连接词。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-CONN-02", "name": "首先…其次固定套路", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["首先[，,].{2,60}[。；;]?\\s*其次", "^首先[，,]"], "explanation": "“首先…其次…”是 AI 组织段落的默认骨架，也是维基清单与中文社区鉴定共识的重叠区。真人学术写作更常用“（1）（2）”编号或直接分节，因为编号便于审稿人引用；散落的“首先其次”反而难定位", "suggestion": "换成编号列表或小节标题，或者干脆按实验/论证的自然顺序直陈。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-INFL-01", "name": "意义拔高", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["标志着", "里程碑", "分水岭", "划时代", "历史性(突破|意义|时刻)", "具有重要(的)?(意义|价值|作用)", "具有深远(的)?(影响|意义)", "扮演着.{0,8}(关键|重要)", "发挥着.{0,8}(重要|关键)(的)?(作用|角色)", "揭示了.{0,16}(本质|规律|机制)", "谱写.{0,6}新篇章"], "explanation": "把一个具体结果包装成历史节点、范式转移或“重要意义”，是维基清单第一条（对意义/遗产的过度拔高）的中文变体：陈述一个事实，再挂一句它“标志着/揭示了/为…提供了新思路”。拔高句不增加任何信息——删掉后论文一个字不少，审稿人却少了一个皱眉的理由。「为…提供新思路」在医学摘要结尾是体裁常规，归 L-TAIL-01 按 medium 提示；本条只留人类极少刻意为之的标志性拔高", "suggestion": "只留事实和数字。“为…提供了新思路”改为具体说明新在哪：跟哪个已知方法的差别是什么。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-COPO-01", "name": "无证据强化词", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["显著(提升|提高|增强|改善|降低|减少|优于)", "有效(提升|提高|改善|解决|缓解|避免|抑制|降低)", "充分(利用|发挥|证明|表明|说明|体现|考虑)", "深入(探讨|分析|研究|剖析|挖掘|讨论)", "全面(分析|阐述|梳理|揭示|总结|考察|评估)", "极大(地)?(提升|提高|丰富|拓展|促进)", "在一定程度上(促进|推动|改善|提升|缓解)"], "explanation": "“显著/有效/充分/深入/全面”这类强化副词本该有统计检验或对照数据背书。AI 的用法是系统性地、无证据地撒——“显著提升”旁边没有一个 p 值。学术规则库里区分度最高的一条（C-ReD：AI 命中率 48% vs 真人 9%）；在论文里它还有实际代价：审稿人会追问“显著到什么程度”", "suggestion": "每个强化词二选一：给出证据（数字、检验、对照），或删掉降为中性陈述。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-FORM-01", "name": "公式化开头", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["随着.{2,24}的(快速|不断|迅速|日益|持续)?(发展|进步|推进|深入|普及|提高)", "随着科技(的)?发展", "在当今(这个)?(快速发展的|信息化|数字化|全球化的?)?时代", "在.{2,12}的(背景|大背景|浪潮)下", "近年来[，,].{0,40}(受到|获得|引起)(了)?(广泛|越来越多|持续|极大)", "日新月异"], "explanation": "“随着…的快速发展”是中文 AI 生成文本的第一开场白——人大编码研究里 142 条被标记“AI 感”的小红书笔记中，模板开场是命中率最高的单项。它放在论文引言里的问题是双重的：既不提供文献综述信息，又立刻让评阅人想到“这段是 AI 写的”", "suggestion": "开头直接进研究缺口：谁的问题、卡在哪、本文接哪一棒。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-TAIL-01", "name": "公式化展望尾", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["有望(在|为|推动|应用|实现)", "(具有|蕴含|展现|展示)(了)?(广阔|巨大|重要|深远|良好)(的)?(应用|研究|发展|理论|市场)(前景|价值|潜力|意义)", "值得(进一步|深入)(研究|探讨|探索|关注)", "为.{2,20}(提供了|开辟了|指明了)(新的?|明确)?(思路|视角|方法|途径|范式|框架|方向|道路|路径|基础)", "奠定(了)?(坚实|重要|良好)(的)?基础", "(打下了|奠定)(了)?(坚实|良好|牢固)(的)?(基础|根基)"], "explanation": "对应维基清单的“大纲式挑战与展望”与 humanizer §13（Inflated significance）：在结论段把普通结果推向想象中的未来。中文 AI 的固定口径是“有望…”“具有广阔应用前景”“为…指明了方向”——三句可以原样贴到任何领域的任何论文末尾，这正是它们暴露 AI 的原因：展望与本文工作没有可核对的具体联系。“为…提供新思路”在真人医学摘要结尾同样高频，故按 medium 提示：说清新在哪，而不是直接判死", "suggestion": "展望只写有具体理由的方向：哪个已知限制准备怎么解决，或哪个结果暗示了哪条路。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-HYPE-01", "name": "政策腔大词", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["赋能", "(重要|主要)?抓手", "(形成|构建|打造).{0,6}闭环", "底层逻辑", "顶层设计", "(降维打击|护城河|组合拳|快车道|新引擎|定海神针|压舱石)", "深度融(合|入)", "有力(支撑|保障|助推)"], "explanation": "公文/自媒体的高热度词混进学术文本，是 AI 在错误语域生成的典型症状（它的训练语料里“赋能”高频出现在所有文体，所以顺手就用在论文里）。学术文本有术语体系，这类词既不是术语也没有操作性定义", "suggestion": "换成领域术语或具体动作。“AI 赋能实验教学”→“AI 用于预判滴定终点，减少 30% 重做率”。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-NEGA-01", "name": "否定式排比", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["不仅.{2,24}(，|、)?(而且|更是|还|甚至|也)", "不是.{2,24}(，|、)?而是", "并非.{2,24}(，|、)?(而是|只是)", "既.{2,16}(，|、)?又.{2,16}(，|、)?更"], "explanation": "“不仅仅是 X，更是 Y”在 humanizer 里排 §1（Not X but Y）：否定半句没有人主张过，正半句因此显得更宏大——加了分量，没加信息。维基清单与中英社区鉴 AI 攻略的三方共识，也是被玩到“PTSD”级别的句式。真人偶尔用它修正一个真实存在的误解，AI 用它给平淡内容镀金。C-ReD：AI 命中率 25% vs 真人 3%", "suggestion": "砍掉否定半句，直接说 Y；如果 Y 确实反驳了某个常见误解，把那个误解明确写出来。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-EVADE-01", "name": "系动词回避", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["扮演着", "充当.{0,10}(的)?(角色|作用|桥梁|媒介)", "作为.{2,16}(的)?(重要|关键|核心)(组成|载体|途径|手段|工具|环节)"], "explanation": "humanizer §18（Avoiding is/are/has）：AI 不喜欢“是/有”这种朴素动词，觉得太简单，非要换成“扮演/充当/占据着…地位”。实证上，英文学术文本 2023 年后 is/are 使用率 measurable 下降。中文对应是“该材料扮演着核心角色”而不是“该材料很关键”。C-ReD 复测：AI 命中率 4% vs 真人 0%——弱信号，靠共现机制兜底。“具有/占据…重要地位”归 L-INFL-01，避免同处双计", "suggestion": "换回“是/有/在…中很关键”。朴素的动词不是水平低，是自信。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-PAD-01", "name": "「进行X」填充句", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["进行(了|着)(全面|系统|深入|详细|细致|广泛)(的)?(分析|研究|探讨|阐述|论述|考察|梳理|总结)", "做出(了)?(重要|巨大|突出)(的)?贡献", "取得(了)?(显著|重要|长足)(的)?(进展|成效|成果|突破)", "开展(了)?(系统|深入|全面)(的)?(研究|分析|实验)"], "explanation": "“对 X 进行了深入分析”比“分析了 X”多七个字，少一分信息——动名词填充是中文学术 AI 味的高频特征。它源于模型的两个默认倾向：凑字数、以及把“做过什么”说得比实际更隆重", "suggestion": "动词直接上。“对三组样品进行了系统的表征分析”→“表征了三组样品”。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "S-OPEN-01", "name": "讲稿腔开场", "tier": "syntactic", "scope": "sentence", "severity": "high", "patterns": ["让我们(一起|共同|先|首先|来)", "接下来(，)?(我们)?(将|来)?(介绍|探讨|分析|看看)", "下面(将|我)?(介绍|分析|探讨|开始)", "本文将(带|带领)大家"], "explanation": "聊天助手的讲稿残留（维基清单 D 类：协作式对话残留）：模型习惯以“向听众做汇报”的口吻组织文本，“让我们”“接下来我们将”是幻灯片演讲腔，不是论文腔。论文的正文化约定是零舞台指示——直接陈述", "suggestion": "删掉舞台指示，让内容自己开场。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "S-CLOS-01", "name": "独句总结段", "tier": "structural", "scope": "shape", "severity": "low", "patterns": [], "explanation": "整段只有一句不超过 40 字的话，通常功能是“给上文盖章”——“由此可见，该方法可行。”维基清单与 humanizer §2（one-line closer）都把“复述式收束”列为高频 AI 迹象；段落形状层面它表现为散布的独句段。真人只在真正需要强调新事实时写独句段", "suggestion": "独句段承载新事实就并回上文；只是总结腔调就整段删掉。", "doc_metric": "one_liner", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "D-UNIF-01", "name": "句长节奏均匀", "tier": "statistical", "scope": "doc", "severity": "medium", "patterns": [], "explanation": "全文句长变异系数（标准差/均值）过低，即句子长度高度均匀。真人写作长短句交错（长句铺论据、短句下结论），即 burstiness——最难手洗的特征，C-ReD 中对四个模型一致有效。真人基线随文本变长上移，故阈值按长度三档（<300 字 0.30 / <600 字 0.33 / 更长 0.37），各档锚定约 10% 真人误报", "suggestion": "检查每段：论据句可以更长更密，结论句可以极短。把连续等长的句子合并或拆分。", "doc_metric": "sentence_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.37, "doc_tiers": [[300, 0.3], [600, 0.33], [null, 0.37]]}, {"id": "D-CONN-01", "name": "连接词密度过高", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "每句平均出现的模板连接词（L-CONN-01 词表）超过 0.08 条。社科院语言所描述的“虚假逻辑衔接”在统计上的样子：衔接词密度与论证密度脱钩——词在，推导不在。阈值按摘要语料从宽校准（完整论文正文的基线更高，后续按文体细分）", "suggestion": "按段清理：先删所有连接词读一遍，补不回逻辑的地方才补词。", "doc_metric": "conn_density", "doc_compare": "above", "taste": "", "doc_threshold": 0.08, "doc_tiers": []}, {"id": "D-PARA-01", "name": "段落长度一致", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "各段长度过于接近（段长变异系数 < 0.25）意味着段落是按固定模板切的，而不是按论证的自然单元——AI 生成文本倾向每段装差不多分量的内容。人类论文的段落犬牙交错：方法段长、转折段短", "suggestion": "按论证单元重新切段：一个主张一个段，主张弱段落就短，不用凑。", "doc_metric": "para_len_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.25, "doc_tiers": []}, {"id": "D-NGRAM-01", "name": "4-gram 高频复现", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "正文中重复出现的四字片段占比偏高。AI 的重复是短语级套路复用（“提供了新的思路”“发挥了重要作用”反复出现），人类的重复更多是术语复现（术语重复是精确性，不是套路）。此规则只报数值，具体复现了什么看词表规则的逐条命中", "suggestion": "找出重复 3 次以上的非术语短语，逐个替换或删除。", "doc_metric": "ngram_repeat", "doc_compare": "above", "taste": "", "doc_threshold": 0.15, "doc_tiers": []}, {"id": "D-DASH-01", "name": "破折号高频", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "平均每句超过 0.15 个破折号——破折号让作者免于说明两个分句的逻辑关系，AI 因此到处用（humanizer §8）。注意：单独几个破折号完全正常（很多作者有此习惯），这条只在密度异常时提示", "suggestion": "破折号换成逗号、冒号或括号——哪个准确用哪个，逼自己把分句关系想清楚。", "doc_metric": "dash_density", "doc_compare": "above", "taste": "", "doc_threshold": 0.15, "doc_tiers": []}], "essay": [{"id": "E-NEGA-01", "name": "否定式拔高", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["不是.{2,24}(，|、)?而是", "不仅.{2,24}(，|、)?(而且|更是|还|甚至|也)", "(不仅仅是?).{2,20}(更是|还是|更是对)"], "explanation": "“不是 X 而是 Y”“不仅 X 更是 Y”——用对仗替代论证，把具体事物拔高成抽象宣言。C-ReD composition 实测 AI 104/250 vs 真人 28/250（“不仅…更是”84 vs 9），是议论文文体区分度最强的词表单项，故在本库升至 high（academic 库中同类为 low 共现门控）", "suggestion": "每个“不是…而是”自问：X 和 Y 都是具体判断吗？拔高的那半句删掉，把另一半写实。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "E-SUB-01", "name": "升华套话", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["(谱写|书写|绘就).{0,8}(篇章|华章|画卷)", "(绽放|散发).{0,6}(光彩|光芒)", "(青春|奋斗)(的)?(风采|底色|模样)"], "explanation": "议论文结尾的通用升华件：拔出来能贴进任何题目的意象。C-ReD composition 实测“谱写…篇章”AI 20 篇 vs 真人 0 篇，“绽放…光彩”23 vs 4。真人高分作文收在具体判断上，不收在万能意象上", "suggestion": "把意象换成这道题特有的具体判断；换不出来说明结尾还没想清楚。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "E-FORM-01", "name": "公式化开头", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["随着.{2,24}的(快速|不断|迅速|日益|持续)?(发展|进步|推进|深入|普及|提高)", "在当今(这个)?(快速发展的|信息化|数字化|全球化的?)?时代", "在.{2,12}的(背景|大背景|浪潮)下", "在这个(信息爆炸|快节奏|数字化|互联网|发展迅速|日新月异)?(的)?时代", "日新月异"], "explanation": "与 academic/general 同源的开场模板族。C-ReD composition 实测“在这个…时代”AI 23 篇 vs 真人 1 篇——考场作文真人开头直接入题，生成模型先铺时代背景", "suggestion": "开头直接给观点或场景；时代背景与题目无关就整句删掉。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "E-CONN-01", "name": "模板连接词", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["首先", "其次", "再者", "此外", "与此同时", "不仅如此", "由此可见", "综上所述", "总而言之", "一言以蔽之", "更重要的是"], "explanation": "议论文的骨架词，真人也用——但 AI 每段都装。C-ReD composition 实测 AI 加权句均 1.63 vs 真人 1.13。单次出现不报，共现密度异常才提示", "suggestion": "骨架词让位给内容衔接：前后句有真关系就不需要“首先其次”。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "E-QUOTE-01", "name": "名言引入腔", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["正如.{2,12}(所说|所言|而言)", "有一句话(说得好|这样说道)", "古人(云|曰|说)", "习近平总书记曾指出"], "explanation": "“正如…所说”式的名言开场。真人高考作文也引名言，但 AI 把它当万能起手式（C-ReD 实测 AI 19 vs 真人 5）；引语若与后文论证无粘合，就是装饰品", "suggestion": "引语后必须接你自己的转译：这句话在本题里意味着什么。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-TRIAD-01", "name": "三连排比", "tier": "syntactic", "scope": "sentence", "severity": "low", "patterns": ["[\\u4e00-\\u9fa5]{2,8}[、][\\u4e00-\\u9fa5]{2,8}[、][\\u4e00-\\u9fa5]{2,8}(?!等)"], "explanation": "内容凑成三项一组。议论文文体 AI 仍偏高但差距温和（AI 86/250 vs 真人 66/250），与新闻文体相反（真人排比修养，反向），故只在 essay 库以 low 共现门控保留", "suggestion": "逐项检查：凑数的删掉，能展开的展开成句。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "D-UNIF-01", "name": "句长节奏均匀", "tier": "statistical", "scope": "doc", "severity": "medium", "patterns": [], "explanation": "真人议论文句长变异系数中位 0.59（AI 中位 0.39）——真人排比与短句起伏大。阈值取 0.40/0.42：真人 p10 之下、AI 中位之上", "suggestion": "论据句可以更长更密，判断句敢用短句；连续等长的排比句拆掉一半。", "doc_metric": "sentence_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.4, "doc_tiers": [[600, 0.42]]}], "general": [{"id": "G-OPEN-01", "name": "万能开场白", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["在这个(信息爆炸|快节奏|数字化|互联网|发展迅速|日新月异)?(的)?时代", "随着科技(的)?(发展|进步)", "随着社会(的)?(发展|进步)", "相信很多(人|朋友|读者)", "你有没有(发现|想过|遇到)", "说起.{2,12}(，)?相信", "不知道你有没有", "^(好的|没问题|当然)[，,!！]", "^(好的|没问题)[，,]?(关于|针对|就)", "以下是(关于|一些建议|针对|详细)", "针对(您|你)的(问题|情况|需求)"], "explanation": "自媒体/问答体的万能开场：先立一个谁都同意的大背景，再滑入正题。与学术版（L-FORM-01）同源，但词表不同——自媒体的开场更“亲昵”（“相信很多朋友”“你有没有想过”），学术论文里则不会这么说。人大编码研究中模板开场是“AI 感”笔记命中率最高的单项。2023 HC3 语料测不到这些表达（该代模型不这么说话），规则面向当代模型，依据为人大编码研究与社区共识", "suggestion": "开头直接给信息增量：一个具体事实、一个反常识结论、或一个真实场景。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-INTERACT-01", "name": "互动尾巴与聊天残留", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["欢迎在评论区", "(点赞|收藏|转发|关注)(加|\\+|、)?(收藏|转发|关注|走起)?", "希望这篇(文章|内容)?(对你)?(有所|有)?帮助", "对你有所?帮助", "希望能(帮助|帮到)(到)?(你|大家)", "如果(您|你)还有(其他|别的|任何)?(问题|疑问)", "(祝|祝愿)你(生活愉快|学习进步|工作顺利|使用愉快)", "以上就是(关于|本次|全部)?", "我们下期(再见|见)", "希望对你有所(启发|帮助)", "让我们(一起|共同)?(期待|学习|进步)", "如果你有(什么)?(问题|想法|不同意见)", "你们的(支持|点赞)就是"], "explanation": "维基清单 D 类（协作式对话残留）的自媒体形态：给读者的舞台指示和运营话术混在正文里。“以上就是…希望对你有帮助”既可以贴在任何一篇文章末尾，也可以出现在 AI 生成的任何回答末尾——正因为万能，才暴露生成痕迹。真人作者的收尾更多是内容的一部分（一个总结性观点、一个开放问题），而不是对读者的操作指令。2023 HC3 语料测不到这些表达，规则面向当代模型，依据为维基清单 D 类（协作式对话残留）", "suggestion": "尾巴要么是内容（收束观点、留一个真问题），要么删掉；运营话术放评论区置顶。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-HYPE-01", "name": "万金油热词", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["赋能", "底层逻辑", "顶层设计", "降维打击", "(形成|构建|打造|玩转).{0,4}闭环", "(重要|主要)?抓手", "天花板(级)?", "满满的?干货", "无脑(冲|选|入)", "吊打", "碾压(级)?", "断层(第一|领先)"], "explanation": "跨平台流浪的高热词：它们的语料密度集中在标题党和营销号文本里，AI 生成“科普”时顺手就撒。问题不在词新，而在它们替代了论证——“降维打击”四个字省掉的正是“到底好在哪”的解释", "suggestion": "每个热词替换成具体的比较或数字；替代不出来，说明这句话本来就没有信息。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-SAFE-01", "name": "保险腔对冲", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["不得不说", "不可否认", "毋庸置疑", "众所周知", "总的来说", "总体而言", "总之", "综上所述"], "explanation": "“不得不说/总的来说/总之”这类收束词的功能是宣布“下面这句不需要论证”。真人偶尔用，AI 系统性地用——HC3 医疗问答上“总之”在 AI 回答的出现率是真人回答的 17 倍（5.1% vs 0.3%）。单次出现只是提示，多处出现说明行文在靠保险腔推进", "suggestion": "删掉保险腔直接说结论；说不出口，说明论据不够，补论据而不是补对冲。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-TRIAD-01", "name": "三连排比", "tier": "syntactic", "scope": "sentence", "severity": "low", "patterns": ["[\\u4e00-\\u9fa5]{2,8}[、][\\u4e00-\\u9fa5]{2,8}[、][\\u4e00-\\u9fa5]{2,8}(?!等)"], "explanation": "humanizer §6（Forced triads）：内容凑成三项一组，为了“显得完整”而不是因为真有三条。HC3 校准中它是问答文体区分度最好的句式规则（AI 43% vs 人类 22%）；注意它在学术摘要里方向相反（真人方法列举），所以本规则只在 general 库", "suggestion": "逐项检查：能合并的合并，能展开的展开成句，凑数的删掉。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "G-CLOS-01", "name": "独句总结段", "tier": "structural", "scope": "shape", "severity": "low", "patterns": [], "explanation": "整段只有一句不超过 40 字的话，通常是“给上文盖章”——“这就是坚持的意义。”自媒体把它当节奏器用，AI 把它当默认收束。与 G-INTERACT-01 叠加出现时，“模板腔收尾”的信号很强", "suggestion": "独句段承载新事实就并回上文；只是总结腔调就整段删掉。", "doc_metric": "one_liner", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "D-UNIF-01", "name": "句长节奏均匀", "tier": "statistical", "scope": "doc", "severity": "medium", "patterns": [], "explanation": "全文句长的变异系数（标准差/均值）低于 0.35，说明句子长度高度均匀——“每句都差不多长”。问答文体的真人差距比学术更大（0.521 vs 0.337）：真人回答短句多、语气起伏大。跨文体最稳的单一指标", "suggestion": "检查每段：论据句可以更长更密，结论句可以极短。把连续等长的句子合并或拆分。", "doc_metric": "sentence_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.35, "doc_tiers": []}, {"id": "D-PARA-01", "name": "段落长度一致", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "各段长度过于接近（段长变异系数 < 0.25）意味着段落是按固定模板切的，而不是按内容自然分段", "suggestion": "按内容单元重新分段：一个意思一个段。", "doc_metric": "para_len_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.25, "doc_tiers": []}, {"id": "D-NGRAM-01", "name": "4-gram 高频复现", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "正文中重复出现的四字片段占比偏高。AI 的重复是短语级套路复用，人类的重复更多是专名与术语复现。只报数值供参照，具体复现了什么看词表规则的逐条命中", "suggestion": "找出重复 3 次以上的非术语短语，逐个替换或删除。", "doc_metric": "ngram_repeat", "doc_compare": "above", "taste": "", "doc_threshold": 0.15, "doc_tiers": []}], "news": [{"id": "N-INTERACT-01", "name": "互动尾巴混入报道", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["欢迎在评论区", "(点赞|收藏|转发|关注)(加|\\+|、)?(收藏|转发|关注|走起)?", "希望这篇(文章|内容)?(对你)?(有所|有)?帮助", "如果(您|你)还有(其他|别的|任何)?(问题|疑问)", "让我们(一起|共同)?(期待|学习|进步)", "你们的(支持|点赞)就是"], "explanation": "自媒体的运营话术混进了新闻报道：报道的事实停在正文，尾巴却在号召点赞关注。C-ReD news 域实测 AI 生成新闻 134/250 篇含互动话术，真人新闻 24/250——新闻体的第一暴露特征", "suggestion": "报道到事实为止；引导互动的话术整句删除。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "N-RECENT-01", "name": "模糊时间开场", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["近日[，,]?", "日前[，,]?", "最近[，,]?.{0,10}(引发|引起|传出)"], "explanation": "“近日/日前”是生成模型的时间占位符——真记者掌握信源和日期，会写“9 月 15 日”；写不出日期的，往往是没采访过。C-ReD news 实测 AI 101/250 vs 真人 13/250。单次出现不算病，高频或开场即用才提示", "suggestion": "换成可核实的具体日期或时间段；真不知道就写清获取时间。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "N-HEARSAY-01", "name": "含糊消息源", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["据悉", "(有关|相关)(负责人|人士|部门)表示", "有(专家|学者|网友)认为"], "explanation": "“据悉/有关人士表示”是无主信源——真正的报道会写“从市城管委获悉”“据项目负责人王某”。C-ReD news 实测 AI 43/250 vs 真人 20/250。注意“据报道/据了解”方向相反（真人 15 vs AI 5，真人消息源标注更规范），不收本条", "suggestion": "补信源：谁说的、向谁核实、什么场合说的；补不出来就删掉这句。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "N-NOTE-01", "name": "编辑部提示语", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["值得注意的是", "值得一提的是", "总体(来看|而言)", "总的来看"], "explanation": "“值得注意的是/总体来看”是分析性文体的衔接词，混在消息报道里就是评论腔入侵报道体。C-ReD news 实测 AI 10:0 与 7:0", "suggestion": "报道体只陈述事实与信源；分析判断另起评论稿。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-CONN-01", "name": "模板连接词", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["首先", "其次", "再者", "此外", "与此同时", "不仅如此", "由此可见", "综上所述", "总而言之"], "explanation": "报道体靠事实推进，不靠议论连接词推进。C-ReD news 实测 AI 命中率 1.24 vs 真人 1.14（加权句均），差距温和，故仅设 low 共现门控", "suggestion": "用事实的时序与因果自然衔接，删掉议论性过渡词。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "D-UNIF-01", "name": "句长节奏均匀", "tier": "statistical", "scope": "doc", "severity": "medium", "patterns": [], "explanation": "新闻真人句长变异系数中位 0.49（引语短句多、导语长句多），AI 生成新闻中位 0.34——消息体的“通稿腔”。阈值取 0.40：真人 p10 之下、AI 中位之上。300 字内过 8 句的样本太少，沿用 0.36 兜底", "suggestion": "导语与引语错开长度；把连续等长的过程句合并，引语单独成句。", "doc_metric": "sentence_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.4, "doc_tiers": [[300, 0.36]]}], "official": [{"id": "O-STK-01", "name": "强化副词堆叠", "tier": "lexical", "scope": "lexicon", "severity": "medium", "patterns": ["切实", "扎实", "进一步", "持续", "不断", "深入", "全面", "有力", "有效", "扎实推进", "大力", "着力"], "explanation": "“切实/扎实/进一步/不断”是公文的基本工作动词修饰，真人篇篇在用，单次出现绝无问题。AI 的问题是密度：真人公文里它们集中在工作要求部分，AI 则均匀撒满全篇——每段都有三五个。是否“副词病”看全文密度（D-STKD），本规则只负责逐句定位供 D-STKD 计数", "suggestion": "密度过高时逐段清理：保留真正需要强调的动词修饰，其余直陈动作。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "O-INFL-01", "name": "空洞拔高", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["迈上.{0,4}新台阶", "谱写.{0,6}新篇章", "取得.{0,4}历史性(成就|突破|变革)", "站在新的?历史起点", "(重要|重大)而深远(的)?意义", "具有重要(的)?(意义|价值|作用)", "为.{2,16}打下(了)?坚实基础", "注入(了)?强劲动力", "提供(了)?(坚强|有力)(的)?保障", "凝聚(了)?强大合力", "展现(了|出)?新的?(气象|作为|担当)"], "explanation": "拔高套话在真人公文中也偶有出现（总结报告体），但 AI 把它当默认收束件：每个工作领域配一句“为…打下坚实基础”“注入强劲动力”。这类句子删除后文件一个字不少。真人密度约每千字 0-1 处，AI 密度数倍于此。首轮按 medium 校准，真人误报超标则降级或收窄词表", "suggestion": "删掉收束套话；确需表态的部分给出可考核的具体目标。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "O-PARA-01", "name": "递进排比堆砌", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["更[高大强快严实优][、和与]?更[高大强快严实优]", "以更高.{2,24}更[严实快]", "新的?[、,].{0,2}新的?", "(获得|幸福感|满意度)[、和](幸福|获得|安全感)感?[、和](安全感|幸福感)"], "explanation": "“更高标准、更严要求、更实举措”式递进排比：把一个要求拆成三个形容词变体，形式递进而内容不递增。真人公文在标题对仗时也用，但 AI 把它当万能修辞，正文每段来一组。判别：排比的每一项能否落到不同的具体措施上——落不到就是装饰", "suggestion": "三个变体保留信息量最大的那个，落到具体措施；其余删除。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "O-TAIL-01", "name": "演讲腔收尾", "tier": "lexical", "scope": "sentence", "severity": "high", "patterns": ["相信在.{2,16}的(共同|携手)努力下", "让我们(以更加|携手|共同|以)", "(使命|任务)光荣、?责任重大", "奋力开创新局面", "奋力谱写", "以优异成绩"], "explanation": "动员演讲腔混进部署文件：真人通知/意见的收尾是“特此通知”或最后一条具体要求，不会有面向听众的号召——号召是领导讲话稿的文体，写进通知反而是 AI 把“政府文体”混成一锅的典型症状（训练语料里讲话稿与通知高度混杂）。中英社区鉴 AI 共识 + 维基清单 D 类的公文变体", "suggestion": "通知以要求或“特此通知”收尾；确需动员表述，那是讲话稿的事，换文体写。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "O-EXCL-01", "name": "公文感叹号", "tier": "syntactic", "scope": "sentence", "severity": "high", "patterns": ["[!！]"], "explanation": "真人公文正文（通知/意见/方案）几乎不用感叹号——感叹是情绪符号，公文靠职级与效力生效，不靠语气。AI 生成的“公文”常带演讲式感叹（“…而奋斗！”），一出现即异常。法规办法中的“！”同样异常", "suggestion": "感叹号改句号；语气强度不该由标点承担。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "D-STKD-01", "name": "强化副词密度过高", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "每句平均出现的强化副词（O-STK-01 词表）超过 0.55 条。真人公文的强化副词集中在部署要求段落，AI 则均匀铺满——“每段都有三个进一步”是 AI 公文的直观印象在统计上的样子。阈值首轮按真人公文分布实测校准", "suggestion": "按“动词 + 宾语”直陈改写，强化词只留给真正需要加压的要求句。", "doc_metric": "conn_density", "doc_compare": "above", "taste": "", "doc_threshold": 0.55, "doc_tiers": []}, {"id": "D-PARA-01", "name": "段落长度一致", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "各段长度过于接近（段长变异系数 < 0.25）意味着段落是按固定模板切的。注意公文的条款结构天然带来一定均匀性，此阈值按公文真人语料实测后放宽或收紧", "suggestion": "按内容单元重新分段：一个要求一个条，要求多寡决定段落长短。", "doc_metric": "para_len_cv", "doc_compare": "below", "taste": "", "doc_threshold": 0.25, "doc_tiers": []}, {"id": "D-DASH-01", "name": "破折号高频", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "平均每句超过 0.15 个破折号。公文正文用括号或“即”引出解释，破折号是行文散文化的信号——AI 把散文化的标点习惯带进了公文", "suggestion": "破折号换成括号或“即”。", "doc_metric": "dash_density", "doc_compare": "above", "taste": "", "doc_threshold": 0.15, "doc_tiers": []}], "personal": [{"id": "T-VOICE-01", "name": "劝慰腔", "tier": "syntactic", "scope": "sentence", "severity": "high", "patterns": ["[，,].{0,14}(跑不了|还在原地|不丢人|不算熬夜|是正常|别硬撑|又不会跑|不会跑)", "[，,——]{1,2}(正常|没关系|没事的)[。！]?$", "(谁也躲不过|躲不过|都这样|都会过去|一切都会(好起来|变好|过去|好的))[。！]?$", "(别急|别慌|慢慢来)[，,].{0,12}(还|总|会|能)", "你已经(很|够|超|挺|那么|这么).{0,3}(棒|好|努力|优秀|坚强|辛苦|了不起)", "不要给自己(太大|太多|过多)?(压力|负担)", "好好(爱|善待|心疼)?自己", "你值得(拥有|被爱|世间|这世界|一切|最好|所有)", "(会一直|一直|永远)陪(着|在)?你", "(请)?记得?(好好)?(照顾|爱惜|犒劳)好?自己"], "explanation": "结构是「陈述状况 + 给个台阶」：替读者做情绪管理，但不给信息。文案池里被毙最多的句式（首轮 15 条被毙稿中 3 条），定稿 37 条为 0。判别看逗号/破折号后那半句：是借口或宽慰，不是新信息。另一大类是通用 LLM 关怀腔（“你已经很棒了”“好好爱自己”“我会一直陪着你”“请记得照顾好自己”）——整句都是情绪劳动、没有事实半句可留，整句删，不做半句截断", "suggestion": "删。真要缓解情绪，用一个梗或自嘲，别用安慰。", "doc_metric": "", "doc_compare": "", "taste": "T1", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-02", "name": "抒情升华", "tier": "syntactic", "scope": "sentence", "severity": "high", "patterns": ["[，,](就是|才是|是)(最好的|你的|一场|偷来的|属于)", "陪你|伴你|照亮你的", "[，,][^，,]{0,4}越[^，,]{1,8}"], "explanation": "把日常动作升格成有意义的意象（“窗口一开，就是你的战场”）：不增加信息，只增加修辞密度。定稿 37 条中 0 条含此结构，而被毙稿里它是稳定的默认收尾方式。也包含「越…越…」式对偶抒情", "suggestion": "删升华半句，只留动作本身。", "doc_metric": "", "doc_compare": "", "taste": "T2", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-03", "name": "说理解释腔", "tier": "syntactic", "scope": "sentence", "severity": "medium", "patterns": ["不是.{0,8}(问题|错|摆烂|态度|罪|偷懒|偷闲)", "(是科学|是生理学|不心虚|才怪)"], "explanation": "给一个不需要辩护的行为做辩护（“发呆不是偷懒，是给大脑整理碎片”）。在界面文案里它通常是“我为什么这么设计”的自白，本质是把设计者的问题写进用户界面。文案池：被毙稿 10%，定稿 0%", "suggestion": "删。设计理由写在设计文档里，不写在界面上。", "doc_metric": "", "doc_compare": "", "taste": "T3", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-04", "name": "效率承诺", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["(效率|产出)(翻倍|提升|暴涨)", "(十倍|翻番).{0,4}(效率|速度)"], "explanation": "无法验证的收益承诺，与“功能说明腔”同源：都在向用户说明效果而不是给用户结果。被毙稿中出现一次，定稿零", "suggestion": "删；确有收益就用具体数字与条件说明。", "doc_metric": "", "doc_compare": "", "taste": "T4", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-05", "name": "劝诫长句", "tier": "syntactic", "scope": "sentence", "severity": "medium", "patterns": [".{2,}[，,].{0,10}(早点睡|早点休息|注意身体|保重身体|照顾好身体)", "(别|不要)(熬太晚|熬夜|硬撑|逞强).{0,12}[，,].{1,20}", "(记得|要)(喝水|吃饭|休息)[，,]"], "explanation": "对用户下健康指令。判别的是结构不是题材：一句话的短问候在定稿里存活，“铺垫+祈使+理由”的长劝诫全被毙——关心若需要理由才成立，那个理由就是在替读者做决定", "suggestion": "删或压到最短；一句话的关心可以留，带理由的劝诫要删。", "doc_metric": "", "doc_compare": "", "taste": "T5", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-06", "name": "夸张赞美", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["(太阳|月亮|星星|别人)还没你", "比(月亮|太阳|谁都)还?", "(最棒|最强|最努力|最优秀)的?你"], "explanation": "把读者往高处架。本人的口味方向是吐槽与自嘲，不是捧——文案池里“赞美读者”的句子被毙，定稿里取而代之的是“把你写成搞笑角色”的反讽", "suggestion": "改成吐槽或自嘲；要夸就夸具体动作。", "doc_metric": "", "doc_compare": "", "taste": "T6", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-07", "name": "把你句式", "tier": "syntactic", "scope": "sentence", "severity": "high", "patterns": ["^[^，,]{0,10}的你[，,]", "(凌晨|深夜|清晨|此刻|现在)的?你[，,]"], "explanation": "「X 的你，Y」把读者推成第三人称观察对象，是抒情腔的固定句法载体。首轮 15 条被毙稿里 4 条是这个句式，定稿 0 条。字幕腔、宣传片旁白腔都属于这一类", "suggestion": "改回第二人称直说，或直接写动作。", "doc_metric": "", "doc_compare": "", "taste": "T7", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-08", "name": "宏大升格", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["(人类|世界|宇宙|所有人|全场|打工人|社畜)的?(至暗|巅峰|终极|最高|最低|荣光)"], "explanation": "把一件小事说成宏大概念（“周一的至暗时刻”）。夸张到脱离语境就失去可信度——而本人的梗是往具体方向夸张，不是往宏大方向", "suggestion": "降到具体名词：谁、在哪、什么事。", "doc_metric": "", "doc_compare": "", "taste": "T8", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-09", "name": "揭短说教", "tier": "syntactic", "scope": "sentence", "severity": "medium", "patterns": ["——\\s*你.{0,4}(也|又|总是|还是)", "你不是(说|讲)过"], "explanation": "破折号后直接把矛头指向读者的行为。注意同结构但把读者的话当梗复述的句子在定稿里存活——差别在主体：指向读者是揭短，复述自己的话是共谋。真人语料里的破折号密度只有 AI 产线的 0.14 倍，破折号+第二人称是双料信号", "suggestion": "删；这个梗要玩就把矛头指向自己。", "doc_metric": "", "doc_compare": "", "taste": "T9", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-10", "name": "空泛鼓励与打气", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["(好好|认真)(读|学|干|写)", "(加油|稳住|你可以的|冲鸭)", "(这波|这把|这次)稳", "(都|也)没倒下", "主打一个", "(主线|支线|日常|今日)任务[:：]", "(喝口水|歇一会|喘口气).{0,6}(再战|再来|继续)"], "explanation": "没有具体对象的鼓励与打气：口号式表达，念着顺口但零信息。文案池里被毙稿含游戏化任务腔、流行语打气等多种形态，定稿 0 条——本人要的是吐槽与自嘲，不是给人打气。弱规则：共现机制下单独一次只提示", "suggestion": "删。鼓励要有具体对象才有信息量。", "doc_metric": "", "doc_compare": "", "taste": "T10", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-11", "name": "收束保险腔", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["(综上所述|总而言之|总的来说|由此可见|不得不说)"], "explanation": "真人基线每千字 0.01 次 vs AI 产线 0.05 次（差 5 倍），是本人文本里最稀缺的标记之一。收束词的功能是宣布“下面这句不用论证”", "suggestion": "删掉收束词直接说结论。", "doc_metric": "", "doc_compare": "", "taste": "T12", "doc_threshold": null, "doc_tiers": []}, {"id": "T-VOICE-12", "name": "顺口溜告诫", "tier": "syntactic", "scope": "sentence", "severity": "low", "patterns": ["(不|没).{1,6}[，,].{1,8}(崩溃|完蛋|报废|废了|白搭)"], "explanation": "押韵对仗的短劝诫（“不睡午觉，下午报废”）：念着顺口，但内容是人人都知道的常识，不提供任何决策信息。与空泛鼓励同族——都是“口号式”表达，被毙稿里有、定稿里没有", "suggestion": "删；要留下就必须带具体对象或数字。", "doc_metric": "", "doc_compare": "", "taste": "T10", "doc_threshold": null, "doc_tiers": []}, {"id": "T-MANUAL-01", "name": "功能说明腔", "tier": "structural", "scope": "sentence", "severity": "high", "patterns": ["(自动|智能)(挑|选|匹配|生成|识别|分析).{0,6}(相关|内容|结果)?", "先.{1,4}再.{1,4}(作答|回答|生成|处理)", "一次最多.{0,6}(篇|条|个|张)", "(请直接|直接)(问|输入|点)", "(勾选|选中)的.{0,8}(以|用).{0,6}(参与|加入)"], "explanation": "解释“产品怎么工作”的文案。用户用一次就懂，写出来只是噪声——本人对这类文案的处理一律是整句删，从不压缩或换说法（事件 79085432a6b4 点名了三条此类文案“不要有无用文案”；e9c030c5a88e、63cbaaa6f270、fe6c60314e3a 各有一处整句删除的实例）", "suggestion": "整句删。操作确实非直觉时，才压成最短的操作指令。", "doc_metric": "", "doc_compare": "", "taste": "T11", "doc_threshold": null, "doc_tiers": []}, {"id": "T-MANUAL-02", "name": "操作指引冗余", "tier": "structural", "scope": "sentence", "severity": "medium", "patterns": ["(点击|单击|拖入|拖拽|勾选|按下|右键).{0,12}(选择|上传|导入|打开|完成)", "(esc|Esc|回车|快捷键).{0,6}(取消|确认)", "(支持|可以|可)先?.{0,6}(导入|导出|批量|离线|多选)"], "explanation": "操作指引类文案。除非该动作反直觉，否则属于“用户自己会试出来”的冗余说明（事件 2c822c5e3b78：一段拖拽说明被压成最短指令；63cbaaa6f270：一条框选说明整句删）", "suggestion": "整句删；必须保留时压到最短，去掉修饰与解释。", "doc_metric": "", "doc_compare": "", "taste": "T11", "doc_threshold": null, "doc_tiers": []}, {"id": "T-MANUAL-03", "name": "功能名当文案", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["(批注台|工作台|控制台|中枢|引擎|模块)$"], "explanation": "把功能名当界面文案用（事件 2c822c5e3b78：一个界面标题被要求删除——用户已经在用了，不需要标题告诉他这是哪）。本规则只匹配整行结尾，避免误伤正文里的正常用法", "suggestion": "删标题；界面本身即说明。", "doc_metric": "", "doc_compare": "", "taste": "T11", "doc_threshold": null, "doc_tiers": []}, {"id": "T-DASH-01", "name": "破折号超我基线", "tier": "statistical", "scope": "doc", "severity": "low", "patterns": [], "explanation": "破折号是本人语料里与 AI 产线差距最大的标点标记（0.14 倍）。它让作者免于说明两个分句的逻辑关系，因此是“解释性偷懒”的载体。阈值按每句 0.2 个（远高于我的基线，只在异常密集时提示）", "suggestion": "换成逗号、冒号或括号，或者把两个分句的关系写清楚。", "doc_metric": "dash_density", "doc_compare": "above", "taste": "T12", "doc_threshold": 0.2, "doc_tiers": []}], "review": [{"id": "R-ANALYT-01", "name": "分析腔标签词", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["镜头语言", "叙事(节奏|结构)的?把握", "(电影|影片)的?(美学|质感)|(视觉|美学)风格", "人物弧光"], "explanation": "AI 影评爱贴影評教材的分析标签（“镜头语言”“人物弧光”），真人短评更多直接说感受。C-ReD film review 实测“镜头语言”AI 25/400 vs 真人 0。标签词本身合法——但一篇短评里堆标签词而没有具体场面，就是模板影评", "suggestion": "每个标签词后面跟一个具体场面或镜头；跟不出来就删。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "R-RECAP-01", "name": "剧情复述腔", "tier": "lexical", "scope": "sentence", "severity": "medium", "patterns": ["(影片|电影|该剧|本剧|这本书)?讲述了", "故事(发生|围绕)在", "(讲述|描绘)了.{0,16}(的)?故事"], "explanation": "短评的功能是评价，不是复述剧情。“讲述了…的故事”是 AI 影评的默认骨架：C-ReD 实测 AI 19/400 vs 真人 0——真人默认读者看过，直接聊感受和证据", "suggestion": "复述压到一句以内，把篇幅让给评价和具体证据。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}, {"id": "L-NEGA-01", "name": "否定式排比", "tier": "lexical", "scope": "sentence", "severity": "low", "patterns": ["不仅.{2,24}(，|、)?(而且|更是|还|甚至|也)", "不是.{2,24}(，|、)?而是", "(不仅仅?是).{2,20}(更是|还是)"], "explanation": "“不仅…更是”式拔高在影评里用于把一部作品拔成“一种现象”。C-ReD film review 实测区分度温和（加权命中 1.06 vs 1.00），只保留 low 共现门控", "suggestion": "把“不仅…更是”拆成两个具体判断，各自给证据。", "doc_metric": "", "doc_compare": "", "taste": "", "doc_threshold": null, "doc_tiers": []}]};
let SCORING = {};
try { SCORING = {"academic": {"corpus": "C-ReD paper 245 篇过 8 句门槛样本（真人 33 vs 四模型 212），2026-09", "intercept": -69.8327, "hit_density": 8.9025, "sentence_cv": -14.8899, "ttr": 83.377, "ngram_repeat": 22.6387, "auroc": 0.984, "auroc_holdout": 0.974, "human_p50": 15, "human_p90": 63, "tiers": [{"min_chars": 600, "intercept": -79.959, "hit_density": 1.7466, "sentence_cv": -6.0175, "ttr": 89.2265, "ngram_repeat": 37.0485}]}, "essay": {"corpus": "C-ReD essay 域（过 8 句门槛样本：真人 1070 vs 7 模型 7544，类平衡加权），2026-09", "intercept": -42.9291, "hit_density": 9.1763, "sentence_cv": -16.4304, "ttr": 52.7223, "ngram_repeat": 4.1655, "auroc": 0.952, "auroc_holdout": 0.951, "human_p50": 6, "human_p90": 55}, "general": {"corpus": "HC3-Chinese 391 篇过 8 句门槛样本（真人 91 vs ChatGPT 300，类平衡加权），2026-09", "intercept": 18.0167, "hit_density": 1.9553, "sentence_cv": -7.4254, "ttr": -17.0713, "ngram_repeat": -5.8575, "auroc": 0.838, "auroc_holdout": 0.87, "human_p50": 27, "human_p90": 69}, "news": {"corpus": "C-ReD news 域（过 8 句门槛样本：真人 1413 vs 7 模型 15112，类平衡加权），2026-09", "intercept": -60.4796, "hit_density": 5.9358, "sentence_cv": -5.344, "ttr": 65.9004, "ngram_repeat": -2.4644, "auroc": 0.938, "auroc_holdout": 0.935, "human_p50": 7, "human_p90": 61}, "official": null, "personal": null, "review": null}; } catch (e) { SCORING = {}; }
const VERSION = "0.17.6";
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
