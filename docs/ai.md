# AI 使用指南与开发声明

本页分两部分：**给 AI 的使用指南**（如何正确驱动 playflow）与 **AI 辅助开发声明**（本项目开发过程使用的 harness 与模型披露）。

---

## 第一部分：给 AI / 编码代理的使用指南

如果你是 AI 代理（编码助手、自动化代理），本节是你的速查手册。playflow 的设计目标就是
"让不写代码的人和不看页面的代理都能编排浏览器任务"，因此对 AI 非常友好。

### 这是什么、何时用

- playflow = **YAML 工作流 + Playwright**。适合：批量处理内网/后台平台的待办、
  定时取数、附件归档、表单补录等**重复性网页任务**。
- 不适合：复杂的前端自动化测试断言（用 Playwright 原生测试）、非浏览器任务。

### 安装与调用

```bash
pip install playflow                      # 需本机已装 Chrome
playflow validate workflow.yaml           # ① 先校验语法与结构
playflow run workflow.yaml --dry-run      # ② 只读演练：写动作全部跳过，安全
playflow run workflow.yaml                # ③ 正式执行
```

Python 调用：`from playflow import run_workflow; run_workflow("wf.yaml", dry_run=True)`。

### 给 AI 写 YAML 的规则（重要）

1. **先校验再执行**：写完 YAML 必须先跑 `validate`，再 `--dry-run`，最后才 `run`。
2. **优先文字匹配**：用 `click_text`（如 `text: [提交, 确定]`）而不是脆弱的 CSS 选择器；
   它自动容错按钮文字中的空格、遍历所有 iframe、穿透 Shadow DOM。
3. **不确定的步骤要标注**：可能不存在的按钮加 `optional: true`；允许失败的步骤加
   `continue_on_error: true`；分支逻辑用 `if:` 条件（支持 `exists()`/`has_text()` 等函数）。
4. **等待交给引擎**：`goto`/`click` 后有默认等待；不要堆固定 `wait`，用
   `wait_for` / `extract` 的 `until` 轮询替代。
5. **批量待办用 `tasks`**：`mode: first_row` 处理"做完一行少一行"的队列；
   表格数据用 `from_csv`；`label_regex` 提取任务号，配合 `--resume`/`--retry-failed`
   实现断点续跑。
6. **凭据不落盘**：密码用 `{{ env.password }}`（env 来源由使用者注入），
   不要把真实密码/token 写进 YAML 或对话。
7. **人工验证环节**：UKey/短信等浏览器外的验证用 `pause` 动作或 `login.manual_pause: true`，
   引擎会等人操作完再继续。

### 常用动作速查

| 需求 | 动作 |
|---|---|
| 打开页面 | `goto` |
| 按文字点按钮 | `click_text` |
| 填输入框 | `fill` |
| 选择单选框/下拉 | `pick_radio` / `select_option` |
| 取页面文字/属性 | `extract` |
| 等 URL/元素 | `wait_for_url` / `wait_for` |
| 断言 | `expect_text` / `expect_visible` / `expect_url` / `assert` |
| 下载附件 | `download`（自动找"附件/下载"链接） |
| 关掉任务页回列表 | `close_task_page` |

45 个动作的完整参数见[动作参考](actions.md)。

### 出错了怎么办

- 单个任务失败不会中断整批：失败清单在 `<流程名>.failed.json`，
  用 `--retry-failed` 只补跑失败项；`--resume` 跳过已完成项。
- 运行中止时 `shots/trace_*.zip`（Playwright trace）可完整回放操作时间线。
- 平台改版导致步骤失效时用 `playflow heal workflow.yaml --url 页面 --fix`
  自动生成修复建议。

### 更多

完整文档：[工作流](workflow.md) · [动作参考](actions.md) · [命令行](cli.md) ·
[Python API](api.md) · [快速开始](quickstart.md)。

---

## 第二部分：AI 辅助开发声明

本项目采用 **AI 辅助、人类主导** 的开发模式，按社区透明度惯例披露如下：

| 项目 | 说明 |
|---|---|
| 需求与验收 | 由维护者 [@timmycheng](https://github.com/timmycheng) 提供：真实内网平台的自动化需求描述、场景验收、发布决策 |
| 开发 Harness | **ZCode**（CLI 编码代理 / coding agent），负责代码生成、测试编写、CI 与发布工程 |
| 模型 | **GLM**（由 [Z.ai](https://z.ai) 训练；v0.1.0 迭代使用 GLM-5.3-Flash） |
| 覆盖范围 | 引擎代码、45 个内置动作、测试体系、文档、CI/发布工作流均由 AI 在人类指令下生成与迭代 |

### 质量保障

尽管代码由 AI 大规模生成，合并与发布均以自动化验收为准：

- `pytest` 单元测试 99 项（模板/条件/校验/断点续跑/通知/报告等，无需浏览器）
- `tests/selftest.py` 端到端验收 62 项（自带 mock 平台，覆盖登录态、队列循环、容错匹配等真实语义）
- `ruff` 代码检查、`python -m build` + `twine check` 构建验证、GitHub Actions 持续执行

人类维护者负责需求正确性、真实平台场景验证与最终发布决策。按 MIT 许可证，软件按"现状"提供。
