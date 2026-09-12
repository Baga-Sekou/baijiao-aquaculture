# 白蕉水产养殖管理平台

软件实训课程作业（智能渔业-3组）。面向珠海斗门白蕉鲈鱼养殖场景，实现
「环境采集 → 后端校验与建议 → 人工确认 → 任务下发 → 设备回执 → 核查留痕」
的完整业务闭环，并针对数据失效、重复提交、回执超时、卡料、实际量未知等
异常分支提供可演示、可验证的处理。

> 本系统的数据与建议用于课程联调验证，**不得作为实际养殖依据**。

---

## 1. 快速开始

**推荐：双击 `start.bat`**（Windows）。首次运行会自动装依赖、建数据库、载入样例数据并打开浏览器。

手动启动见下方 1.1–1.6。

### 1.0 在别的电脑上使用（拷贝即用）

整个文件夹拷到另一台电脑后，只要那台电脑装了 **Python 3.10+**，双击 `start.bat` 即可，
**不需要安装 MySQL 或其他数据库**。

原因是本平台默认使用 **SQLite**（单文件数据库），数据库就是 `data/baijiao.db` 这一个文件。

```bash
# 先自检环境（会告诉你缺什么、怎么修）
python scripts/check_env.py
```

### 1.1 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+（开发环境 3.14.5） | 唯一必需项 |
| 浏览器 | Edge / Chrome | 看板与移动端页面 |
| MySQL | 8.0（**可选**） | 仅在需要切回 MySQL 时需要 |

### 1.2 安装依赖

```bash
pip install -r requirements.txt
```

### 1.3 配置

默认无需任何配置。`.env` 中 `DB_URL=` 留空即使用 SQLite。

如需切回 MySQL（课程要求口径），在 `.env` 中这样写：

```ini
DB_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/baijiao_aquaculture?charset=utf8mb4
```

### 1.4 初始化数据库

```bash
python scripts/init_db.py --seed
```

### 1.5 导入课堂测试数据（可跳过）

项目已内置两份 CSV 于 `data/sample/`：

```bash
python scripts/import_csv.py --dir data/sample
```

（也可逐个指定文件路径；Windows 下命令行传中文文件名易因编码丢失，推荐 `--dir`。）

导入会保留原字段与来源文件，使用独立记录编号，避免两年重复的 `id` 相互覆盖；
重复导入同一文件不会重复写入。

### 1.6 启动后端

```bash
python backend/app.py
```

浏览器打开 <http://127.0.0.1:5000> 。左上角可切换身份（塘主 / 管理员 / 运营者），
用于演示三级权限。

> 想让手机在同一 WiFi 下访问：把 `.env` 里的 `APP_HOST` 改成 `0.0.0.0`，
> 手机访问 `http://本机局域网IP:5000`。

### 1.7 启动采集控制终端仿真器（可选，用于演示投喂闭环）

```bash
# 正常终端
python simulator/terminal.py --code TERM-A01 --pond 1

# 故障注入：卡料 / 实际量缺失 / 回执超时 / 不支持远程停止
python simulator/terminal.py --code TERM-A01 --pond 1 --fault stuck
```

### 1.8 生成演示数据与跑验收

```bash
python scripts/seed_demo.py        # 生成近 30 天环境曲线、若干投喂任务与异常
python tests/test_acceptance.py    # 跑第 9 章验收场景，输出 docs/验收测试记录.json
```

---

## 2. 目录结构

```
白蕉水产养殖管理平台/
├── start.bat                     一键启动（Windows：装依赖→建库→载数据→开浏览器）
├── .env                          DB_URL 留空=SQLite；填 MySQL 串=切回 MySQL
├── data/
│   ├── baijiao.db                SQLite 数据库文件（首次运行自动生成）
│   └── sample/                   内置的两年课堂测试 CSV
├── backend/
│   ├── app.py                    应用入口（Flask，含页面路由）
│   ├── database.py               连接与会话（默认 SQLite，可切 MySQL）
│   ├── models.py                 领域模型（对应需求书 7.1 的业务记录）
│   ├── api/                      接口层
│   │   ├── ponds.py              鱼塘、批次、用户、授权
│   │   ├── env.py                环境测量、摄食反馈、终端注册
│   │   ├── feeding.py            建议、任务、回执、核查、超时巡检
│   │   ├── monitor.py            相机、疑似死鱼事件、告警
│   │   └── ops.py                看板、多塘比较、运营记录、日志、配置
│   ├── services/                 业务规则层
│   │   ├── common.py             编号、配置、审计、权限、时间解析
│   │   ├── environment.py        上传校验、有效性、日内曲线与统计特征
│   │   ├── suggestion.py         投喂建议生成（含演示规则与规则版本）
│   │   └── task_service.py       任务幂等、设备占用、回执、超时、核查
│   ├── templates/                Web 看板页面（响应式，兼顾移动端）
│   └── static/                   CSS / JS（无外部依赖，离线可演示）
├── simulator/terminal.py         采集控制终端仿真器（含故障注入）
├── scripts/
│   ├── check_env.py              环境自检（Python/依赖/数据库/端口）
│   ├── init_db.py                建库建表 + 基础数据
│   ├── import_csv.py             导入课堂 CSV（去重、冲突处理）
│   ├── seed_demo.py              生成演示数据
│   └── reset_runtime.py          重置运行数据
├── tests/test_acceptance.py      验收场景测试
└── docs/
    ├── 设计说明.md               分层设计、状态机、异常策略
    └── 验收测试记录.md/.json     验收场景实际结果
```

---

## 3. 关键设计约定

这些约定直接来自《需求说明书》，实现时不得绕过：

1. **三种量分列保存**：建议量（系统提出）、确认量（用户确认）、实际量（设备实测或
   人工填报，必须标来源）。设备无计量能力时实际量为「未获取」，**不用确认量补齐**。
2. **请求编号幂等**：客户端为一次确认生成请求编号，重复提交返回原任务；
   回执按任务号去重，不重复累计用料。
3. **设备占用**：同一设备已有未结束或待核查任务时，拒绝新的投喂任务。
4. **超时保守处理**：回执超时 → 任务转「结果未知（待核查）」并保留设备占用，
   **不自动重发**；核查结束且满足设备恢复条件才解除占用。
5. **来源可标识**：`real / sim / manual / csv` 四种来源分列，界面明确区分
   演示数据与真实数据。
6. **缺测不补零**：日内曲线缺测以空缺表示，无效值单独标记并排除在建议输入之外。

### 任务状态机

```
pending 待下发
  └─ dispatch ─→ dispatched 已下发待回执
                   ├─ accept ─→ running 执行中 ─→ finish(done) ─→ done 已完成
                   ├─ finish(failed)/fault ─→ failed 失败
                   ├─ stop ─→ stopped 已停止
                   └─ 超时未回执 ─→ unknown 结果未知（待核查）
                                        └─ review ─→ done / stopped / failed / not_executed
                                                     （结论为 unknown 时保持待核查）
```

---

## 4. 交付内容与说明

- **已实现**：后端接口与业务规则、采集终端仿真、Web 看板与移动端响应式页面、
  验收场景测试。
- **按需求书 2.3 标注为后续扩展**：微信小程序（当前用移动端 Web 代替）、
  真实硬件接入、AI 模型预测（投喂量 / 生长 / 水质）、视觉识别效果。
  相关接口位置已预留，未实现的能力不返回伪结果。

## 5. 已知限制

- 视觉识别为**仿真流程演示**（`vision-sim-v1`），未接入真实相机与识别模型。
- 模型预测接口未实现，调用会明确返回「尚未接入」，不返回伪预测。
- 演示规则（`rule-v1.0`）为课堂联调用，非养殖阈值；界面已标注「演示规则」。
- 微信小程序未实现，移动端由响应式 Web 承担。
