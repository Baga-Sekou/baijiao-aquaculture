"""性能/并发基准（需求书 8.4：记录端数、采样频率、查询耗时）。

先启动后端（python backend/app.py），再运行本脚本：
    python scripts/benchmark.py            # 默认批量写入 200 条测量
    python scripts/benchmark.py --rows 500

产出 docs/性能测试记录.md，包含：
- 当前库内记录规模（测量 / 任务 / 日志条数）
- 采样写入吞吐（单条与批量，条/秒）
- 常用查询接口耗时（平均 / p95，20 次采样）
"""
import argparse
import json
import os
import statistics
import time

import requests

BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs",
                   "性能测试记录.md")
USER = {"X-User": "owner"}


def timed(fn, repeat=20):
    xs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    xs.sort()
    return {"avg_ms": round(statistics.mean(xs), 1),
            "p95_ms": round(xs[max(0, int(len(xs) * 0.95) - 1)], 1),
            "max_ms": round(xs[-1], 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200, help="批量写入的测量条数")
    args = ap.parse_args()

    from datetime import datetime, timedelta
    r = requests.get(BASE + "/health", timeout=5)
    if not r.ok:
        raise SystemExit("后端未启动，请先运行 python backend/app.py")

    print("== 性能基准 ==")
    report = ["# 性能测试记录", "",
              f"- 测试时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
              f"- 服务地址：{BASE}", ""]

    # ---- 记录规模 ----
    def count(path):
        d = requests.get(BASE + path, headers=USER, timeout=10).json()
        return d.get("total", len(d.get("data", []))) if isinstance(d.get("data"), list) \
            else d.get("data", {}).get("total", 0)

    measurements_before = count("/api/ponds/1/export")
    report += ["## 1. 记录规模", "",
               f"- 测量记录（A01 导出视图）：{measurements_before} 条", ""]

    # ---- 采样写入吞吐 ----
    now = datetime.now()
    single_payload = {"pond_id": 1, "metric": "temperature", "value": 26.0,
                      "unit": "℃", "collected_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                      "source": "sim"}
    t0 = time.perf_counter()
    n_single = 20
    for i in range(n_single):
        p = dict(single_payload, collected_at=(now + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S"))
        requests.post(BASE + "/api/measurements", headers=USER, json=p, timeout=10)
    single_sps = n_single / (time.perf_counter() - t0)

    batch = {"items": [{"pond_id": 1, "metric": "temperature", "value": 26.0 + (i % 5) * 0.1,
                        "unit": "℃",
                        "collected_at": (now + timedelta(seconds=60 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "sim"} for i in range(args.rows)]}
    t0 = time.perf_counter()
    rb = requests.post(BASE + "/api/measurements/batch", headers=USER, json=batch, timeout=120).json()
    batch_sps = args.rows / (time.perf_counter() - t0)
    accepted = rb.get("accepted", 0)
    report += ["## 2. 采样写入吞吐", "",
               f"- 单条写入：{n_single} 条，{single_sps:.0f} 条/秒（含 HTTP 往返）",
               f"- 批量写入：{args.rows} 条，接受 {accepted} 条，{batch_sps:.0f} 条/秒", ""]

    # ---- 查询耗时 ----
    cases = {
        "最新环境值 /env/latest": lambda: requests.get(
            BASE + "/api/ponds/1/env/latest", headers=USER, timeout=10),
        "日内曲线 /env/day": lambda: requests.get(
            BASE + "/api/ponds/1/env/day?metric=temperature", headers=USER, timeout=10),
        "趋势图表 /charts（30天）": lambda: requests.get(
            BASE + "/api/ponds/1/charts?days=30", headers=USER, timeout=10),
        "单塘看板 /dashboard": lambda: requests.get(
            BASE + "/api/ponds/1/dashboard", headers=USER, timeout=10),
        "多塘比较 /compare（7天）": lambda: requests.get(
            BASE + "/api/compare?days=7", headers=USER, timeout=10),
    }
    report += ["## 3. 查询耗时（20 次采样）", "",
               "| 接口 | 平均 | p95 | 最大 |", "|---|---|---|---|"]
    print()
    for name, fn in cases.items():
        m = timed(fn)
        report.append(f"| {name} | {m['avg_ms']} | {m['p95_ms']} | {m['max_ms']} |")
        print(f"  {name}: 平均 {m['avg_ms']}ms / p95 {m['p95_ms']}ms")

    measurements_after = count("/api/ponds/1/export")
    report += ["", f"- 写入后测量记录：{measurements_after} 条"
               f"（本次新增 {max(0, measurements_after - measurements_before)}）", "",
               "> 结论口径：采样频率按批量写入吞吐折算；查询耗时为接口端到端时间，"
               "含 HTTP 与序列化开销。SQLite 单机演示环境数据。"]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report))
    print(f"\n[ok] 报告已写入 docs/性能测试记录.md")


if __name__ == "__main__":
    main()
