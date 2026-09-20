/* human-vs-ai 浏览器引擎——与 Python 包 human_vs_ai 同构的第二实现。
 *
 * 存在理由:纯前端网页版(file:// 打开即用,零安装零上传)。
 * 纪律:两个实现的输出必须逐字段一致——tools/check_web_consistency.py
 * 用固定语料对比 Python 与本文件的 findings/hints JSON,任何漂移都会被
 * 测试抓住。改动切分或统计逻辑时两端必须同步改。
 *
 * 口径说明:浏览器没有 jieba,tokenize 退化为字级 2-gram(与 Python 无
 * jieba 时同口径)。TTR 数值因此与 Python(装了 jieba)不同,但两个
 * profile 的规则库都没有 TTR 判定规则,D-UNIF/D-CONN/D-PARA/D-NGRAM/
 * D-DASH 全部与分词无关——一致性不受影响。
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
  var OPEN_Q_RE = /[\u201C\u300E\u300C]/;
  var CLOSE_Q_RE = /[\u201D\u300F\u300D]/;
  var STRUCT_RE = /^\s*(```|~~~|#{1,6}\s|\||\*|[-+]\s|\d+\.\s|===|---)/;
  var PUNCT_RE = /[，。！？；：、…\u201C\u201D\u2018\u2019《》（）()[\]【】,\.!\?;:\u0022\u0027—\-\s]/g;

  function stripMarkdown(text) {
    var out = [];
    var inCode = false;
    var lines = text.split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var s = line.trim();
      if (s.indexOf("```") === 0 || s.indexOf("~~~") === 0) {
        inCode = !inCode;
        out.push("");
        continue;
      }
      if (inCode) continue;
      if (!s) { out.push(""); continue; }
      if (STRUCT_RE.test(line)) { out.push(""); continue; }
      var cleaned = line.replace(/`([^`]*)`/g, "$1");
      cleaned = cleaned.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
      cleaned = cleaned.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
      out.push(cleaned);
    }
    return out.join("\n");
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
    var clean = stripMarkdown(text);
    var paras = splitParagraphs(clean);
    var out = [];
    for (var pi = 0; pi < paras.length; pi++) {
      var sents = splitSentences(paras[pi]);
      for (var si = 0; si < sents.length; si++) sents[si].para = pi;
      out.push(sents);
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
    var vals = [];
    for (var s = 0; s + window <= tokens.length; s++) {
      var seen = {};
      var u = 0;
      for (var k = s; k < s + window; k++) {
        if (!seen[tokens[k]]) { seen[tokens[k]] = 1; u++; }
      }
      vals.push(u / window);
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

  function computeDocStats(paraSents, lexicon) {
    var allSents = [];
    for (var p = 0; p < paraSents.length; p++)
      for (var s = 0; s < paraSents[p].length; s++) allSents.push(paraSents[p][s].text);
    var lens = allSents.map(function (t) { return t.replace(PUNCT_RE, "").length; });
    var paraLens = paraSents.map(function (para) {
      var n = 0;
      for (var i = 0; i < para.length; i++) n += para[i].text.replace(PUNCT_RE, "").length;
      return n;
    });
    var fullText = allSents.join("");
    var tokens = tokenize(fullText);
    var stats = {
      n_paragraphs: paraSents.length,
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

  /* ---------- 引擎 ---------- */

  function compileRules(rules) {
    return rules.map(function (r) {
      var out = {};
      for (var k in r) out[k] = r[k];
      out._patterns = (r.patterns || []).map(function (p) {
        return new RegExp(p);
      });
      out.doc_threshold = r.doc_threshold == null ? NaN : r.doc_threshold;
      out.doc_tiers = (r.doc_tiers || []).map(function (t) {
        return [t[0] == null ? null : t[0], t[1]];
      });
      out.min_sentences = r.min_sentences == null ? 8 : r.min_sentences;
      return out;
    });
  }

  function analyze(text, rules) {
    rules = compileRules(rules);
    var doc = splitDocument(text);
    var findings = [], hints = [];
    var raw = {};

    function push(rule, f) {
      if (!raw[rule.id]) raw[rule.id] = [];
      raw[rule.id].push(f);
    }

    for (var pi = 0; pi < doc.length; pi++) {
      var para = doc[pi];
      for (var si = 0; si < para.length; si++) {
        var sent = para[si];
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
    return { findings: findings, hints: hints, stats: stats };
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
