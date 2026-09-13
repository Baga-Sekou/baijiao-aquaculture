"""采集控制终端仿真器（需求说明书 2.3：实训可用仿真设备验证相同流程）。

职责（与真实终端一致）：
- 向内注册身份、关联鱼塘与设备；周期心跳。
- 周期采集环境数据并上传（可用真实串口/TCP 传感器，否则用内置模拟器）。
- 拉取后端下发的投喂任务，按任务号去重（重连/重启后仍能识别已接收任务）。
- 回传 accept / execute / finish / stop 回执；可模拟卡料、回执超时、实际量缺失。

故障注入（演示用）：
    --fault stuck        投喂中卡料，返回 failed
    --fault no_actual    完成但不上报实际量（实际量=未获取）
    --fault timeout      接收后不回执（触发后端超时->待核查）
    --fault slow_stop    不支持远程停止

用法：
    python simulator/terminal.py --code TERM-A01 --pond 1
    python simulator/terminal.py --code TERM-A01 --pond 1 --fault stuck
"""
import argparse
import hashlib
from pathlib import Path

try:
    from .task_journal import TaskJournal
except ImportError:
    from task_journal import TaskJournal
import json
import os
import random
import sys
import threading
import time
from datetime import datetime

import requests

BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")


def make_base(port):
    """给内嵌仿真用：指向本机同一进程监听的端口。"""
    return f"http://127.0.0.1:{port}"


# 模拟传感器基线（演示参数；真实接入时替换为串口/TCP 读数）
SIM_BASE = {"temperature": 24.0, "oxygen": 6.5, "ph": 7.1, "salinity": 0.22}
SIM_UNIT = {"temperature": "℃", "oxygen": "mg/L", "ph": "", "salinity": ""}
SIM_AMP = {"temperature": 3.5, "oxygen": 1.2, "ph": 0.15, "salinity": 0.02}


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


class Terminal:
    def __init__(self, code, pond_id, fault=None, interval=10, dry_run=False,
                 base=None, state_dir=None, execution_seconds=2):
        self.code = code
        self.pond_id = pond_id
        self.fault = fault
        self.interval = interval
        self.dry_run = dry_run
        self.base = base or BASE
        # 重启去重日志：按 (后端地址, 终端编号) 分文件，避免多终端互相覆盖
        state_dir = Path(state_dir or Path(__file__).resolve().parents[1] / "data" / "terminal-state")
        key = hashlib.sha256(f"{self.base}|{code}".encode()).hexdigest()[:24]
        self.journal = TaskJournal(state_dir / f"{key}.sqlite")
        self.execution_seconds = execution_seconds
        self.active_request = None
        self.stopped = threading.Event()
        self.rng = random.Random(hash(code) & 0xffff)

    # ---------------------------------------------------------- 注册
    def register(self):
        r = requests.post(f"{self.base}/api/terminals/register",
                          json={"code": self.code, "pond_id": self.pond_id,
                                "ip": "127.0.0.1",
                                "devices": ["S-TEMP", "S-OXY", "S-PH", "FEED"]}, timeout=5)
        log("注册:", r.status_code, r.json().get("data", {}).get("status"))
        return r.ok

    def heartbeat(self):
        try:
            requests.post(f"{self.base}/api/terminals/{self.code}/heartbeat", timeout=5)
        except requests.RequestException:
            pass

    # ---------------------------------------------------------- 采集
    def read_metric(self, metric):
        """模拟读数。真实场景：从串口(RS232)或 TCP 读取并解析。"""
        base = SIM_BASE[metric]
        amp = SIM_AMP[metric]
        # 用当日小时做正弦，模拟日内变化
        h = datetime.now().hour
        val = base + amp * random.uniform(-1, 1) * 0.4 + amp * 0.5 * (h - 12) / 12 * -1
        return round(val, 2)

    def collect_once(self):
        for metric in ("temperature", "oxygen", "ph"):
            payload = {
                "pond_id": self.pond_id, "metric": metric,
                "value": self.read_metric(metric), "unit": SIM_UNIT[metric],
                "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "terminal_code": self.code, "source": "sim",
            }
            try:
                r = requests.post(f"{self.base}/api/measurements", json=payload, timeout=5)
                if r.ok:
                    d = r.json()
                else:
                    log("采集被拒:", r.json().get("message"))
            except requests.RequestException as e:
                log("采集上传失败:", e)

    # ---------------------------------------------------------- 任务
    def poll_tasks(self):
        try:
            r = requests.get(f"{self.base}/api/terminals/{self.code}/tasks", timeout=5)
            if not r.ok:
                return []
            return r.json().get("data", [])
        except requests.RequestException:
            return []

    def receipt(self, task_no, kind, status, actual=None, fault=None):
        payload = {"task_no": task_no, "kind": kind, "status": status,
                   "actual_amount": actual, "fault": fault,
                   # 声明来源为仿真：后端据此把实际量标为 simulated，
                   # 不冒充实测值（需求书 5.3）
                   "source": "simulated",
                   "occurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if kind in ("finish", "stop") and self.active_request:
            self.journal.finish(self.active_request, payload)
        return self.send_receipt(payload)

    def send_receipt(self, payload):
        try:
            r = requests.post(f"{self.base}/api/receipts", json=payload, timeout=5)
            return r.ok and r.json().get("ok") is True
        except requests.RequestException:
            return False

    def handle_task(self, task):
        no = task["task_no"]
        request_no = task.get("request_no")
        if not request_no:
            log(f"拒绝任务 {no}：缺少稳定请求编号")
            return
        # 认领落盘后才能执行；重启后只补发已保存的结果，未定结果留待人工核查。
        if not self.journal.claim(request_no, no):
            saved = self.journal.receipt(request_no)
            if saved:
                self.send_receipt(saved)
            return
        self.active_request = request_no
        log(f"接收任务 {no}: 确认量 {task['confirm_amount']}{task['unit']}")

        # 接收回执
        self.receipt(no, "accept", "accepted")
        time.sleep(0.3)

        if self.fault == "timeout":
            log(f" [{no}] 注入故障：接收后不回执（等待后端判超时）")
            return

        # 执行中
        self.receipt(no, "execute", "running")
        deadline = time.monotonic() + self.execution_seconds
        while True:
            wants_stop = task.get("stop_requested") or self.fault == "stop"
            if wants_stop:
                if self.fault == "slow_stop":
                    log(f" [{no}] 设备不支持远程停止，等待现场处理/后端超时核查")
                    return
                self.receipt(no, "stop", "stopped", actual=None)
                log(f" [{no}] 已停止（实际量未获取）")
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
            current = next((x for x in self.poll_tasks() if x["task_no"] == no), None)
            if current:
                task = current

        if self.fault == "stuck":
            self.receipt(no, "finish", "failed", actual=0.0, fault="卡料")
            log(f" [{no}] 执行失败：卡料")
            return

        amount = task["confirm_amount"]
        if self.fault == "no_actual":
            self.receipt(no, "finish", "done", actual=None)
            log(f" [{no}] 完成（实际量未获取）")
        else:
            # 实测投喂量在确认量附近波动
            actual = round(amount * self.rng.uniform(0.97, 1.0), 2)
            self.receipt(no, "finish", "done", actual=actual)
            log(f" [{no}] 完成，实测投喂 {actual}{task['unit']}")

    # ---------------------------------------------------------- 主循环
    def run(self, dur_sec=None):
        if not self.dry_run and not self.register():
            log("注册失败，退出")
            return
        log(f"终端 {self.code} 启动（鱼塘 {self.pond_id}，故障注入={self.fault or '无'}）")
        t0 = time.time()
        cycle = 0
        while not self.stopped.is_set():
            if dur_sec and time.time() - t0 > dur_sec:
                break
            cycle += 1
            self.heartbeat()
            # 采集（每 3 个循环一次，避免刷屏）
            if cycle % 3 == 0:
                self.collect_once()
            for task in self.poll_tasks():
                self.handle_task(task)
            time.sleep(self.interval)
        self.stop()
        log("终端退出")

    def stop(self):
        """置离线。失败时重试一次并如实记录，不静默吞掉——
        否则会出现「一个终端已离线、另一个仍在线」的状态不一致。"""
        self.stopped.set()
        for attempt in range(2):
            try:
                r = requests.post(f"{self.base}/api/terminals/{self.code}/offline", timeout=5)
                if r.ok:
                    return True
                log(f"下线返回 {r.status_code}，重试中")
            except requests.RequestException as e:
                log(f"下线失败（第 {attempt + 1} 次）: {e}")
            time.sleep(0.3)
        log(f"[警告] 终端 {self.code} 未能确认离线，请检查后端")
        return False


def main():
    ap = argparse.ArgumentParser(description="采集控制终端仿真器")
    ap.add_argument("--code", default="TERM-A01")
    ap.add_argument("--pond", type=int, default=1)
    ap.add_argument("--interval", type=float, default=10, help="轮询间隔（秒）")
    ap.add_argument("--fault", choices=["stuck", "no_actual", "timeout", "slow_stop", "stop"])
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒）")
    ap.add_argument("--once", action="store_true", help="只跑一轮采集")
    args = ap.parse_args()

    t = Terminal(args.code, args.pond, fault=args.fault, interval=args.interval)
    if args.once:
        t.register()
        t.collect_once()
        print()
        log("完成一轮采集")
        return
    try:
        t.run(dur_sec=args.duration)
    except KeyboardInterrupt:
        t.stop()


if __name__ == "__main__":
    main()
