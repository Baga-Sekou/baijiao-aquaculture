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
import json
import os
import random
import sys
import threading
import time
from datetime import datetime

import requests

BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")

# 模拟传感器基线（演示参数；真实接入时替换为串口/TCP 读数）
SIM_BASE = {"temperature": 24.0, "oxygen": 6.5, "ph": 7.1, "salinity": 0.22}
SIM_UNIT = {"temperature": "℃", "oxygen": "mg/L", "ph": "", "salinity": ""}
SIM_AMP = {"temperature": 3.5, "oxygen": 1.2, "ph": 0.15, "salinity": 0.02}


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


class Terminal:
    def __init__(self, code, pond_id, fault=None, interval=10, dry_run=False):
        self.code = code
        self.pond_id = pond_id
        self.fault = fault
        self.interval = interval
        self.dry_run = dry_run
        self.seen_tasks = set()          # 已接收任务号，用于去重（模拟断电重启后从本地恢复）
        self.stopped = threading.Event()
        self.rng = random.Random(hash(code) & 0xffff)

    # ---------------------------------------------------------- 注册
    def register(self):
        r = requests.post(f"{BASE}/api/terminals/register",
                          json={"code": self.code, "pond_id": self.pond_id,
                                "ip": "127.0.0.1",
                                "devices": ["S-TEMP", "S-OXY", "S-PH", "FEED"]}, timeout=5)
        log("注册:", r.status_code, r.json().get("data", {}).get("status"))
        return r.ok

    def heartbeat(self):
        try:
            requests.post(f"{BASE}/api/terminals/{self.code}/heartbeat", timeout=5)
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
                r = requests.post(f"{BASE}/api/measurements", json=payload, timeout=5)
                if r.ok:
                    d = r.json()
                    print(".", end="", flush=True)
                else:
                    log("采集被拒:", r.json().get("message"))
            except requests.RequestException as e:
                log("采集上传失败:", e)

    # ---------------------------------------------------------- 任务
    def poll_tasks(self):
        try:
            r = requests.get(f"{BASE}/api/terminals/{self.code}/tasks", timeout=5)
            if not r.ok:
                return []
            return r.json().get("data", [])
        except requests.RequestException:
            return []

    def receipt(self, task_no, kind, status, actual=None, fault=None):
        payload = {"task_no": task_no, "kind": kind, "status": status,
                   "actual_amount": actual, "fault": fault,
                   "occurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            r = requests.post(f"{BASE}/api/receipts", json=payload, timeout=5)
            return r.ok
        except requests.RequestException:
            return False

    def handle_task(self, task):
        no = task["task_no"]
        # 去重：重连/重启后仍能识别已接收任务
        if no in self.seen_tasks:
            return
        self.seen_tasks.add(no)
        log(f"接收任务 {no}: 确认量 {task['confirm_amount']}{task['unit']}")

        # 接收回执
        self.receipt(no, "accept", "accepted")
        time.sleep(0.3)

        if self.fault == "timeout":
            log(f" [{no}] 注入故障：接收后不回执（等待后端判超时）")
            return

        # 执行中
        self.receipt(no, "execute", "running")
        time.sleep(0.5)

        if task.get("stop_requested") or (self.fault == "stop"):
            if self.fault == "slow_stop":
                log(f" [{no}] 设备不支持远程停止，等待现场处理")
                return
            self.receipt(no, "stop", "stopped", actual=None)
            log(f" [{no}] 已停止")
            return

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
        self.stopped.set()
        try:
            requests.post(f"{BASE}/api/terminals/{self.code}/offline", timeout=5)
        except requests.RequestException:
            pass


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
