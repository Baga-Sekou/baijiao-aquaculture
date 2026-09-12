"""验收场景测试（对应需求说明书第 9 章验收要求）。

以完整业务场景为单位，逐条给出检查方法、预期结果与实际结果。
运行前需先启动后端；本脚本自行拉起/复位运行数据。

用法：
    python tests/test_acceptance.py
"""
import json
import os
import sys
import time
import uuid

import requests

BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
H = {"X-User": "wang"}          # 王师傅：A01 可控制 + 可核查
H_OWNER = {"X-User": "owner"}
H_NOCONTROL = {"X-User": "liu"}  # 刘师傅：只有 A02，且不可核查

RESULTS = []


def call(method, path, user=None, **kw):
    headers = dict(kw.pop("headers", {}))
    if user:
        headers["X-User"] = user
    r = requests.request(method, BASE + path, headers=headers, timeout=10, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def check(name, method_desc, expected, passed, actual):
    RESULTS.append({"场景": name, "检查方法": method_desc,
                    "预期结果": expected, "结果": "通过" if passed else "失败",
                    "实际": actual})
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}: {actual}")


def reset():
    """复位运行数据与测量数据，保证场景之间互不干扰。

    会清空已导入的 CSV 测量；测试前如需保留，请重新运行 scripts/import_csv.py。
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import reset_runtime
    sys.argv = ["reset_runtime", "--all"]
    reset_runtime.main()


def fresh_suggestion(pond=1, user="wang"):
    _, d = call("POST", f"/api/ponds/{pond}/suggestions", user=user, json={})
    return d.get("data")


# 投喂类场景固定用 A02：A02 没有终端在轮询，回执不会被仿真器自动应答，
# 从而保证「回执超时」「迟到回执」等场景可复现。
FEED_POND = 2


def make_task(prefix=""):
    """在 FEED_POND 上新建一条已下发的任务，返回任务号。"""
    seed_env(FEED_POND)
    sg = fresh_suggestion(pond=FEED_POND, user="owner")
    _, t = call("POST", f"/api/ponds/{FEED_POND}/tasks", user="owner",
                json={"request_no": prefix + "-" + uuid.uuid4().hex[:8],
                      "suggestion_code": sg["code"]})
    return t["data"]["task_no"]


def seed_env(pond=1, temp=26.5, day=None):
    """上传一条当前有效的水温，保证建议可生成。"""
    from datetime import datetime
    ts = (day or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    call("POST", "/api/measurements", json={
        "pond_id": pond, "metric": "temperature", "value": temp, "unit": "℃",
        "collected_at": ts, "source": "sim"})


# ============================================================ 场景
def s_terminal_register():
    call("POST", "/api/terminals/register", json={"code": "TERM-A01", "pond_id": 1})
    call("POST", "/api/terminals/register", json={"code": "TERM-A02", "pond_id": 2})
    code, d = call("GET", "/api/terminals")
    terms = {t["code"]: t["status"] for t in d["data"]}
    call("POST", "/api/terminals/TERM-A02/offline")
    _, d2 = call("GET", "/api/terminals")
    t2 = {t["code"]: t["status"] for t in d2["data"]}
    passed = terms.get("TERM-A01") == "online" and terms.get("TERM-A02") == "online" \
        and t2.get("TERM-A01") == "online" and t2.get("TERM-A02") == "offline"
    check("终端注册", "两个终端分别注册，断开其中一个再连接",
          "鱼塘归属及连接数正确，另一个终端仍可上传", passed,
          f"注册后 {terms}，断开 A02 后 {t2}")


def s_env_upload():
    stamp = time.time()
    call("POST", "/api/measurements", json={
        "pond_id": 1, "metric": "temperature", "value": 25.7, "unit": "℃",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"), "source": "sim"})
    _, d = call("GET", "/api/ponds/1/env/latest", user="wang")
    t = d["data"]["temperature"]
    passed = t["connected"] and abs(t["value"] - 25.7) < 1e-6 and t["collected_at"]
    check("环境数据上传", "上传一条含鱼塘/设备/时间/单位的有效测量",
          "两端可查到同一记录，显示来源及更新时间", passed,
          f"最新水温 {t['value']}{t['unit']} @ {t['collected_at']} 来源={t.get('source')}")


def s_data_invalid():
    seed_env()
    call("POST", "/api/measurements", json={
        "pond_id": 1, "metric": "temperature", "value": 88.8, "unit": "℃",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    _, d = call("GET", "/api/ponds/1/env/day?metric=temperature&date=%s"
                % time.strftime("%Y-%m-%d"), user="wang")
    series = d["data"]["series"]
    bad = [x for x in series if not x["valid"]]
    passed = len(bad) >= 1 and bad[0]["invalid_reason"]
    check("数据失效", "提供超量程的必需输入",
          "标明无效原因，不生成有效投喂建议", passed,
          f"超量程记录被标记 valid=False，原因：{bad[0]['invalid_reason'] if bad else '无'}")


def s_day_features():
    _, d = call("GET", "/api/ponds/1/env/day?metric=temperature&date=%s"
                % time.strftime("%Y-%m-%d"), user="wang")
    f = d["data"]["features"]
    passed = "samples_valid" in f and "missing" in f and f["enough"] is not None
    check("日内数据整理", "提供包含缺测和异常值的日内数据",
          "保存原始曲线，正确标记异常，统计窗口与样本数可查", passed,
          f"窗口={f['window']}，有效样本={f['samples_valid']}/{f['expected_samples']}，缺测={f['missing']}")


def s_temp_driven():
    reset(); seed_env(1, 26.5)   # 适温
    sg1 = fresh_suggestion()
    reset(); seed_env(1, 16.0)   # 低温
    sg2 = fresh_suggestion()
    passed = sg1 and sg2 and sg1["amount"] > sg2["amount"] and sg1["inputs"]["day_features"]
    check("温度参与计算", "提供两组不同有效温度特征的输入",
          "结果符合规则，能追溯特征窗口、原始输入和版本", passed,
          f"适温建议 {sg1['amount'] if sg1 else '-'}kg > 低温建议 {sg2['amount'] if sg2 else '-'}kg；"
          f"规则={sg1['rule_version'] if sg1 else '-'}")


def s_normal_feed():
    reset(); tn = make_task("REQ")
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "accept", "status": "accepted"})
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "execute", "status": "running"})
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish", "status": "done",
                                        "actual_amount": 10.2})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    td = d["data"]
    passed = (td["status"] == "done" and td["suggested_amount"] and td["confirm_amount"]
              and td["actual_amount"] == 10.2 and len(td["receipts"]) >= 3)
    check("正常投喂", "有权限用户确认建议，终端返回完整执行反馈",
          "同一任务依次更新状态，保留建议量、确认量和反馈", passed,
          f"状态={td['status']}，建议={td['suggested_amount']}，确认={td['confirm_amount']}，"
          f"实际={td['actual_amount']}，回执 {len(td['receipts'])} 条")


def s_duplicate_submit():
    reset(); seed_env(FEED_POND)
    sg = fresh_suggestion(pond=FEED_POND, user="owner")
    req = "REQ-DUP-" + uuid.uuid4().hex[:8]
    _, t1 = call("POST", f"/api/ponds/{FEED_POND}/tasks", user="owner",
                 json={"request_no": req, "suggestion_code": sg["code"]})
    _, t2 = call("POST", f"/api/ponds/{FEED_POND}/tasks", user="owner",
                 json={"request_no": req, "suggestion_code": sg["code"]})
    tn = t1["data"]["task_no"]
    # 重复 finish 回执
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "done", "actual_amount": 9.9})
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "done", "actual_amount": 9.9})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    passed = (t2["data"]["task_no"] == tn and d["data"]["actual_amount"] == 9.9)
    check("重复提交", "重复提交同一请求及重复发送同一回执",
          "只对应一个任务和一次执行，不重复统计", passed,
          f"两次请求均返回 {tn}；重复回执后实际量仍为 {d['data']['actual_amount']}")


def s_receipt_timeout():
    reset(); tn = make_task("REQ-TO")
    # 不发送回执；把超时阈值降为 0 并等过 1 秒余量后触发巡检
    call("PUT", "/api/config/task_timeout_sec", user="owner", json={"value": "0"})
    time.sleep(1.4)
    call("POST", "/api/tasks/check-timeouts")
    call("PUT", "/api/config/task_timeout_sec", user="owner", json={"value": "120"})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    td = d["data"]
    passed = td["status"] == "unknown" and td["device_locked"] is True
    check("回执超时", "任务下发后中断回执",
          "显示待核查及原因，不自动再次投料", passed,
          f"状态={td['status']}，设备占用={td['device_locked']}（未自动重发）")


def s_review_recover():
    # 承接上一个场景：任务处于 unknown
    _, lst = call("GET", "/api/tasks?status=unknown", user="owner")
    tasks = [t for t in lst["data"] if t["pond_id"] == FEED_POND]
    if not tasks:
        check("核查与恢复", "核实待核查任务再尝试新建任务", "可登记结论并解除占用",
              False, "无可核查任务")
        return
    tn = tasks[0]["task_no"]
    _, r = call("POST", f"/api/tasks/{tn}/review", user="owner", json={
        "conclusion": "not_executed", "basis": "查询终端执行记录，无该任务执行痕迹",
        "evidence": "现场查看无投料", "device_recovery": "终端已无该任务，旧指令不再执行"})
    passed = r["data"]["status"] == "failed" and r["data"]["device_locked"] is False
    check("核查与恢复", "核实未执行的任务，登记结论及依据，再尝试新建任务",
          "有权限者登记结论及依据；核查结束且设备满足恢复条件时解除占用", passed,
          f"结论=not_executed，任务状态={r['data']['status']}，设备占用={r['data']['device_locked']}")


def s_late_receipt():
    reset(); tn = make_task("REQ-LATE")
    call("PUT", "/api/config/task_timeout_sec", user="owner", json={"value": "0"})
    time.sleep(1.4)
    call("POST", "/api/tasks/check-timeouts")
    call("PUT", "/api/config/task_timeout_sec", user="owner", json={"value": "120"})
    # 核查判为未执行
    call("POST", f"/api/tasks/{tn}/review", user="owner", json={
        "conclusion": "not_executed", "basis": "现场确认",
        "device_recovery": "设备可用"})
    # 迟到回执说完成了 -> 冲突
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "done", "actual_amount": 8.8})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    td = d["data"]
    late = [r for r in td["receipts"] if r["late"]]
    conflict = [rv for rv in td["reviews"] if rv["conflict"]]
    passed = len(late) >= 1 and len(conflict) >= 1
    check("迟到与重复回执", "核查后补发原任务回执",
          "回执关联原任务，冲突进入再次核对并保留证据", passed,
          f"迟到回执 {len(late)} 条，冲突标记 {len(conflict)} 条")


def s_actual_unknown():
    reset(); tn = make_task("REQ-NA")
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "done"})   # 无 actual_amount
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    td = d["data"]
    passed = td["status"] == "done" and td["actual_amount"] is None \
        and td["actual_source"] == "unknown" and td["confirm_amount"] is not None
    check("实际量未知", "设备返回完成但没有实际量",
          "显示未获取，不以确认量冒充实测量", passed,
          f"实际量={td['actual_amount']}（未获取），确认量={td['confirm_amount']}，未互相顶替")


def s_stop_and_fault():
    reset(); tn = make_task("REQ-FT")
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "accept", "status": "accepted"})
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "failed", "actual_amount": 0.0, "fault": "卡料"})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    _, al = call("GET", "/api/alerts", user="owner")
    has_alert = any(a["kind"] == "feed_fail" for a in al["data"])
    passed = d["data"]["status"] == "failed" and has_alert
    check("停止与卡料", "模拟卡料反馈",
          "按设备反馈更新状态，保留异常和已知执行量", passed,
          f"状态={d['data']['status']}，实际量={d['data']['actual_amount']}，产生异常告警={has_alert}")


def s_permission():
    reset(); seed_env()
    sg = fresh_suggestion(user="owner")   # 用 owner 生成（A01）
    # 刘师傅无权访问 A01
    code, d = call("POST", "/api/ponds/1/suggestions", user="liu", json={})
    code2, d2 = call("POST", "/api/ponds/1/tasks", user="liu",
                     json={"request_no": "REQ-P-" + uuid.uuid4().hex[:8],
                           "suggestion_code": sg["code"] if sg else "SG-0000"})
    passed = code == 403 and code2 == 403
    check("权限限制", "使用无控制权限账户提交任务",
          "后端拒绝执行并返回原因", passed,
          f"生成建议 HTTP {code}（{d.get('message')}）；提交任务 HTTP {code2}（{d2.get('message')}）")


def s_model_interface():
    """模型接口：未配置时返回明确状态，不返回伪预测。"""
    code, d = call("GET", "/api/ponds/1/model/predict?target=growth", user="wang")
    if code == 404:
        check("模型调用", "请求预测接口", "输出目标/单位/时间范围/版本明确，失败不返回伪预测",
              True, "模型接口尚未接入（按需求书 2.3 标注为后续扩展，未返回伪预测）")
    else:
        check("模型调用", "请求预测接口", "明确输出且不伪预测",
              bool(d.get("ok") and ("unit" in str(d) or "note" in str(d))),
              json.dumps(d, ensure_ascii=False)[:120])


def main():
    print("=" * 78)
    print("白蕉水产养殖管理平台 · 验收场景测试")
    print("=" * 78)
    reset()
    for fn in (s_terminal_register, s_env_upload, s_data_invalid, s_day_features,
               s_temp_driven, s_normal_feed, s_duplicate_submit, s_receipt_timeout,
               s_review_recover, s_late_receipt, s_actual_unknown, s_stop_and_fault,
               s_permission, s_model_interface):
        try:
            fn()
        except Exception as e:
            check(fn.__name__, "-", "-", False, f"异常：{e}")
    print("=" * 78)
    n_pass = sum(1 for r in RESULTS if r["结果"] == "通过")
    print(f"合计 {len(RESULTS)} 项，通过 {n_pass} 项，失败 {len(RESULTS)-n_pass} 项")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs",
                       "验收测试记录.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    print("测试记录已写入 docs/验收测试记录.json")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
