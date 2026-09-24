"""抓取省门户"印发类/批复类"真人公文（文种内二次校准语料，v0.20.0）。

背景：v0.18.1 泛化体检证明 official 系数绑定事务文种；本脚本补充印发/批复
的真人配对语料（湖南 + 安徽，与既有湖北/四川不同渠道），供文种内拟合与
留出验证用。来源栏目：
- 湖南：省政府门户 tzgg/swszf 静态档案分页（2020tzgg_N.html，N≥2；首页是
  JS 壳拿不到，但档案页覆盖近期全部条目）；
- 安徽：搜索引擎收割的 /public/1681/{id}.html 详情页种子（列表页同为 JS 壳）。

可抓取性：政府公文按《著作权法》第五条不适用著作权保护。技术注记：
hunan.gov.cn 对 Python urllib 握手报 SSL BAD_ECPOINT，统一走 curl 子进程。

礼貌抓取：请求间隔 ≥1.2s，UA 自报身份。
输出 `_qa/corpus/gov-oos/gov-{province}.json`（gitignore），格式与
gov-hubei.json 一致：{meta:{channel}, items:[{url,title,text,n_chars}]}，
与既有文件按 url 去重合并（可重复跑补量）。

用法:python tools/scrape_genre_corpus.py [--pages 5]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "_qa" / "corpus" / "gov-oos"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) human-vs-ai-corpus/0.1 (polite)"
_DELAY = 1.2
_NOISE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)
_TAG = re.compile(r"<[^>]+>")
# 标题 → 文种：批复优先（批复也会带"规划"字样）；印发含所印规划/方案/措施
RE_PIFU = re.compile(r"批复")
RE_YINFA = re.compile(r"印发|《[^》]*规划[^》]*》|《[^》]*方案[^》]*》|若干措施|若干政策|政策措施")
RE_SHIWU = re.compile(r"通知|通报|意见|方案|措施|决定|公告")
# 页面模板残片：正文行里出现这些关键词的块丢掉
_JUNK = ("版权所有", "网站标识码", "联系方式", "相关链接", "浏览次数", "扫一扫",
         "主办单位", "备案", "索引号", "成文日期", "发布时间", "责任编辑", "相关解读",
         "打印本页", "关闭本页", "分享到", "字号：", "【打印】", "返回顶部", "无障碍",
         "长者模式", "手机版", "订 阅", "客户端", "微 信", "微博", "网站地图",
         "使用帮助", "意见征集", "调查征集")
_MIN_CHARS = {"批复": 350, "印发": 1200, "事务": 300}  # 批复天然短（~450-2700），印发必须带全文附录
CHANNELS = {"hunan": "www.hunan.gov.cn swszf",
            "anhui": "www.ah.gov.cn 省政府文件",
            "yunnan": "www.yn.gov.cn zcwj"}


def fetch(url: str) -> str:
    """curl 子进程抓取（绕 hunan.gov.cn 的 SSL BAD_ECPOINT），utf-8/gb18030 兜底。"""
    r = subprocess.run(["curl", "-s", "-m", "30", "-A", _UA, url],
                       capture_output=True, timeout=45)
    raw = r.stdout
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_text(html_text: str) -> str:
    """去脚本样式与标签后按块拼接，丢模板残片块——与 expand_gov_corpus 同口径。"""
    text = _NOISE.sub("", html_text)
    text = _TAG.sub("\n", text)
    text = html.unescape(text)
    good = []
    for b in re.split(r"\n\s*\n", text):
        b = "\n".join(ln.strip() for ln in b.splitlines() if ln.strip())
        if len(re.findall(r"[\u4e00-\u9fa5]", b)) < 30:
            continue
        if any(k in b for k in _JUNK):
            continue
        good.append(b)
    return "\n".join(good).strip()


def genre_of(title: str) -> str | None:
    if RE_PIFU.search(title):
        return "批复"
    if RE_YINFA.search(title):
        return "印发"
    if RE_SHIWU.search(title):
        return "事务"
    return None


def collect_hunan(pages: int) -> list[dict]:
    """翻湖南 swszf 静态档案页（第 2 页起），收印发/批复详情链接。"""
    seen: set[str] = set()
    out: list[dict] = []
    link_re = re.compile(
        r"href=[\"'](/hnszf/xxgk/tzgg/swszf/\d{6}/t\d+_\d+\.html)[\"'][^>]*>\s*([^<]{10,90})")
    for n in range(2, pages + 2):
        url = f"https://www.hunan.gov.cn/hnszf/xxgk/tzgg/swszf/2020tzgg_{n}.html"
        try:
            page = fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [列表失败] hunan p{n}: {str(e)[:60]}")
            break
        added = 0
        for href, title in link_re.findall(page):
            title = title.strip().rstrip(".")
            g = genre_of(title)
            if not g or href in seen:
                continue
            seen.add(href)
            out.append({"prov": "hunan", "url": "https://www.hunan.gov.cn" + href,
                        "title": title, "genre": g})
            added += 1
        print(f"  [列表] hunan p{n}: +{added}（累计 {len(out)}）")
        if added == 0:
            break
        time.sleep(_DELAY)
    return out


# 安徽种子：搜索引擎收割（列表页 JS 壳拿不到），详情页 /public/1681/{id}.html
ANHUI_SEEDS = [
    ("关于印发安徽省2026年重点项目清单(第二批)的通知", "https://www.ah.gov.cn/public/1681/565542351.html"),
    ("关于印发安徽省2026年重点项目清单的通知", "https://www.ah.gov.cn/public/1681/565502961.html"),
    ("关于印发《安徽省国土空间规划(2021—2035年)》的通知", "https://www.ah.gov.cn/public/1681/565320591.html"),
    ("关于同意将舒城县晓天老街列为安徽历史文化街区的批复", "https://www.ah.gov.cn/public/1681/564251461.html"),
    ("关于阜阳港总体规划(2035年)的批复", "https://www.ah.gov.cn/public/1681/565470431.html"),
    ("办公厅关于印发《安徽省\"人工智能+万物\"应用行动方案》的通知", "https://www.ah.gov.cn/public/1681/565487271.html"),
    ("办公厅关于印发全民科学素质行动规划纲要实施方案的通知", "https://www.ah.gov.cn/public/1681/554087541.html"),
    ("印发《关于巩固拓展经济稳中向好势头若干政策举措》的通知", "https://www.ah.gov.cn/zwyw/ztzl/tzxfxd/zxzc/565498101.html"),
]


def collect_yunnan(pages: int) -> list[dict]:
    """云南省政府文件 zcwj/zxwj 静态档案页（index_N.html，N 越大越旧）。

    只收事务文种：印发/批复会被 v0.20.0 的 genre_scoring 抑制出分，
    抓了也进不了 official 样本外切片，白耗请求。
    """
    base = "https://www.yn.gov.cn/zwgk/zcwj/zxwj/"
    seen: set[str] = set()
    out: list[dict] = []
    link_re = re.compile(
        r"""href=["'](\./\d{6}/t\d+_\d+\.html)["'][^>]*>\s*([^<]{10,90})""")
    for n in range(905, 905 - pages, -1):
        url = f"{base}index_{n}.html"
        try:
            page = fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [列表失败] yunnan p{n}: {str(e)[:60]}")
            break
        added = 0
        for href, title in link_re.findall(page):
            title = title.strip()
            if RE_PIFU.search(title) or RE_YINFA.search(title) or "人事" in title:
                continue  # 印发/批复（抑制出分）与人事任免不要
            if href in seen:
                continue
            seen.add(href)
            out.append({"prov": "yunnan", "url": base + href[2:],
                        "title": title, "genre": "事务"})
            added += 1
        print(f"  [列表] yunnan p{n}: +{added}（累计 {len(out)}）")
        if added == 0:
            break
        time.sleep(_DELAY)
    return out


def collect_anhui() -> list[dict]:
    out = []
    for title, url in ANHUI_SEEDS:
        g = genre_of(title)
        if g:
            out.append({"prov": "anhui", "url": url, "title": title, "genre": g})
    return out


def scrape(jobs: list[dict]) -> None:
    """抓详情、清洗、按 url 去重合并进 gov-{prov}.json（每省读一次写一次）。"""
    got: dict[str, int] = {"hunan": 0, "anhui": 0, "yunnan": 0}
    for prov in ("hunan", "anhui", "yunnan"):
        rows = [j for j in jobs if j["prov"] == prov]
        if not rows:
            continue
        path = OUT / f"gov-{prov}.json"
        data = (json.loads(path.read_text(encoding="utf-8")) if path.exists()
                else {"meta": {"channel": CHANNELS[prov]}, "items": []})
        have = {it["url"] for it in data["items"]}
        for j in rows:
            if j["url"] in have:
                continue
            try:
                body = clean_text(fetch(j["url"]))
            except Exception as e:  # noqa: BLE001
                print(f"  [fail] {j['title'][:30]}: {str(e)[:50]}")
                time.sleep(_DELAY)
                continue
            n = len(body)
            if n < _MIN_CHARS[j["genre"]]:
                print(f"  [短] {j['title'][:34]} {n} 字（<{_MIN_CHARS[j['genre']]}）")
                time.sleep(_DELAY)
                continue
            data["items"].append({"url": j["url"], "title": j["title"],
                                  "text": body, "n_chars": n})
            have.add(j["url"])
            got[prov] += 1
            print(f"  [ok] {j['genre']} {j['title'][:38]} {n} 字")
            time.sleep(_DELAY)
        if got[prov]:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"== {prov} 入库 {got[prov]} 篇 → {path.name} ==")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5, help="湖南档案页翻页数")
    ap.add_argument("--limit", type=int, default=80, help="单次抓取详情页上限")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = (collect_hunan(args.pages) + collect_anhui() + collect_yunnan(args.pages))[: args.limit]
    print(f"待抓 {len(jobs)} 篇（hunan {sum(1 for j in jobs if j['prov'] == 'hunan')}"
          f" / anhui {sum(1 for j in jobs if j['prov'] == 'anhui')}）")
    scrape(jobs)


if __name__ == "__main__":
    main()
