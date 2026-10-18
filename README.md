# DAS 管道泄漏风险离线回放与自动背景分级预警

这是一个在 Ubuntu/WSL 中运行的本地 DAS 历史 TDMS 回放软件。它不连接采集仪器、不修改原始 TDMS 文件；浏览器只充当操作台，实际分析由 Linux 中的 Python 服务完成。

> 定位必须准确：输出是**泄漏风险候选/优先复核对象**，不是经现场验证的泄漏结论。

## 已实现的分析链路

1. 对选定 TDMS 的有限通道和有限采样窗进行只读读取，避免把约 GB 级原始文件整体加载进内存；
2. 计算 RMS、峰值、主频、局部 RMS 比、2–120 Hz 对数频谱，以及相邻通道的幅值平方相干性；
3. 用 median/MAD 的稳健统计与迭代剔除保护带学习背景通道，降低异常通道污染背景的风险；
4. 仅当 RMS 异常、局部差异和频谱偏离同时满足阈值时，形成风险候选；相邻候选通道自动合并为空间通道区间；
5. 支持同一文件多窗口持续性，以及连续 TDMS 文件持续性；只有重复出现且主频稳定的候选才升级为黄/橙/红预警；
6. 异步任务状态保存至 SQLite；任务过程写入 Linux 日志；单文件任务可下载逐通道 CSV；pytest + Allure 覆盖核心规则与接口回归；
7. 支持 TDMS 数据资产接入清单、采集端状态上报（本地模拟契约）及持续性预警事件的 HTTP Webhook 投递与有限重试。

## 技术栈

- Python 3.13、npTDMS、NumPy、SciPy
- FastAPI、本地浏览器操作台、Plotly
- SQLite、Rotating Linux log
- pytest、Allure、Git/GitHub

## 启动

在 Ubuntu/WSL：

```bash
cd "/mnt/d/pytest_practice/简历/软件源码/das-monitor"
./scripts/bootstrap.sh
./scripts/run_dev.sh
```

在 Windows 浏览器打开 `http://127.0.0.1:8000`。

完整命令与面试口径见 [docs/linux_runbook.md](docs/linux_runbook.md)；算法和边界见 [docs/algorithm_and_limits.md](docs/algorithm_and_limits.md)。
接口边界、示例和测试范围见 [docs/interface_contract_and_test_strategy.md](docs/interface_contract_and_test_strategy.md)。

Postman 手工/回归接口用例集合位于 [postman/DAS_Offline_Monitor_API_Test.postman_collection.json](postman/DAS_Offline_Monitor_API_Test.postman_collection.json)：导入后在 Runner 中执行，可覆盖正常数据资产接入、任务异步状态、设备状态、路径安全和参数边界。
