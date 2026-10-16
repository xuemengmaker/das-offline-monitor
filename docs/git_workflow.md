# Git 操作清单

本仓库位于 Windows 目录，但可以由 Windows Git 或 Ubuntu Git 操作；一次提交只选其中一个终端，避免两边同时进行 Git 操作。

## 首次提交前

Ubuntu 的 Git 身份与 Windows 的 Git 身份可以不同，因此先检查：

```bash
cd "/mnt/d/pytest_practice/简历/软件源码/das-monitor"
git config user.name
git config user.email
```

若为空，请把下面内容替换为你自己的 GitHub 姓名和 GitHub 绑定邮箱后执行：

```bash
git config user.name "你的姓名"
git config user.email "你的 GitHub 邮箱"
```

然后创建初始版本：

```bash
git add .
git status
git commit -m "feat: initialize DAS offline replay monitor"
```

`git add .` 的意思是把未忽略的改动放入“暂存区”；`git commit` 才会把该快照写入本地历史。`.venv`、日志、SQLite 运行数据和 Allure 报告已经被 `.gitignore` 排除。

## 日常更新

```bash
git status
git add src/das_monitor/tdms_service.py tests/test_tdms_service.py
git commit -m "feat: add bounded TDMS window analysis"
git log --oneline --decorate -5
```

建议按功能提交，而不是把无关改动混在一个提交中。

## 推送到 GitHub（首次）

在 GitHub 新建空仓库后，使用页面给出的仓库 URL：

```bash
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

后续已建立跟踪关系时，只需：

```bash
git push
```

## 找回旧版本

先只查看，不直接覆盖工作区：

```bash
git log --oneline
git show <提交ID> --stat
git switch -c review/<提交ID> <提交ID>
```

需要回到主分支时执行 `git switch main`。不要一开始就用 `git reset --hard`，它会丢弃未提交的改动。
