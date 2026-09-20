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

  /* R1 重要内容判据（不含中文数词+量词——文案里的量词不是数据） */
  var IMPORTANT_RE = /\d|%|％|[一二三四五六七八九十百千万]+(倍|万|亿|人天|分钟)|(结论|结果表明|数据显示|实测|验证|复现|报错|错误码|失败率|通过率|达标|未达标)/;
  /* R3 具体名词 / 梗 */
  var NOUN_RE = /(DDL|deadline|组会|参考文献|文献|论文|paper|Paper|accept|数据|导师|大佬|咖啡|午饭|午休|书桌|台灯|日历|邮件|报错|日志|版本|分支|草稿|推文|稿子)/;
  var MEME_RE = /(牛马|摸鱼|摆烂|连滚带爬|火葬场|狠人|卷王|躺平)/;

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
    if (has("T-VOICE-01") && heads.length >= 2) return [heads[0] + "。", "", "T-VOICE-01"];
    if (has("T-VOICE-02") && heads.length >= 2) return [heads[0] + "。", "", "T-VOICE-02"];
    if (has("T-VOICE-07")) {
      var m = body.match(/^[^，,]{0,10}的你[，,]\s*(.+)$/);
      if (m) return [m[1].trim() + "。", "", "T-VOICE-07"];
    }
    if (has("T-VOICE-11")) {
      return [body.replace(/^(综上所述|总而言之|总的来说|由此可见|不得不说)[，,]?\s*/, "") + "。",
        "", "T-VOICE-11"];
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

  function compile(rules) {
    return rules.map(function (r) {
      return {
        id: r.id, name: r.name, severity: r.severity, taste: r.taste || "",
        explanation: r.explanation || "", scope: r.scope,
        _patterns: (r.patterns || []).map(function (p) { return new RegExp(p); }),
      };
    });
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
    var manual = false;
    for (var k = 0; k < ids.length; k++) if (ids[k].indexOf("T-MANUAL") === 0) manual = true;
    var action;
    if (manual) action = DELETE;
    else if (cand) action = REWRITE;
    else {
      var hasSubstance = stripTrailingParen(line).length > 14;
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

  function rewriteText(text, rules) {
    var compiled = compile(rules);
    var advices = [];
    var lines = String(text).split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      advices.push(classifyLine(line, compiled));
    }
    return { advices: advices };
  }

  return { rewriteText: rewriteText, classifyLine: classifyLine,
           KEEP: KEEP, REWRITE: REWRITE, DELETE: DELETE };
});
