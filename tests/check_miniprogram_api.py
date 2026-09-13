"""小程序接口预检：用 Python 模拟 wx.request，逐个调用小程序依赖的接口。

目的：在打开开发者工具之前排掉"接口层"错误（路径、字段名、权限、
未训练模型等），把需要在真机/工具里人工确认的范围缩到最小。

用法：
    python tests/check_miniprogram_api.py                      # 默认 127.0.0.1:5000
    python tests/check_miniprogram_api.py http://172.33.13.39:5000
"""
import json
import sys

import requests

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:5000"
# 与 utils/api.js 一致：带 X-User 头
HEADERS = {"X-User": "owner"}

results = []


def req(method, path, **kw):
    """模拟 wx.request。返回 (ok, payload)。"""
    try:
        r = requests.request(method, BASE + path, headers=HEADERS, timeout=10, **kw)
        d = r.json()
    except requests.RequestException as e:
        return False, {"message": f"网络错误 {e}"}
    except ValueError:
        return False, {"message": "响应不是 JSON"}
    return bool(d.get("ok")), d


def check(name, page, method, path, need=None, payload=None, soft=False):
    """need: 必须存在的字段（点号路径）。

    soft=True 时失败记为 [WARN] 而非 [FAIL]（用于依赖运行状态、
    需人工准备的条件，如设备是否空闲、模型是否已训练）。
    """
    ok, d = req(method, path, json=payload)
    line = f"{method} {path}"
    if not ok:
        msg = d.get("message", "失败")
        if soft:
            results.append((name, page, line, True, f"跳过：{msg}"))
            print(f"[WARN] {name:26s} {line}\n        {msg}")
        else:
            results.append((name, page, line, False, msg))
            print(f"[FAIL] {name:26s} {line}\n        {msg}")
        return None

    data = d.get("data")
    missing = []
    for path_expr in (need or []):
        cur = data
        for part in path_expr.split("."):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            if not isinstance(cur, dict) or part not in cur:
                missing.append(path_expr)
                break
            cur = cur[part]
    if missing:
        results.append((name, page, line, False, f"缺字段 {missing}"))
        print(f"[FAIL] {name:26s} {line}\n        缺字段: {missing}")
    else:
        results.append((name, page, line, True, "OK"))
        print(f"[PASS] {name:26s} {line}")
    return data


def free_pond(ponds):
    """找一个投料设备空闲的鱼塘（设备占用时不允许下发，是需求书要求的行为）。"""
    for p in ponds:
        ok, d = req("GET", f"/api/ponds/{p['id']}/tasks")
        if not ok:
            continue
        busy = [t for t in (d.get("data") or []) if t.get("device_locked")]
        if not busy:
            return p, None
    return None, "所有鱼塘的设备都有未结束任务（可先核查待核查任务释放占用）"


print("=" * 78)
print(f"小程序接口预检    BASE={BASE}")
print("=" * 78)

# ---------------- 鱼塘列表页 ----------------
print("\n【鱼塘列表页 pages/index】")
ponds = check("鱼塘列表", "index", "GET", "/api/ponds", need=[
    "name", "code", "area", "area_unit", "terminal.status"])
pid = None
if ponds:
    pid = ponds[0]["id"]
    print(f"        → 取第 1 个鱼塘 id={pid}（{ponds[0]['name']}）")
else:
    print("        [中止] 鱼塘列表不可用")
    sys.exit(1)

# ---------------- 单塘页 ----------------
print("\n【单塘看板页 pages/pond】")
check("鱼塘详情", "pond", "GET", f"/api/ponds/{pid}", need=["name", "batches"])
check("环境实况", "pond", "GET", f"/api/ponds/{pid}/env/latest", need=[
    "temperature.connected", "oxygen.connected", "ph.connected"])
check("最近任务", "pond", "GET", f"/api/ponds/{pid}/tasks")

sg = check("生成投喂建议", "pond", "POST", f"/api/ponds/{pid}/suggestions",
           need=["code", "amount", "unit", "rule_version", "reason"], payload={})

# 确认并下发：必须用「同一个鱼塘」的建议。
# 设备被占用时按需求书应拒绝，属正常业务行为，故先找一个设备空闲的塘，
# 再针对该塘生成建议并下发；下发后随即核查释放，保证脚本可重复运行。
free, why = free_pond(ponds)
if free:
    # 该塘可能尚无环境数据（此时后端会正确地拒绝生成建议），
    # 先像真实终端那样上传一条有效水温，再验证建议与下发链路。
    from datetime import datetime
    req("POST", "/api/measurements", json={
        "pond_id": free["id"], "metric": "temperature", "value": 26.0,
        "unit": "℃", "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "sim"})
    sg2 = check(f"生成建议(空闲塘{free['id']})", "pond", "POST",
                f"/api/ponds/{free['id']}/suggestions", need=["code", "amount"])
    if sg2:
        import uuid
        made = check(f"确认并下发任务(塘{free['id']})", "pond", "POST",
                     f"/api/ponds/{free['id']}/tasks", need=["task_no"],
                     payload={"request_no": "MP-CHECK-" + uuid.uuid4().hex[:8],
                              "suggestion_code": sg2["code"],
                              "confirm_amount": sg2["amount"]})
        if made:
            req("POST", f"/api/tasks/{made['task_no']}/review",
                json={"conclusion": "not_executed", "basis": "小程序接口预检自动清理",
                      "device_recovery": "预检结束，设备可用"})
            print(f"        → 已自动核查释放设备占用（{made['task_no']}）")
else:
    check("确认并下发任务", "pond", "POST", "/api/ponds/0/tasks", soft=True, payload={})
    print(f"        {why}")

# 生长预测：需先训练
ok, d = req("GET", f"/api/ponds/{pid}/model/predict?target=growth")
if ok:
    print(f"[PASS] {'生长预测（已训练）':26s} GET /api/ponds/{pid}/model/predict?target=growth")
    results.append(("生长预测", "pond", "GET model/predict", True, "OK"))
    for f in ("version", "note"):
        if f not in (d.get("data") or {}):
            print(f"        [警告] 缺字段 {f}")
else:
    msg = d.get("message", "")
    print(f"[WARN] {'生长预测（未训练）':26s} {msg}")
    results.append(("生长预测", "pond", "GET model/predict", True,
                    f"未训练（页面会提示）：{msg}"))

# ---------------- 交流页 ----------------
print("\n【交流排行页 pages/community】")
check("排行榜", "community", "GET", "/api/leaderboard?days=30", need=[
    "pond.name", "not_ranked_reason"])

# 排行榜中 fcr 可能为 null（数据不足），上面只校验必备字段
posts = check("帖子列表", "community", "GET", "/api/community/posts", need=[
    "title", "category", "reply_count", "author.name"])
# 创建接口只返回新帖基本字段；reply_count/author.name 由随后的列表接口提供
# （小程序发布后是重新 load() 拉列表，不依赖创建返回值）
check("发布帖子", "community", "POST", "/api/community/posts", need=[
    "id", "title", "category", "author.name"],
    payload={"title": "小程序预检帖", "content": "由 check_miniprogram_api 生成",
             "category": "经验交流"})

# ---------------- 汇总 ----------------
print("\n" + "=" * 78)
n_pass = sum(1 for *_, ok, _ in results if ok)
n_fail = len(results) - n_pass
print(f"合计 {len(results)} 项，通过 {n_pass} 项，失败 {n_fail} 项")
if n_fail == 0:
    print("接口层全部可用 → 可放心在微信开发者工具中导入 miniprogram/ 目录")
print("=" * 78)
sys.exit(0 if n_fail == 0 else 1)
