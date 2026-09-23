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
                    "doubao-seed-2.0-lite", "doubao-seed-2.0-pro", "kimi-k3",
                    "kimi-k2.8-preview", "minimax-m3", "glm-5.3"]},
        {"url": "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
         "key": keys[1], "models": ["glm-5.3-flash", "glm-4.7-flash"]},
        {"url": "https://api.deepseek.com/chat/completions",
         "key": keys[2], "models": ["deepseek-flash", "deepseek-chat", "deepseek-reasoner"]},
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

OFFICIAL_TASKS = [
    "关于开展安全生产大检查的通知（某区应急管理局）",
    "关于加强冬季火灾防控工作的通知（某街道办事处）",
    "关于表彰 2025 年度先进集体和个人的通报（某市总工会）",
    "关于申请拨付老旧小区改造资金的请示（某区住建局）",
    "关于举办全民健身运动会的实施方案（某区教体局）",
    "关于推进政务服务\"一网通办\"改革的实施意见（某市政府办公室）",
    "关于汛期防灾减灾工作的会议纪要（某县政府办）",
    "关于规范校外培训机构管理的通告（某市教育局）",
    "关于 2025 年度法治政府建设情况的年度报告（某区政府）",
    "关于开展人居环境整治行动的工作总结（某镇人民政府）",
    "关于公开征求城市停车场管理办法意见的公告（某市司法局）",
    "关于任免工作人员的通知（某市人力资源和社会保障局）",
    "关于做好国庆节假日期间值班工作的通知（某区政府办）",
    "关于组织申报省级科技计划项目的通知（某市科技局）",
    "关于餐饮场所燃气安全专项整治的方案（某区城管局）",
    "关于评选劳动模范和先进工作者的通知（某市总工会）",
]

# 省级门户常见文种（泛化体检对照用）：印发类含所印文件全文附录，批复为事项答复
GOV_GENRE_TASKS = [
    "关于印发《某市\"十五五\"科技创新规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某省\"十五五\"生态环境保护规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某市数字经济发展\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某省综合交通运输\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某市卫生健康\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某省农业农村现代化\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某市文化和旅游发展\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于印发《某省教育事业发展\"十五五\"规划》的通知，附规划全文，总长 4000 字左右",
    "关于某市国土空间总体规划的批复，800 字左右",
    "关于某高速公路项目用地预审与选址的批复，800 字左右",
    "关于同意设立某省级经济开发区的批复，800 字左右",
    "关于某流域防洪规划的批复，800 字左右",
    "关于某历史文化名城保护规划的批复，800 字左右",
    "关于某市城市总体规划修改方案的批复，800 字左右",
    "关于同意某航道整治工程可行性研究报告的批复，800 字左右",
    "关于某自然保护区范围和功能区调整的批复，800 字左右",
]

SCENES = {
    # 每题一个最小干预 prompt：问题 + 长度约束
    "qa": [f"{q} 请写一篇 600 字左右的回答。" for q in QA_QUESTIONS],
    "official": [f"请以公文格式写一份{t}，600 字左右。" for t in OFFICIAL_TASKS],
    "gov-genre": [f"请以公文格式写一份{t}。" for t in GOV_GENRE_TASKS],
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
    ap.add_argument("--models", default=None, help="逗号分隔，只保留这些模型（样本外生成用）")
    args = ap.parse_args()

    out_dir = args.out or (ROOT / "_qa" / "corpus" / "gen2026")
    out_dir.mkdir(parents=True, exist_ok=True)
    endpoints = load_endpoints(args.api_file)
    keep = {m.strip() for m in args.models.split(",")} if args.models else None

    jobs = []
    for ep in endpoints:
        models = ep["models"] if keep is None else [m for m in ep["models"] if m in keep]
        jobs.extend((ep, job) for job in assign_jobs(args.scene, models))

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
