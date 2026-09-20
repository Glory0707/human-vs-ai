/* human-vs-ai 浏览器引擎——与 Python 包 human_vs_ai 同构的第二实现。
 *
 * 存在理由:纯前端网页版(file:// 打开即用,零安装零上传)。
 * 纪律:两个实现的输出必须逐字段一致——tools/check_web_consistency.py
 * 用固定语料对比 Python 与本文件的 findings/hints JSON,任何漂移都会被
 * 测试抓住。改动切分或统计逻辑时两端必须同步改。
 *
 * 口径说明:全文唯一切分口径是字级 2-gram(Python 端 v0.11.0 起同口径),
 * TTR 三端同数并纳入一致性对比。所有 doc 统计(D-UNIF/D-CONN/D-PARA/
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
  var OPEN_Q_RE = /[“『「]/;
  var CLOSE_Q_RE = /[”』」]/;
  var PUNCT_RE = /[，。！？；：、…“”‘’《》（）()[\]【】,\.!\?;:"'—\-\s]/g;

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
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/(?:https?:\/\/|www\.)[^\s，。；！？、）)】」』]+/gi, "")
      .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "")
      .replace(/\*{1,3}(?!\s)([^*]*?[一-鿿][^*]*?)(?<!\s)\*{1,3}/g, "$1")
      .replace(/[ \t]{2,}/g, " ")
      .trim();
  }

  /* 行 → 有序单元 ["p"|"li"|"tr"|"b", 文本] */
  function lineUnits(text) {
    var units = [];
    var inCode = false;
    var rawLines = text.split(/\r?\n/);
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

  function stripMarkdown(text) {
    var rendered = groupBlocks(lineUnits(text)).map(function (b) {
      return b[0] === "para" ? b[1][0] : b[1].join("\n");
    });
    return rendered.join("\n\n");
  }

  function splitParagraphs(text) {
    var paras = [];
    var cur = [];
    var lines = text.split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].trim()) cur.push(lines[i].trim());
      else if (cur.length) { paras.push(cur.join("\n")); cur = []; }
    }
    if (cur.length) paras.push(cur.join("\n"));
    return paras;
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
        while (j < n && SENT_END.indexOf(text[j]) >= 0) j++;
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
    var clean = text.replace(PUNCT_RE, "");
    if (clean.length < 2) return clean.split("");
    var out = [];
    for (var i = 0; i < clean.length - 1; i++) out.push(clean.slice(i, i + 2));
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
    var clean = text.replace(PUNCT_RE, "");
    if (clean.length < 8) return 0.0;
    var grams = {};
    var total = 0;
    for (var i = 0; i + 4 <= clean.length; i++) {
      var g = clean.slice(i, i + 4);
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
    var lens = allSents.map(function (t) { return t.replace(PUNCT_RE, "").length; });
    var paraLens = blocks.map(function (block) {
      var n = 0;
      for (var i = 0; i < block.sents.length; i++) n += block.sents[i].text.replace(PUNCT_RE, "").length;
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

  /* 规则特征用未门控加权密度（共现门控是逐句指控的纪律，文档级聚合
     保留幅度信息更有效）；TTR 直接用 stats.ttr——全文唯一切分口径是
     字级 2-gram，与 Python 端逐位一致。短文本（<8 句）不出分。 */
  function computeScore(stats, weightedHits, scoring) {
    if (!scoring) return null;
    if (stats.n_sentences < 8) return null;
    var z = scoring.intercept;
    var components = {};
    var values = {
      hit_density: stats.n_sentences ? weightedHits / stats.n_sentences : 0,
      sentence_cv: stats.sentence_cv,
      ttr: stats.ttr,
      ngram_repeat: stats.ngram_repeat,
      conn_density: stats.conn_density,
    };
    for (var feat in scoring) {
      if (SCORING_META[feat] || !scoring.hasOwnProperty(feat)) continue;
      var v = values[feat];
      if (typeof v !== "number" || isNaN(v)) continue;
      components[feat] = scoring[feat] * v;
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
        if (rule2.doc_metric === "one_liner" && para.length === 1 && para[0].text.length <= 40) {
          push(rule2, {
            rule_id: rule2.id, rule_name: rule2.name, severity: rule2.severity,
            tier: rule2.tier, para: pi, sentence: para[0].text,
            matches: ["独句段（" + para[0].text.length + " 字）"],
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
    /* 够 8 句却没出分（该 profile 无 scoring 段）给一句原因；
       <8 句保持空——短文本不展示统计，多一行解释反而吵 */
    var scoreNote = (!scoring && stats.n_sentences >= 8) ? "该文体未校准评分，宁缺毋滥" : "";
    return { findings: findings, hints: hints, stats: stats,
             score: computeScore(stats, weightedHits, scoring || null),
             score_note: scoreNote };
  }

  return {
    analyze: analyze,
    splitDocument: splitDocument,
    splitSentences: splitSentences,
    splitParagraphs: splitParagraphs,
    stripMarkdown: stripMarkdown,
    computeDocStats: computeDocStats,
    tokenize: tokenize,
    mattr: mattr,
    fourGramRepeat: fourGramRepeat,
  };
});
