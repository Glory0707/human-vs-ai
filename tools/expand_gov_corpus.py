"""扩充公文体真人评测集：多来源抓取事务公文（通知/通报/方案/意见/公告）。

v0.17.7 的 gov/ 集 15 篇全是长篇规章；本脚本补充事务文种（与 gen2026
AI 公文的文种对齐），来源为中央部委与省政府门户的公开文件栏目——
政府公文按《著作权法》第五条不适用著作权保护，正文可自由使用。

礼貌抓取：请求间隔 ≥1s，只抓列表页与正文页，UA 自报身份。
输出 `_qa/corpus/gov/`（gitignore），格式与 gov_*.txt 一致。

用法:python tools/expand_gov_corpus.py [--per-source 40] [--max-pages 6]
"""
from __future__ import annotations

import argparse
import html
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "_qa" / "corpus" / "gov"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 human-vs-ai-corpus"}
_NOISE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_DOC_WORD = re.compile(r"通知|通报|纪要|方案|公告|意见|措施")

# 每源：列表页 URL 模板（{page}=页码，1 起）、详情链接正则、绝对化前缀函数
SOURCES = [
    {
        "name": "税务总局",
        "list": "https://www.chinatax.gov.cn/chinatax/n810341/n810755/index{n}.html",
        "base": "https://www.chinatax.gov.cn",
        "page_fmt": lambda n: "" if n == 1 else f"_{n - 1}",
        "link_re": re.compile(r'href="(/chinatax/[^"]+?\.html)"[^>]*>([^<]{6,80})'),
    },
    {
        "name": "教育部",
        "list": "http://www.moe.gov.cn/jyb_xxgk/moe_1777/moe_1778/index{n}.html",
        "page_fmt": lambda n: "" if n == 1 else f"_{n - 1}",
        "link_re": re.compile(r'href="(\./\d{6}/t\d+_\d+\.html)"[^>]*>([^<]{8,60})'),
        "base": "http://www.moe.gov.cn/jyb_xxgk/moe_1777/moe_1778/",
    },
    {
        "name": "农业农村部",
        "list": "http://www.moa.gov.cn/gk/tzgg_1/index{n}.htm",
        "page_fmt": lambda n: "" if n == 1 else f"_{n - 1}",
        "link_re": re.compile(r'href="(\.\./\S+?/t\d+_\d+\.htm)"[^>]*>([^<]{8,60})'),
        "base": "http://www.moa.gov.cn/gk/tzgg_1/",
    },
    {
        "name": "广东省政府",
        "list": "https://www.gd.gov.cn/zwgk/wjk/qbwj/yfh/index{n}.html",
        "page_fmt": lambda n: "" if n == 1 else f"_{n - 1}",
        "link_re": re.compile(r'href="(https://www\.gd\.gov\.cn/zwgk/wjk/qbwj/[^"]+post_\d+\.html)"[^>]*>([^<]{8,70})'),
    },
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def collect_links(src: dict, max_pages: int) -> list[tuple[str, str]]:
    """翻列表页收详情链接，返回 [(绝对 URL, 标题)]，去重保序。"""
    base = src.get("base")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for page in range(1, max_pages + 1):
        url = src["list"].format(n="") if page == 1 else None
        if url is None:
            # 模板含 {n} 的源用 page_fmt 生成第 n 页文件名
            url = src["list"].replace("{n}", src["page_fmt"](page))
            if "{n}" in url:  # 模板不含页码（单页源）
                break
        try:
            html_text = fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [列表失败] {src['name']} p{page}: {str(e)[:60]}")
            break
        added = 0
        for m in src["link_re"].finditer(html_text):
            href, title = m.group(1), m.group(2).strip()
            if base and href.startswith("./"):
                href = base + href[2:]
            elif href.startswith("../../") and base:
                href = base.rsplit("/", 2)[0] + "/" + href.split("/")[-1] if False else href  # 农业农村部相对层级在详情页处理
            if base and href.startswith("../"):
                # ../../govpublic/x/t.htm → 站点根相对（剥掉全部 ../）
                href = re.sub(r"^(?:\.\./)+", "", href)
                if "moa.gov.cn" in base:
                    href = "http://www.moa.gov.cn/" + href
            if not href.startswith("http"):
                if href.startswith("/") and base:
                    href = base + href  # 站内绝对路径
                else:
                    continue
            if href in seen:
                continue
            if not _DOC_WORD.search(title):
                continue
            seen.add(href)
            out.append((href, title))
            added += 1
        print(f"  [列表] {src['name']} p{page}: +{added}（累计 {len(out)}）")
        if added == 0 and page > 1:
            break
        time.sleep(1.2)
    return out


def extract(html_text: str) -> str:
    """与 fetch_gov_corpus.extract 同口径：去脚本样式与导航残片，拼正文行。"""
    text = _NOISE.sub("", html_text)
    text = _TAGS.sub("\n", text)
    text = html.unescape(text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text)]
    good = []
    for b in blocks:
        cn = len(re.findall(r"[\u4e00-\u9fa5]", b))
        if cn < 30:
            continue
        if any(k in b for k in ("版权所有", "网站标识码", "联系方式", "相关链接", "浏览次数",
                                "扫一扫", "主办单位", "备案", "索引号", "成文日期", "发布日期")):
            continue
        good.append(b)
    if not good:
        return ""
    lines = []
    for b in good:
        lines.extend(ln.strip() for ln in b.splitlines() if ln.strip())
    return "\n".join(lines)


def safe_name(title: str, url: str) -> str:
    tail = re.sub(r"\W+", "", url)[-12:] or "x"
    head = re.sub(r"[^\u4e00-\u9fa5\w]", "", title)[:12] or "doc"
    return f"exp_{head}_{tail}.txt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source", type=int, default=40)
    ap.add_argument("--max-pages", type=int, default=6)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for src in SOURCES:
        print(f"== {src['name']} ==")
        links = collect_links(src, args.max_pages)[: args.per_source]
        got = 0
        for url, title in links:
            out = OUT / safe_name(title, url)
            if out.exists():
                continue
            try:
                body = extract(fetch(url))
                cn = len(re.findall(r"[\u4e00-\u9fa5]", body))
                if cn < 300:  # 事务公文至少 300 中文字，过滤通知索引页
                    continue
                out.write_text(f"[source] {url}\n[title] {title}\n\n{body}", encoding="utf-8")
                got += 1
                total += 1
                print(f"  [ok] {title[:36]} {cn} 字")
            except Exception as e:  # noqa: BLE001
                print(f"  [fail] {url}: {str(e)[:60]}")
            time.sleep(1.2)
        print(f"== {src['name']} 入库 {got} 篇 ==")
    print(f"\n完成：本次新增 {total} 篇，目录 {OUT}")


if __name__ == "__main__":
    main()
