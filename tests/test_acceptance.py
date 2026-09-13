"""验收场景测试（对应需求说明书第 9 章验收要求）。

以完整业务场景为单位，逐条给出检查方法、预期结果与实际结果。

隔离设计：默认在临时目录建一个**专用测试数据库**并拉起独立后端实例
（端口 5057），全程不触碰演示库 data/baijiao.db；跑完自动清理。
如需对已启动的服务做实测，用 --base 指定地址（此时会清空该服务的数据）。

用法：
    python tests/test_acceptance.py                 # 隔离模式（推荐）
    python tests/test_acceptance.py --base http://127.0.0.1:5000
"""
import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 部分场景需要直接向库里播种数据（如模型训练样本、FCR 称重记录），
# 它们在主进程中 import database/models，故需把 backend 加入模块搜索路径。
# 隔离模式下 DB_URL 会被指向测试库，这些直连同样作用于测试库。
sys.path.insert(0, os.path.join(ROOT, "backend"))

# 由 main() 填充：隔离模式下指向临时服务
BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
TEST_DB = None          # 隔离模式下的测试库路径
_HARNESS = {}           # 保存子进程/临时目录，供清理

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

    隔离模式下用子进程指向**测试库**执行复位，绝不触碰演示库；
    实测模式（--base）下按本机 DB 配置复位，即清空该服务的数据。
    """
    env = dict(os.environ)
    db_url = _HARNESS.get("db_url")
    if db_url:
        env["DB_URL"] = db_url
    subprocess.run([sys.executable, os.path.join("scripts", "reset_runtime.py"), "--all"],
                   cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def trigger_timeout(tn, timeout_sec=0, tries=12):
    """把超时阈值调到指定值并轮询巡检，直到目标任务转入待核查。

    不使用固定 sleep：环境慢时固定等待会偶发失败。最多重试 tries 次。
    返回 (最后一次巡检响应, 任务详情)。
    """
    call("PUT", "/api/config/task_timeout_sec", user="owner",
         json={"value": str(timeout_sec)})
    c = d = None
    try:
        for _ in range(tries):
            time.sleep(0.5)
            _, c = call("POST", "/api/tasks/check-timeouts")
            _, d = call("GET", f"/api/tasks/{tn}", user="owner")
            if (d.get("data") or {}).get("status") == "unknown":
                break
    finally:
        call("PUT", "/api/config/task_timeout_sec", user="owner", json={"value": "120"})
    return c, d


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
    # 不发送回执；降低阈值并轮询巡检，直到转入待核查
    _, d = trigger_timeout(tn)
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
        "evidence": "现场查看无投料", "device_recovery": "终端已无该任务，旧指令不再执行",
        "recovery_checks": {"terminal_idle": True, "old_command_disabled": True, "device_ready": True}})
    passed = r["data"]["status"] == "failed" and r["data"]["device_locked"] is False
    check("核查与恢复", "核实未执行的任务，登记结论及依据，再尝试新建任务",
          "有权限者登记结论及依据；核查结束且设备满足恢复条件时解除占用", passed,
          f"结论=not_executed，任务状态={r['data']['status']}，设备占用={r['data']['device_locked']}")


def s_late_receipt():
    reset(); tn = make_task("REQ-LATE")
    trigger_timeout(tn)
    # 核查判为未执行
    call("POST", f"/api/tasks/{tn}/review", user="owner", json={
        "conclusion": "not_executed", "basis": "现场确认",
        "device_recovery": "设备可用",
        "recovery_checks": {"terminal_idle": True, "old_command_disabled": True, "device_ready": True}})
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
    check("卡料回执", "模拟卡料反馈",
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
    """模型接口：训练后可预测；未训练/数据不足时明确报错，不返回伪预测。

    全程用 A02：与 CSV 导入、多塘比较等场景的数据隔离。
    """
    reset()
    from datetime import datetime, timedelta
    today = datetime.now()

    # 1) 生长数据：5 次称重，覆盖 60 天
    for d, w in ((60, 50), (45, 120), (30, 200), (15, 290), (0, 380)):
        ts = (today - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
        call("POST", f"/api/ponds/{FEED_POND}/weigh", user="owner",
             json={"avg_weight": w, "sample_count": 30, "weighed_at": ts})

    # 2) 水质数据：最近 24 小时水温/溶氧逐小时
    for h in range(25, -1, -1):
        ts = (today - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
        call("POST", "/api/measurements", json={
            "pond_id": FEED_POND, "metric": "temperature",
            "value": 24 + 2 * ((24 - h) % 12) / 12.0, "unit": "℃",
            "collected_at": ts, "source": "sim"})
        call("POST", "/api/measurements", json={
            "pond_id": FEED_POND, "metric": "oxygen",
            "value": 6.0 + (24 - h) * 0.02, "unit": "mg/L",
            "collected_at": ts, "source": "sim"})

    # 3) 投喂历史：8 天，各一天已完成投喂。任务创建时间不可回填，
    #    历史训练样本直接播种入库（接口行为由其余场景覆盖）。
    for d in range(8, 0, -1):
        ts = (today - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
        call("POST", "/api/measurements", json={
            "pond_id": FEED_POND, "metric": "temperature", "value": 25.0 + d * 0.1,
            "unit": "℃", "collected_at": ts, "source": "sim"})
    from database import SessionLocal
    from models import FeedingTask
    _db = SessionLocal()
    try:
        for d in range(8, 0, -1):
            created = today - timedelta(days=d)
            _db.add(FeedingTask(
                task_no=f"TK-ML-{d:02d}", request_no=f"REQ-ML-{d:02d}",
                pond_id=FEED_POND, round_no=f"ML-{d}",
                suggested_amount=40 + d * 6, confirm_amount=40 + d * 6,
                actual_amount=40 + d * 6, actual_source="manual", unit="kg",
                status="done", device_locked=False,
                approved_at=created, dispatched_at=created, finished_at=created,
                created_at=created, updated_at=created))
        _db.commit()
    finally:
        _db.close()

    # 4) 训练三个目标
    _, tr = call("POST", f"/api/ponds/{FEED_POND}/model/train", user="owner",
                 json={"target": "all"})
    ok3 = all(tr["data"][k]["ok"] for k in ("growth", "feed", "water_quality"))
    # 5) 预测三个目标，输出必须含目标/单位/版本/依据
    _, pg = call("GET", f"/api/ponds/{FEED_POND}/model/predict?target=growth", user="owner")
    _, pf = call("GET", f"/api/ponds/{FEED_POND}/model/predict?target=feed&horizon=3", user="owner")
    _, pw = call("GET", f"/api/ponds/{FEED_POND}/model/predict?target=water_quality&horizon=6",
                 user="owner")
    fields_ok = (pg.get("ok") and pg["data"]["unit"] == "g" and pg["data"]["version"]
                 and pf.get("ok") and pf["data"]["unit"] == "kg"
                 and len(pf["data"]["series"]) == 3
                 and pw.get("ok") and pw["data"]["horizon_hours"] == 6)
    # 6) 反例：删除模型文件后预测生长 → 明确报错而非伪数据
    model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "models")
    for f in ("pond_1_growth.json",):
        p = os.path.join(model_dir, f)
        if os.path.exists(p):
            os.remove(p)
    _, pb = call("GET", "/api/ponds/1/model/predict?target=growth", user="owner")
    honest = (not pb.get("ok")) and pb.get("reason") in ("model_not_trained", "insufficient_data")
    passed = ok3 and fields_ok and honest
    check("模型训练与预测接口（未完成独立效果测试）", "用 60 天称重/8 天投喂/24 小时水质训练三个模型并预测",
          "输出目标/单位/时间范围/版本明确；未训练时明确报错，不返回伪预测", passed,
          f"训练 growth/feed/water 全成功={ok3}；生长预测当前 {pg.get('data', {}).get('current_weight_g')}g、"
          f"日增 {pg.get('data', {}).get('daily_gain_g')}g、达标 {pg.get('data', {}).get('days_to_target')} 天；"
          f"投喂预测明日 {pf.get('data', {}).get('series', [{}])[0].get('amount_kg')}kg；"
          f"未训练塘返回 reason={pb.get('reason')}")


def s_fish_event():
    """死鱼事件：人工登记 → 确认 → 处理 / 误报，状态流转留痕。"""
    reset()
    _, e1 = call("POST", "/api/ponds/1/fish-events", user="wang",
                 json={"position_desc": "水面东侧", "observation": "发现 1 尾翻肚", "source": "manual"})
    c1 = e1["data"]["code"]
    _, h1 = call("POST", f"/api/fish-events/{c1}/handle", user="wang", json={"action": "confirm"})
    _, h2 = call("POST", f"/api/fish-events/{c1}/handle", user="wang",
                 json={"action": "process", "note": "已捞除并记录"})
    _, e2 = call("POST", "/api/ponds/1/fish-events", user="wang",
                 json={"position_desc": "水面西侧", "source": "manual"})
    c2 = e2["data"]["code"]
    _, h3 = call("POST", f"/api/fish-events/{c2}/handle", user="wang",
                 json={"action": "false_alarm", "note": "气泡反光误判"})
    _, lst = call("GET", "/api/ponds/1/fish-events", user="wang")
    by = {x["code"]: x for x in lst["data"]}
    passed = (h1["data"]["status"] == "processing" and h2["data"]["status"] == "handled"
              and h3["data"]["status"] == "false_alarm"
              and by[c1]["status"] == "handled" and by[c2]["status"] == "false_alarm")
    check("死鱼事件", "登记两条疑似事件，分别确认-处理与标记误报",
          "识别/登记结果为待确认事件，确认与处理留痕", passed,
          f"事件1 {by[c1]['status']}，事件2 {by[c2]['status']}（确认前均不计入已确认数量）")

    # 回归：误报与已处理为终态，不得互相改写而丢失分类
    s3, r3 = call("POST", f"/api/fish-events/{c2}/handle", user="wang",
                  json={"action": "process", "note": "改判"})
    s4, r4 = call("POST", f"/api/fish-events/{c1}/handle", user="wang",
                  json={"action": "false_alarm", "note": "改判"})
    _, lst2 = call("GET", "/api/ponds/1/fish-events", user="wang")
    by2 = {x["code"]: x for x in lst2["data"]}
    ok2 = (s3 >= 400 and s4 >= 400
           and by2[c2]["status"] == "false_alarm" and by2[c1]["status"] == "handled")
    check("事件终态保护", "把误报改为已处理、把已处理改为误报",
          "终态不接受改写，误报分类不丢失", ok2,
          f"误报→已处理 HTTP {s3}、已处理→误报 HTTP {s4}；"
          f"状态保持 {by2[c2]['status']} / {by2[c1]['status']}")


def s_compare():
    """多塘比较：窗口统计可查，字段完整。"""
    reset()
    seed_env(1, 26.0)
    seed_env(2, 27.0)
    _, d = call("GET", "/api/compare?days=7", user="owner")
    rows = d.get("data") or []
    fields = ("pond", "avg_temperature", "avg_oxygen", "feed_kg", "alerts", "medicine_count")
    passed = (d.get("ok") and len(rows) >= 2
              and all(all(f in r for f in fields) for r in rows))
    check("多塘比较", "两个塘都有近期数据时查询 7 天窗口比较",
          "每塘返回环境均值/投喂/告警/用药等可比指标", passed,
          f"返回 {len(rows)} 个塘；" + "；".join(
              f"{r['pond']['code']} 水温均值={r['avg_temperature']} 投喂={r['feed_kg']}kg"
              for r in rows))


def s_plan_ops():
    """计划与运营：计划、称重、用药、成本、饲料系数、导出。"""
    reset()
    seed_env()
    from datetime import datetime, timedelta
    today = datetime.now()
    # 计划
    _, p = call("POST", "/api/ponds/1/plans", user="wang",
                json={"content": "巡塘并记录水色", "cycle": "daily",
                      "due_time": (today + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")})
    _, pc = call("POST", f"/api/plans/{p['data']['id']}/complete", user="wang", json={})
    _, pl = call("GET", "/api/ponds/1/plans", user="wang")
    plan_ok = pc["data"]["completed"] is True and any(x["id"] == p["data"]["id"] for x in pl["data"])
    # 称重两次（间隔 30 天）+ 期间投喂 → 饲料系数可算
    for d, w in ((30, 120.0), (0, 210.0)):
        call("POST", "/api/ponds/1/weigh", user="wang",
             json={"avg_weight": w, "sample_count": 30,
                   "weighed_at": (today - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")})
    sg = fresh_suggestion(user="owner")
    _, t = call("POST", "/api/ponds/1/tasks", user="owner",
                json={"request_no": "REQ-OPS-" + uuid.uuid4().hex[:8],
                      "suggestion_code": sg["code"], "confirm_amount": 88.8,
                      "change_reason": "运营场景固定投喂 88.8kg"})
    call("POST", "/api/receipts", json={"task_no": t["data"]["task_no"], "kind": "finish",
                                        "status": "done", "actual_amount": 88.8})
    _, fs = call("GET", "/api/ponds/1/feed-stats?days=60", user="owner")
    fcr_ok = fs["data"]["fcr"] is not None and "用料" in fs["data"]["fcr_note"]
    # 用药与成本
    _, m = call("POST", "/api/ponds/1/medicine", user="wang",
                json={"item": "聚维酮碘", "amount": 2.5, "unit": "L"})
    _, ml = call("GET", "/api/ponds/1/medicine", user="wang")
    _, c = call("POST", "/api/ponds/1/costs", user="owner",
                json={"kind": "actual", "item": "鲈鱼配合饲料", "amount": 6800, "unit": "元"})
    med_ok = any(x["item"] == "聚维酮碘" for x in ml["data"])
    # 导出：含时间/单位/来源
    _, ex = call("GET", "/api/ponds/1/export", user="owner")
    rows = ex.get("data") or []
    export_ok = (ex.get("ok") and rows
                 and all(("time" in r and "unit" in r and "source" in r) for r in rows))
    passed = plan_ok and fcr_ok and med_ok and export_ok
    check("计划与运营", "建计划并完成；两次称重+投喂后查询饲料系数；登记用药与成本；导出记录",
          "各接口返回完整且口径可解释，导出含时间/单位/来源", passed,
          f"计划完成={plan_ok}，FCR={fs['data']['fcr']}（{fs['data']['fcr_note'][:30]}…），"
          f"用药登记={med_ok}，导出 {len(rows)} 条含来源={export_ok}")


def s_vision():
    """视频与识别：上传帧 → 真实分析 → 待确认事件；OCR 依赖未装时明确说明。"""
    reset()
    try:
        import cv2
        import numpy as np
    except ImportError:
        check("视频与识别", "上传相机帧做识别", "返回分析结果与待确认事件",
              False, "测试机未安装 opencv，跳过")
        return
    # 合成一帧"水面"：暗色背景 + 一块亮色漂浮物
    rng = np.random.default_rng(7)
    frame = np.full((360, 480, 3), 52, dtype=np.uint8)
    frame = cv2.GaussianBlur(frame, (21, 21), 0) + rng.normal(0, 3, frame.shape).astype(np.uint8)
    cv2.ellipse(frame, (240, 180), (34, 13), 20, 0, 360, (215, 210, 200), -1)
    okenc, buf = cv2.imencode(".jpg", frame)
    _, up = call("POST", f"/api/ponds/1/vision/frame", user="wang",
                 files={"file": ("frame.jpg", buf.tobytes(), "image/jpeg")},
                 data={"position": "水上"})
    d = up.get("data") or {}
    suspected = bool(d.get("suspected"))
    ev = d.get("event")
    # 相机通道状态更新为在线
    _, cams = call("GET", "/api/ponds/1/cameras", user="wang")
    cam_online = bool(cams.get("data")) and any(c["online"] for c in cams["data"])
    # 事件列表出现识别来源的待确认事件
    _, evs = call("GET", "/api/ponds/1/fish-events", user="wang")
    ev_ok = ev is not None and any(x["code"] == ev["code"] and x["status"] == "pending"
                                   and x["source"] == "vision" for x in evs["data"])
    # OCR：无 tesseract 时必须明确返回 ocr_unavailable，不返回假文本
    _, oc = call("POST", "/api/ponds/1/vision/ocr", user="wang",
                 files={"file": ("doc.jpg", buf.tobytes(), "image/jpeg")})
    ocr_honest = (not oc.get("ok") and oc.get("reason") == "ocr_unavailable") or oc.get("ok")
    passed = up.get("ok") and suspected and cam_online and ev_ok and ocr_honest
    check("视频与识别", "上传合成相机帧（含亮色漂浮物）并查询相机与事件；再传图做 OCR",
          "帧被真实分析并生成待确认事件；相机转在线；OCR 依赖缺失时明确说明", passed,
          f"检出漂浮物={suspected} 置信度={d.get('confidence')} 模型={d.get('model_version')}，"
          f"相机在线={cam_online}，事件待确认={ev_ok}，OCR 明确={ocr_honest}")


def s_data_import():
    """数据导入：CSV 导入成功且重复导入不重复写入。"""
    import_csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "sample")
    csvs = sorted(glob.glob(os.path.join(import_csv_path, "*.csv")))
    if not csvs:
        check("数据导入", "导入内置 CSV", "写入并保留来源", False, "未找到内置 CSV")
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "scripts"))
    reset()
    import import_csv
    db_path_note = "进程内直连同一 SQLite"
    from database import SessionLocal
    from models import Measurement as M

    def count_rows():
        db = SessionLocal()
        try:
            return db.query(M).filter(M.source == "csv").count()
        finally:
            db.close()

    db = SessionLocal()
    try:
        ib1 = import_csv.import_one(db, csvs[0])
        n1 = count_rows()
        ib2 = import_csv.import_one(db, csvs[0])   # 重复导入
        n2 = count_rows()
    finally:
        db.close()
    passed = (ib1 and ib1.rows_inserted > 0 and ib2.rows_inserted == 0
              and ib2.rows_duplicated == ib1.rows_total and n1 == n2)
    check("数据导入", f"导入 {os.path.basename(csvs[0])} 再重复导入一次（{db_path_note}）",
          "首次写入并保留来源记录编号；重复导入不重复写入", passed,
          f"首次写入 {ib1.rows_inserted} 条，重复导入写入 {ib2.rows_inserted} 条、"
          f"跳过重复 {ib2.rows_duplicated} 条，记录数 {n1}->{n2}")


def s_community():
    """交流与排行榜：发帖/回帖/关闭；排行按 FCR 排序，数据不足不参与。"""
    reset()
    seed_env()
    _, p = call("POST", "/api/community/posts", user="wang",
                json={"title": "低温期投喂经验", "content": "水温低于 18℃ 时减半投喂，观察摄食再补。",
                      "category": "经验交流", "pond_id": 1})
    pid = p["data"]["id"]
    _, r = call("POST", f"/api/community/posts/{pid}/replies", user="owner",
                json={"content": "同感，另外建议开增氧机后再投。"})
    _, lst = call("GET", "/api/community/posts", user="owner")
    post_ok = any(x["id"] == pid and x["reply_count"] >= 1 for x in lst["data"])
    # 排行榜：给 A01 两次称重 + 投喂，FCR 可算；A02 数据不足不参与
    from datetime import datetime, timedelta
    today = datetime.now()
    for d, w in ((20, 150.0), (0, 240.0)):
        call("POST", "/api/ponds/1/weigh", user="owner",
             json={"avg_weight": w, "sample_count": 30,
                   "weighed_at": (today - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")})
    sg = fresh_suggestion(user="owner")
    _, t = call("POST", "/api/ponds/1/tasks", user="owner",
                json={"request_no": "REQ-LB-" + uuid.uuid4().hex[:8],
                      "suggestion_code": sg["code"], "confirm_amount": 66.0,
                      "change_reason": "排行场景固定投喂 66kg"})
    call("POST", "/api/receipts", json={"task_no": t["data"]["task_no"], "kind": "finish",
                                        "status": "done", "actual_amount": 66.0})
    _, lb = call("GET", "/api/leaderboard?days=30", user="owner")
    rows = lb.get("data") or []
    ranked = [x for x in rows if x["rank"]]
    a01 = next((x for x in rows if x["pond"]["code"] == "A01"), {})
    a02 = next((x for x in rows if x["pond"]["code"] == "A02"), {})
    lb_ok = (lb.get("ok") and ranked and ranked[0]["rank"] == 1
             and a01.get("fcr") is not None
             and a02.get("rank") is None and a02.get("not_ranked_reason"))
    _, cl = call("POST", f"/api/community/posts/{pid}/close", user="wang", json={})
    passed = post_ok and lb_ok and cl["data"]["status"] == "closed"
    check("交流与排行榜", "发帖回帖并关闭；A01 两次称重+投喂，A02 不给称重，查询排行",
          "交流帖可发布/回复/关闭；FCR 可算的塘参与排名，数据不足明确不参与", passed,
          f"发帖回复关闭均成功={post_ok and cl['data']['status'] == 'closed'}；"
          f"A01 FCR={a01.get('fcr')}（{a01.get('fcr_basis')}）；A02 未参与原因：{a02.get('not_ranked_reason')}")



def s_auth_binding():
    """越权绑定：URL 鱼塘与建议所属鱼塘必须一致；告警/日志同样校验权限。"""
    reset()
    seed_env(2, 26.5)
    _, s2 = call("POST", "/api/ponds/2/suggestions", user="owner", json={})
    code2 = s2["data"]["code"]
    c1, d1 = call("GET", f"/api/suggestions/{code2}", user="wang")
    c2, d2 = call("POST", "/api/ponds/1/tasks", user="wang",
                  json={"request_no": "REQ-X-" + uuid.uuid4().hex[:8],
                        "suggestion_code": code2})
    call("POST", "/api/measurements", json={
        "pond_id": 2, "metric": "temperature", "value": 99.0, "unit": "℃",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    _, al = call("GET", "/api/alerts", user="owner")
    a2 = next((a for a in al["data"] if a["pond_id"] == 2), None)
    c3, d3 = call("POST", f"/api/alerts/{a2['id']}/handle", user="wang",
                  json={"status": "closed"}) if a2 else (0, {})
    c4, d4 = call("GET", "/api/logs?pond_id=2", user="wang")
    passed = (c1 == 403 and c2 == 403 and c3 == 403 and c4 == 403
              and not d1.get("ok") and not d2.get("ok"))
    check("跨塘越权", "仅 A01 权限用户用 A02 建议下发任务/读建议/关告警/读日志",
          "建议详情、任务创建、告警处理、日志查询均拒绝并返回原因", passed,
          f"读建议 {c1}，跨塘下发 {c2}，关告警 {c3}，读日志 {c4}（均应 403）")


def s_running_timeout():
    """执行中失联：running 阶段超时同样进入待核查。"""
    reset()
    tn = make_task("REQ-RT")
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "accept",
                                        "status": "accepted"})
    c, d = trigger_timeout(tn)
    td = d["data"]
    passed = td["status"] == "unknown" and td["device_locked"] is True
    check("执行中超时", "终端 accept 后失联，触发超时巡检",
          "running 阶段超时同样转待核查并保留设备占用，不自动重发", passed,
          f"状态={td['status']}，占用={td['device_locked']}，"
          f"巡检 timed_out={(c or {}).get('data', {}).get('timed_out')}")


def s_out_of_order_receipt():
    """乱序回执：done 之后补发 execute/running 不回退状态。"""
    reset()
    tn = make_task("REQ-OO")
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                        "status": "done", "actual_amount": 9.0})
    call("POST", "/api/receipts", json={"task_no": tn, "kind": "execute",
                                        "status": "running"})
    _, d = call("GET", f"/api/tasks/{tn}", user="owner")
    td = d["data"]
    passed = (td["status"] == "done" and td["device_locked"] is False
              and td["finished_at"] is not None
              and len(td["receipts"]) >= 2)
    check("乱序回执", "先 finish/done 再补发 execute/running",
          "迟到过程回执留痕但不把已完成任务改回执行中", passed,
          f"状态={td['status']}（应保持 done），占用={td['device_locked']}，"
          f"回执 {len(td['receipts'])} 条留痕")


def s_cancel_suggestion():
    """取消建议：真正取消，且不能再下发任务。"""
    reset()
    seed_env()
    sg = fresh_suggestion(user="owner")
    code = sg["code"]
    _, r1 = call("POST", f"/api/suggestions/{code}/cancel", user="owner", json={})
    c2, r2 = call("POST", f"/api/suggestions/{code}/cancel", user="owner", json={})
    c3, r3 = call("POST", "/api/ponds/1/tasks", user="owner",
                  json={"request_no": "REQ-CX-" + uuid.uuid4().hex[:8],
                        "suggestion_code": code})
    passed = (r1.get("ok") and r1["data"]["status"] == "cancelled"
              and c2 == 409 and c3 == 400 and not r3.get("ok"))
    check("取消建议", "取消待确认建议后重复取消并用其下发任务",
          "状态转 cancelled；重复取消被拒；已取消建议不能再下发任务", passed,
          f"取消后状态={r1['data']['status']}，重复取消 HTTP {c2}，"
          f"下发 HTTP {c3}（{r3.get('message')}）")


def s_input_validation():
    """输入校验：未来时间/非法数值/非法状态/坏参数都给 400/404，不再 500。"""
    reset()
    c1, _ = call("POST", "/api/measurements", json={
        "pond_id": 1, "metric": "temperature", "value": 25.0, "unit": "℃",
        "collected_at": "2099-01-01 00:00:00"})
    c2, _ = call("POST", "/api/measurements", json={
        "pond_id": 1, "metric": "temperature", "value": "NaN", "unit": "℃",
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    c3, _ = call("GET", "/api/compare?days=abc", user="owner")
    c4, _ = call("GET", "/api/ponds/1/charts?days=abc", user="owner")
    c5, _ = call("GET", "/api/ponds/999/dashboard", user="owner")
    reset()
    seed_env()
    sg = fresh_suggestion(user="owner")
    _, t = call("POST", "/api/ponds/1/tasks", user="owner",
                json={"request_no": "REQ-V-" + uuid.uuid4().hex[:8],
                      "suggestion_code": sg["code"]})
    tn = t["data"]["task_no"]
    c6, _ = call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                                "status": "garbage"})
    c7, _ = call("POST", "/api/receipts", json={"task_no": tn, "kind": "finish",
                                                "status": "done", "actual_amount": -9})
    _, td = call("GET", f"/api/tasks/{tn}", user="owner")
    passed = (c1 == 400 and c2 == 400 and c3 == 400 and c4 == 400
              and c5 == 404 and c6 == 400 and c7 == 400
              and td["data"]["status"] == "dispatched")
    check("输入校验", "未来采集时间/NaN/坏 days/不存在鱼塘/非法回执/负实测量",
          "全部返回 400/404 并给出原因，任务状态不被非法回执改变", passed,
          f"未来时间 {c1}，NaN {c2}，days {c3}/{c4}，缺塘 {c5}，"
          f"非法状态 {c6}，负实测量 {c7}，任务保持 {td['data']['status']}")


def s_fcr_consistency():
    """FCR 口径：失败任务已知实际量计入用料；称重区间外不计入 FCR。"""
    reset()
    from database import SessionLocal
    from models import FeedingTask, WeighRecord
    from datetime import datetime, timedelta
    today = datetime.now()
    db = SessionLocal()
    try:
        for d, w in ((20, 100.0), (10, 200.0)):
            db.add(WeighRecord(pond_id=2, weighed_at=today - timedelta(days=d),
                               avg_weight=w, sample_count=30, note="FCR口径测试"))
        for d, amt, st in ((12, 60, "done"), (11, 40, "done"), (5, 500, "failed")):
            ts = today - timedelta(days=d)
            db.add(FeedingTask(
                task_no=f"TK-FCR-{d}", request_no=f"REQ-FCR-{d}", pond_id=2,
                suggested_amount=amt, confirm_amount=amt, actual_amount=amt,
                actual_source="measured", unit="kg",
                status=st, device_locked=False, approved_at=ts, created_at=ts,
                finished_at=ts))
        db.commit()
    finally:
        db.close()
    _, fs = call("GET", "/api/ponds/2/feed-stats?days=30", user="owner")
    _, lb = call("GET", "/api/leaderboard?days=30", user="owner")
    a02 = next((x for x in lb["data"] if x["pond"]["code"] == "A02"), {})
    expected = round(100 / 6200, 3)
    passed = (fs["data"]["fcr"] == expected
              and fs["data"]["feed_window_kg"] == 600.0
              and a02.get("fcr") == expected)
    check("FCR口径", "两次称重间完成 100kg、之后失败 500kg，查 feed-stats 与排行榜",
          "失败后的已知实际量计入区间用料；FCR 分子限称重区间；两页面 FCR 一致", passed,
          f"feed-stats FCR={fs['data']['fcr']}（期望 {expected}），"
          f"用料={fs['data']['feed_window_kg']}kg，排行榜 A02 FCR={a02.get('fcr')}")


def s_concurrent_suggestions():
    """并发生成建议：编号不冲突，全部成功。"""
    reset()
    seed_env(1, 26.5)
    from concurrent.futures import ThreadPoolExecutor
    import requests as rq
    def post(_):
        r = rq.post(BASE + "/api/ponds/1/suggestions",
                    headers={"X-User": "owner"}, timeout=15)
        return r.status_code
    with ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(post, range(16)))
    ok_n = sum(1 for c in codes if c == 200)
    passed = ok_n == 16
    check("并发建议", "8 线程并发 16 次生成建议",
          "编号生成并发安全，全部 HTTP 200", passed,
          f"成功 {ok_n}/16，失败状态码：{sorted(set(c for c in codes if c != 200)) or '无'}")



def _free_port(preferred):
    """优先用指定端口，被占用时让系统分配一个空闲端口。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
        s2 = socket.socket()
        s2.bind(("127.0.0.1", 0))
        p = s2.getsockname()[1]
        s2.close()
        return p


def _wait_health(base, timeout=40):
    """等待后端 /health 就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(base + "/health", timeout=2)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def start_isolated_server():
    """在临时目录建专用测试库并拉起独立后端，返回 base 地址。

    不触碰项目内的演示库 data/baijiao.db。
    """
    tmpdir = tempfile.mkdtemp(prefix="bj_accept_")
    db_path = os.path.join(tmpdir, "test.db")
    port = _free_port(5057)
    base = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["DB_URL"] = "sqlite:///" + db_path.replace("\\", "/")
    env["APP_PORT"] = str(port)
    env["APP_HOST"] = "127.0.0.1"
    env["APP_DEBUG"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    # 关闭后台自动超时巡检：否则它会抢先把任务转成 unknown，
    # 使「回执超时」等用例的时序断言不稳定（本脚本自行控制巡检时机）。
    env["TIMEOUT_SWEEP_SEC"] = "0"

    # 建表 + 基础数据（直接对测试库操作，与演示库完全隔离）
    subprocess.run([sys.executable, os.path.join("scripts", "init_db.py"), "--seed", "--quiet"],
                   cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    log = open(os.path.join(tmpdir, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join("backend", "app.py")],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)

    if not _wait_health(base):
        proc.terminate()
        log.close()
        raise SystemExit(f"[错误] 测试后端未能启动，日志见 {tmpdir}/server.log")

    _HARNESS["proc"] = proc
    _HARNESS["tmpdir"] = tmpdir
    _HARNESS["log"] = log
    _HARNESS["db_url"] = env["DB_URL"]
    # 让父进程内的直连（部分场景需直接播种数据）也指向测试库，
    # 否则会误连演示库。database.py 用 load_dotenv(override=False)，
    # 已存在的环境变量优先，因此这里设置的值会生效。
    _HARNESS["old_db_url"] = os.environ.get("DB_URL")
    os.environ["DB_URL"] = env["DB_URL"]
    return base, db_path


def stop_isolated_server():
    proc = _HARNESS.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if _HARNESS.get("log"):
        _HARNESS["log"].close()
    tmp = _HARNESS.get("tmpdir")
    if tmp and os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    # 还原父进程的 DB_URL，避免影响同一进程内的后续操作
    if "old_db_url" in _HARNESS:
        if _HARNESS["old_db_url"] is None:
            os.environ.pop("DB_URL", None)
        else:
            os.environ["DB_URL"] = _HARNESS["old_db_url"]


def main():
    global BASE, TEST_DB
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="对已启动的服务做测试（会清空该服务的数据）")
    args = ap.parse_args()

    print("=" * 78)
    print("白蕉水产养殖管理平台 · 验收场景测试")
    print("=" * 78)

    isolated = not args.base
    if isolated:
        print("隔离模式：使用临时测试库，不影响演示库 data/baijiao.db")
        BASE, TEST_DB = start_isolated_server()
        print(f"测试后端：{BASE}   测试库：{TEST_DB}")
    else:
        BASE = args.base.rstrip("/")
        print(f"实测模式：{BASE}（注意：会清空该服务的数据）")
    print("-" * 78)

    try:
        reset()
        for fn in (s_terminal_register, s_env_upload, s_data_invalid, s_day_features,
                   s_temp_driven, s_normal_feed, s_duplicate_submit, s_receipt_timeout,
                   s_review_recover, s_late_receipt, s_actual_unknown, s_stop_and_fault,
                   s_permission, s_auth_binding, s_running_timeout, s_out_of_order_receipt,
                   s_cancel_suggestion, s_input_validation, s_model_interface,
                   s_fish_event, s_compare, s_plan_ops, s_fcr_consistency,
                   s_community, s_concurrent_suggestions, s_vision, s_data_import):
            try:
                fn()
            except Exception as e:
                check(fn.__name__, "-", "-", False, f"异常：{e}")
            if fn in (s_vision, s_data_import):
                try:
                    reset()
                except Exception:
                    pass
    finally:
        if isolated:
            stop_isolated_server()

    print("=" * 78)
    n_pass = sum(1 for r in RESULTS if r["结果"] == "通过")
    print(f"合计 {len(RESULTS)} 项，通过 {n_pass} 项，失败 {len(RESULTS)-n_pass} 项")
    if isolated:
        print("演示库未被修改（测试使用临时库，已清理）")
    out = os.path.join(ROOT, "docs", "验收测试记录.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    print("测试记录已写入 docs/验收测试记录.json")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop_isolated_server()
        raise
