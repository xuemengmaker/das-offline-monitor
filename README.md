# DAS 离线回放监测与自动背景分级预警系统

这是一个在 Ubuntu/WSL 上运行的本地演示软件：从指定 TDMS 数据根目录**只读**选择历史文件，进行窗口化质量检查、自动背景初筛和分级预警，并在浏览器中显示任务状态与结果。

> 边界：它用于离线回放演示与测试，不连接真实采集硬件、不宣称现场实时监测，也不把初筛结果当作最终泄漏结论。

## 技术栈

- Python 3.13、npTDMS、NumPy、SciPy
- FastAPI + 本地浏览器操作台
- SQLite 任务记录、结构化 Linux 日志
- pytest + Allure、Git

## Linux 启动方式（实现完成后）

```bash
cd /home/wangxm/projects/das-monitor
./scripts/bootstrap.sh
./scripts/run_dev.sh
```

浏览器访问 `http://127.0.0.1:8000`。
