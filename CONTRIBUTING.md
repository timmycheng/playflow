# 贡献指南 / Contributing

> **English TL;DR:** `pip install -e .[dev]` → `ruff check .` → `pytest` → `python tests/selftest.py` (auto-starts a mock platform, needs local Chrome) → open a PR. Please keep PRs focused and add a CHANGELOG entry. For details see below (Chinese). 报告安全问题请勿公开 Issue，见 [SECURITY.md](SECURITY.md)。

感谢你关注 playflow！无论是报告 Bug、改进文档还是提交代码，都欢迎。

## 开发环境

```bash
git clone https://github.com/timmycheng/playflow.git
cd playflow
pip install -e .[dev]          # 含 playwright + pyyaml + pytest/ruff/build 等
python tests/selftest.py       # 端到端验收（自动拉起 mock 平台，需本机 Chrome）
```

## 开发流程

1. 从 `main` 拉出功能分支：`git checkout -b feat/xxx`
2. 改动后确保以下检查全部通过：
   - `ruff check .` —— 代码风格
   - `pytest` —— 单元测试（无需浏览器）
   - `python tests/selftest.py` —— 端到端验收（改动引擎语义时必须跑）
3. 提交 PR，说明：改了什么、为什么、如何验证。

## 代码约定

- **库内输出走 `logging`**：库代码（`playflow/`）不要直接 `print()`；
  需要用户可见的进度用 `log(msg, echo=True)`（经 `playflow` logger）。
  只有交互式 UI（暂停提示、record 交互、probe 交互）保留 `print`。
- **动作需标注读/写模式**：新动作用 `@action("名字", mode="read")`（只读）或
  `@action("名字")`（默认写，dry-run 时会被跳过）。参数在 `with:` 下传入，
  函数签名固定为 `(engine, params, step)`。
- **中文报错信息**：面向最终用户的错误（`StepError` / `ConfigError` / `AbortError`）用中文；
  内部异常链路保持可读即可。
- **新功能三件套**：README 章节、CHANGELOG 条目、selftest 用例（若涉及引擎语义）。

## 测试怎么写

- **单元测试**放 `tests/unit/`，不依赖浏览器：模板、条件、校验、partials、
  通知/报告/heal 的纯逻辑都可以在这里测。
- **端到端**加进 `tests/selftest.py`：mock 平台会自动启动，别依赖外部网络。

## 发布（维护者）

发布流程与 Trusted Publishing 配置见 [RELEASING.md](RELEASING.md)；
版本号更新 `playflow/__init__.py` 的 `__version__` 并在 [CHANGELOG.md](CHANGELOG.md) 记录。
