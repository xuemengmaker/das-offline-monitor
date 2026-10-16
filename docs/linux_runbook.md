# Linux 运行手册

## 这个项目在哪里

- Windows 源码目录：`D:\pytest_practice\简历\软件源码\das-monitor`
- Ubuntu 对应目录：`/mnt/d/pytest_practice/简历/软件源码/das-monitor`
- 历史数据根目录：`/mnt/f/数据`（只读使用，项目不复制 TDMS 原始数据）

Windows 目录挂载到 WSL 后，源码在 VS Code 中容易查看，同时 Python 程序、依赖和日志都由 Ubuntu 执行。

## 首次准备

在 Ubuntu 终端运行：

```bash
cd "/mnt/d/pytest_practice/简历/软件源码/das-monitor"
./scripts/bootstrap.sh
```

`bootstrap.sh` 会依据 `pyproject.toml` 和 `uv.lock` 创建项目专属 `.venv` 并安装固定版本的依赖。`.venv` 不提交到 Git。

## 启动软件

```bash
cd "/mnt/d/pytest_practice/简历/软件源码/das-monitor"
./scripts/run_dev.sh
```

看到 `Uvicorn running on http://127.0.0.1:8000` 后，在 Windows 浏览器打开：

`http://127.0.0.1:8000`

浏览器是本地操作台；Python/FastAPI 进程在 Ubuntu 中运行。用 `Ctrl+C` 停止服务。

## 验证与日志

```bash
./.venv/bin/pytest --alluredir=allure-results -q
tail -f logs/das_monitor.log
```

日志关键字段：时间、级别、事件、任务 ID、TDMS 相对路径、候选数或错误栈。遇到故障时，先记录任务 ID，再查看同一 ID 的日志链路。

在 Windows PowerShell 中生成 Allure HTML 报告：

```powershell
& 'D:\pytest_practice\简历\软件源码\das-monitor\scripts\generate_allure_report.ps1'
```

随后在浏览器打开 `report\allure-report\index.html`。`allure-results` 与 HTML 报告均是可再生测试产物，已被 Git 忽略。

## 面试中准确的表述

“我在 Ubuntu/WSL 中运行 FastAPI 的 DAS 离线回放服务；原始 TDMS 数据放在只读挂载目录。接口创建异步分析任务，后台按通道读取受限窗口，计算 RMS 和基于 median/MAD 的 robust Z-score，任务状态和结果写入 SQLite，同时记录结构化日志。这个项目是历史数据回放与预警初筛演示，不是连接实际采集设备的现场实时系统。”
