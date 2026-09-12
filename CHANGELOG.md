# Changelog

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.2] - 2026-09-12

### 修复

- 条件表达式不再“失败开放”：比较运算符两侧可省略空格（`count>3`、`status==200`），
  函数名写错、单等号等无法解析的表达式会抛 `ConfigError`，而不是静默判为真。
- `download` 显式指定 `selector` 时信任用户选择：`<button>`、`href="#"` 等
  JS 触发下载的元素也能下载（自动发现模式仍按 href/关键词过滤）。
- `--resume` / `--retry-failed` 对 `mode: once` 任务生效：已完成或在清单外的
  单页任务会被跳过，不再重复执行。
- `playflow run` 有任务失败或任务级错误时退出码为 1，便于脚本与 CI 感知结果。
- Python API 直调 `run_workflow` 时自动挂控制台日志 handler；`disable_console_logging()`
  后不会再被自动加回。
- dry-run 安全边界：`save_state` / `close_page` / `close_task_page` 标注为写动作，
  演练时不再改动登录态文件或关闭页面。
- 录制单选框/复选框时提取关联 `label` 文本，优先生成更稳的 `pick_radio` 文字步骤。

### 文档

- 修正登录态元数据文件名（`state.meta.json`，不是 `state.json.meta.json`）。
- 补回缺失的真实站点示例 `examples/deepseek_usage.yaml`（DeepSeek 用量/余额导出）。
- 更新单元测试数量说明。

## [0.1.1] - 2026-09-11

### 新增

- AI 使用指南与 AI 辅助开发声明（`docs/ai.md`：面向 AI 代理的库使用规范 + 开发过程使用的 harness 与模型披露）
- 社区化物料：CONTRIBUTING / 行为准则 / 安全策略 / Issue 与 PR 模板 / Dependabot
- 双语文档：README.en.md（英文版）与徽章
- 文档站：mkdocs-material + GitHub Actions 自动部署（docs/ 目录）
- `py.typed` 类型标注声明与公共模块类型标注

### 修复

- 登录态复用误判：`success_url` 校验不再只等页面 `load` 就下结论，新增观察窗口
  （`login.reuse_settle_ms`，默认 3000ms），SPA/延时 JS 跳回登录页也会被识别并重新登录。
- `state_reusable` 现在会检查目标站点 cookie 是否已过期，全部过期时不再复用登录态。

## [0.1.0] - 2026-09-11

首个 PyPI 发布。核心是 YAML 驱动的 Playwright 工作流引擎，面向内网老平台的批量任务自动化。

### 新增

- **执行引擎**：步骤 + 条件 + 循环 + 变量模板；44 个内置动作（导航/点击/表单/取值/接口/下载/断言/控制流），支持 `@action` 注册自定义动作。
- **面向真实老平台的容错**：文字匹配自动忽略空格、自动遍历 iframe、兼容新标签页与同页跳转、穿透开放 Shadow DOM。
- **登录与人工介入**：登录态跨流程复用（`state.json` + 站点元数据）、UKey/短信人工暂停点、批量运行中被踢回登录页自动重登。
- **任务循环**：`first_row` 队列 / `once` 单页 / `each_row` 表格三种模式，列表行自动识别。
- **创作工具**：`probe` 页面探测生成选择器建议与 YAML 草稿；`record` 录制人工操作生成步骤（密码自动脱敏）。
- **断点续跑**：按任务号记录进度文件（`<流程名>.progress.json`），`playflow run --resume` 跳过已完成任务；失败清单落盘 `<流程名>.failed.json`，`--retry-failed` 只补跑失败项。
- **运行报告**：每次运行写入 `reports/run_*.json` 与 `run_*.html` 汇总（成功/失败/跳过清单、耗时）。
- **失败现场**：`settings.trace: on_error`（默认）在运行异常中止时保存 Playwright trace.zip，可回放操作时间线。
- **结果通知**：`notify.webhook` 支持企业微信/钉钉群机器人与通用 JSON webhook，任务结束或失败时推送汇总。
- **dry-run**：`playflow run --dry-run` 只执行读动作（导航/取值/断言），写动作（点击/填写/上传等）跳过并记录，用于安全验证流程。
- **heal 自愈**：`playflow heal 流程.yaml` 对步骤的选择器/文字做页面可达性检查，平台改版后给出相似度评分的修复建议（`--fix` 生成 `.healed.yaml`）。
- **数据源任务**：`tasks` 支持 `from_csv` / `from_xlsx`（可选依赖 `playflow[xlsx]`），把表格每一行作为一次任务执行。
- **子流程复用**：`partials` + `include`，步骤片段在同一工作流内或跨文件复用。
- **录制验证**：`playflow record --verify` 录制完成后逐步回放验证，失败步骤在生成的 YAML 中标注。
- **工程质量**：库内输出改造为 `logging`（`playflow` logger，控制台输出可配置/可关闭）；pytest 单元测试层（无需浏览器）；selftest 支持一条命令自动拉起 mock 平台；GitHub Actions CI（ruff + pytest + 构建 + mock 平台端到端）。
