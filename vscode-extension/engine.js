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
