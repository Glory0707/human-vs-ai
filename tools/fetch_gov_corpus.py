"""抓取中国政府网公开公文正文,建公文体真人评测集。

公文的可抓取性:政府公文按《著作权法》第五条不适用著作权保护(官方文件),
文本可自由使用;URL 逐篇人工核对过是国务院/部委公开文件。

正文提取:gov.cn 页面正文在特定容器内,这里不依赖结构——去掉
script/style/标签后取最长的连续中文文本块,再砍掉页头页尾导航残片。
粗糙但够评测用;每篇抓完人工抽查开头结尾。

用法:python tools/fetch_gov_corpus.py   (URL 清单内嵌在脚本里,新增公文往列表加)
"""
from __future__ import annotations

import html
import re
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent.parent / "_qa" / "corpus" / "gov"

URLS = [
    # 国务院部门文件
    "https://www.gov.cn/zhengce/zhengceku/202501/content_6997129.htm",
    "https://www.gov.cn/zhengce/zhengceku/202601/content_7054201.htm",
    "https://www.gov.cn/zhengce/zhengceku/202603/content_7064281.htm",
    "https://www.gov.cn/zhengce/zhengceku/2020-01/21/content_5471256.htm",
    "https://www.gov.cn/zhengce/zhengceku/2022-09/23/content_5711343.htm",
    "https://www.gov.cn/zhengce/zhengceku/202404/content_6948172.htm",
    "https://www.gov.cn/zhengce/zhengceku/202408/content_6967137.htm",
    "https://www.gov.cn/zhengce/zhengceku/202501/content_6996676.htm",
    "https://www.gov.cn/zhengce/zhengceku/202602/content_7057794.htm",
    "https://www.gov.cn/zhengce/zhengceku/202605/content_7068346.htm",
    "https://www.gov.cn/zhengce/zhengceku/202608/content_7078452.htm",
    "https://www.gov.cn/zhengce/content/202605/content_7068345.htm",
    # 实施方案 / 若干措施 / 行动方案（正文级长文）
    "https://www.gov.cn/zhengce/zhengceku/202512/content_7053397.htm",
    "https://www.gov.cn/zhengce/zhengceku/202601/content_7056523.htm",
    "https://www.gov.cn/zhengce/zhengceku/202607/content_7075851.htm",
    "https://www.gov.cn/zhengce/zhengceku/202609/content_7080361.htm",
]

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) human-vs-ai-corpus"}
_NOISE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_CN_RUN = re.compile(r"[^，。！？；：、…“”‘’《》（）\u4e00-\u9fa50-9A-Za-z，,;:.\(\)（）\[\]【】%％\s]+")


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


def extract(html_text: str) -> str:
    text = _NOISE.sub("", html_text)
    text = _TAGS.sub("\n", text)
    text = html.unescape(text)
    # 按空行切成块,保留含密集中文的块(公文正文常被切成多个条款块,全部按序拼接)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text)]
    good = []
    for b in blocks:
        cn = len(re.findall(r"[\u4e00-\u9fa5]", b))
        if cn < 30:  # 导航、链接列表、版权行
            continue
        if any(k in b for k in ("版权所有", "互联网站", "联系方式", "相关链接", "浏览次数", "扫一扫")):
            continue
        good.append(b)
    if not good:
        return ""
    lines = []
    for b in good:
        lines.extend(ln.strip() for ln in b.splitlines() if ln.strip())
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for i, url in enumerate(URLS):
        name = "gov_" + url.rstrip(".htm").rsplit("content_", 1)[-1] + ".txt"
        out = OUT / name
        if out.exists():
            ok += 1
            continue
        try:
            body = extract(fetch(url))
            cn = len(re.findall(r"[\u4e00-\u9fa5]", body))
            if cn < 200:
                print(f"[skip] {url} 正文过短({cn} 字)")
                fail += 1
                continue
            out.write_text(f"[source] {url}\n\n{body}", encoding="utf-8")
            print(f"[ok] {name} {cn} 字")
            ok += 1
        except Exception as e:
            print(f"[fail] {url}: {e}")
            fail += 1
        time.sleep(1.2)
    print(f"\n完成:{ok} 成功 / {fail} 失败,目录 {OUT}")


if __name__ == "__main__":
    sys.exit(main())
