# Python API

```python
from playflow import run_workflow, WorkflowEngine, action

summary = run_workflow("workflow.yaml")
summary = run_workflow(workflow=my_dict, base_dir=".", headless=True)
summary = run_workflow("workflow.yaml", dry_run=True)     # 只读演练
summary = run_workflow("workflow.yaml", resume=True)      # 断点续跑
summary = run_workflow("workflow.yaml", retry_failed=True)
```

## 自定义动作

函数签名固定为 `(engine, params, step)`，用 `@action` 注册后即可在 YAML 中 `uses:`。

```python
from playflow import action

@action("我的动作")                # 默认 mode="write"（dry-run 跳过）
def my_action(engine, params, step):
    engine.get_text(params["selector"])
    engine.logf("自定义动作执行完成", echo=True)

@action("只读抓取", mode="read")   # dry-run 下仍会执行
def my_reader(engine, params, step):
    return engine.get_text(params["selector"])
```

`WorkflowEngine` 常用成员：

| 成员 | 说明 |
|---|---|
| `current` | 当前 Playwright Page |
| `ctx` | BrowserContext |
| `vars` | 变量字典 |
| `settings` | 合并默认值后的 settings |
| `locator(sel, frame)` | 跨 frame 定位 |
| `frames_of(page)` | 列出所有 frame |
| `selector_exists(sel)` / `selector_visible(sel)` / `count_elements(sel)` | 存在性/可见性/计数 |
| `get_text` / `get_attr` / `get_value` | 取值 |
| `has_text(text)` | 页面是否包含归一化文字 |
| `logf(msg, echo=...)` | 写日志（echo 时同步控制台） |
| `snap(stage)` | 截图 |

## 日志

库内输出走标准 `logging`（logger 名 `playflow`）：

- 默认自动挂一个控制台 handler，脚本直跑能看到进度
- `playflow.disable_console_logging()` 可关闭，或自行配置 handler（此时 playflow 不会再附加）
- 文件日志（`logs/run_*.log`）不受 logging 配置影响，始终写入

## 模板与条件

- `{{ }}` 模板：整串是单个模板时保留原始类型（数字/布尔/字典）
- 条件支持字符串表达式、结构化映射与条件函数，详见 [动作参考](actions.md)
