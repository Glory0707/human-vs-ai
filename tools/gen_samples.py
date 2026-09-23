"""调用大模型 API 生成评测用 AI 文本语料（不入库，_qa/corpus/ 已 gitignore）。

用途：当代代际校准与验证。C-ReD 语料停留在 2025 代模型，本脚本用当季
模型（doubao-seed-2.x / kimi / minimax / glm / deepseek）按场景模板生成
新代际样本，供词表方向验证、评分漂移检查、对抗评测复用。

prompt 只给问题与长度约束，不加任何风格引导——检测器实战遇到的是模型
默认行为，引导词会让语料偏离真实分布。

key 从外部文件读（--api-file），绝不写入本仓库。输出 JSONL 每行：
{model, scene, prompt_id, prompt, text, n_chars}。脚本可重入：已生成的
(model, prompt_id) 自动跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ---------- 端点与模型池（同一 key 更换 model ID 的归为一组） ----------

def load_endpoints(api_file: Path) -> list[dict]:
    text = api_file.read_text(encoding="utf-8")
    keys = [l.split("：", 1)[1].strip() for l in text.splitlines() if l.startswith("api key")]
    if len(keys) < 3:
        sys.exit(f"API 文件格式变化：只解析出 {len(keys)} 个 key")
    openai_compat = "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"
    return [
        {"url": openai_compat, "key": keys[0],
         "models": ["doubao-seed-2.1-pro", "doubao-seed-2.1-turbo",
                    "doubao-seed-2.0-lite", "kimi-k3", "kimi-k2.8-preview",
                    "minimax-m3", "glm-5.3"]},
        {"url": "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
         "key": keys[1], "models": ["glm-5.3-flash"]},
        {"url": "https://api.deepseek.com/chat/completions",
         "key": keys[2], "models": ["deepseek-flash"]},
    ]

# ---------- 场景模板 ----------

QA_QUESTIONS = [
    "为什么现在的年轻人越来越难存下钱？",
    "有哪些提升工作效率的小习惯？",
    "如何看待\"AI 会取代程序员\"这种说法？",
    "长期熬夜的人身体会发生什么变化？",
    "新手如何挑选第一台相机？",
    "为什么建议大学生在校期间多实习？",
    "租房有哪些踩坑经验值得分享？",
    "每天喝咖啡对身体到底好不好？",
    "如何系统学习做饭？",
    "读历史书有什么实际用处？",
    "为什么越来越多人开始骑行通勤？",
    "养猫和养狗各是什么体验？",
    "如何克服拖延症？",
    "小城市生活和大城市生活各有什么优劣？",
    "有哪些值得坚持的省钱习惯？",
    "为什么现在大家越来越爱看短视频？",
    "怎样给孩子选择兴趣班比较合理？",
    "健身房私教课值不值得买？",
    "普通人如何提高自己的表达能力？",
    "为什么建议每年做一次体检？",
    "出差旅行有哪些高效打包技巧？",
    "如何判断一款新能源汽车值不值得买？",
    "长期伏案工作如何保护颈椎？",
    "普通人如何开始跑步并坚持下来？",
]

SCENES = {
    # 每题一个最小干预 prompt：问题 + 长度约束
    "qa": [f"{q} 请写一篇 600 字左右的回答。" for q in QA_QUESTIONS],
}

PER_MODEL = 8  # 每个模型抽的题目数（轮转覆盖整池）


def assign_jobs(scene: str, models: list[str]) -> list[tuple[str, int, str]]:
    prompts = SCENES[scene]
    jobs = []
    for mi, model in enumerate(models):
        for j in range(min(PER_MODEL, len(prompts))):
            pi = (mi * 3 + j * 5) % len(prompts)  # 步长互质轮转，覆盖整池
            jobs.append((model, pi, prompts[pi]))
    return jobs


def chat(url: str, key: str, model: str, user: str, timeout: int = 180) -> str:
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model,
                         "messages": [{"role": "user", "content": user}]}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="qa", choices=sorted(SCENES))
    ap.add_argument("--api-file", type=Path, default=Path(r"D:/科研/API.txt"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    out_dir = args.out or (ROOT / "_qa" / "corpus" / "gen2026")
    out_dir.mkdir(parents=True, exist_ok=True)
    endpoints = load_endpoints(args.api_file)

    jobs = []
    for ep in endpoints:
        jobs.extend((ep, job) for job in assign_jobs(args.scene, ep["models"]))

    out_path = out_dir / f"{args.scene}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["model"], r["prompt_id"]))
            except (json.JSONDecodeError, KeyError):
                pass
    todo = [(ep, m, pi, p) for ep, (m, pi, p) in jobs if (m, pi) not in done]
    print(f"任务 {len(jobs)}，已完成 {len(done)}，待生成 {len(todo)} → {out_path}")

    lock = threading.Lock()
    fh = out_path.open("a", encoding="utf-8")
    fails: list[str] = []

    def work(item):
        ep, model, pi, prompt = item
        for attempt in range(3):
            try:
                text = chat(ep["url"], ep["key"], model, prompt)
                if len(text.strip()) < 100:
                    raise ValueError(f"过短 {len(text)} 字")
                rec = {"model": model, "scene": args.scene, "prompt_id": pi,
                       "prompt": prompt, "text": text.strip(), "n_chars": len(text)}
                with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    n = len(done) + 1
                    done.add((model, pi))
                print(f"  [{n}/{len(jobs)}] {model} #{pi} {len(text)}字")
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    with lock:
                        fails.append(f"{model} #{pi}: {e}")
                    print(f"  FAIL {model} #{pi}: {e}")
                else:
                    time.sleep(5 * (attempt + 1))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))
    fh.close()
    print(f"完成，用时 {(time.time()-t0)/60:.0f} 分钟，失败 {len(fails)}")
    for f in fails:
        print(" ", f)


if __name__ == "__main__":
    main()
