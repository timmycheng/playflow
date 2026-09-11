# 工作流

工作流是一个 YAML 文件，顶层包含 `settings`、`browser`、`login`、`env`、`steps`、
`tasks`、`partials`、`notify` 等可选字段。完整结构参见仓库
[README](https://github.com/timmycheng/playflow#readme)。本页覆盖引擎的关键机制。

## 步骤通用字段

| 字段 | 说明 |
|---|---|
| `uses` | **必填**，动作名 |
| `name` | 步骤名，出现在日志与截图中 |
| `with` | 动作参数（支持 `{{ }}` 变量渲染） |
| `if` | 条件，不满足则跳过本步 |
| `id` | 把本步返回值存入 `steps.<id>` |
| `continue_on_error` | `true` 时本步失败不中断当前任务 |

## 任务模式

| mode | 行为 |
|---|---|
| `first_row`（默认） | 反复取第一行 → 执行 → 回列表，直到列表空；同行反复出现会终止防死循环 |
| `once` | 只执行一次，适合单页/接口流程 |
| `each_row` | `remove_after: true`（默认）始终取第一行；`false` 按行号依次取 |

## 断点续跑与失败重试

每次运行把进度写到工作流目录：

- `<流程名>.progress.json` —— 已完成任务号（每条任务结束即落盘）
- `<流程名>.failed.json` —— 失败任务号及原因

```bash
playflow run workflow.yaml --resume         # 跳过已完成的任务
playflow run workflow.yaml --retry-failed   # 只补跑失败清单里的任务
```

`first_row` 队列中处理成功的行会从列表消失，`--resume` 天然从第一行未完成任务继续；
跳过的行用行游标越过，不会重复处理。

## dry-run 只读演练

```bash
playflow run workflow.yaml --dry-run
```

动作分读/写两类（`goto`/`extract`/断言是读；`click`/`fill`/`upload` 是写；
`request` 按 HTTP 方法区分）。dry-run 只执行读动作、跳过写动作并记录，
任务间等待归零、每个列表任务只处理 1 条。登录仍会真实执行。

## heal 改版自检

```bash
playflow heal workflow.yaml --url http://平台/list --fix
```

登录后打开 `--url` 页面，逐个检查步骤的 `selector`/文字参数是否命中；
未命中给出按相似度排序的候选（按钮文字/id/name/placeholder）。
`--fix` 不改原文件，生成 `workflow.healed.yaml`（阈值默认 0.55，可用 `--min-score` 调整）。
heal 只检查“登录后 + `--url` 指定页面”这一个页面状态，中途才出现的元素需分页检查。

## 运行报告与 trace

- `reports/run_<时间>.json` / `.html`：每次运行（含异常中止）自动生成，含任务清单与成败统计
- `settings.trace: on_error`（默认）在异常中止时把 Playwright trace 存到 `shots/trace_*.zip`，
  用 `playwright show-trace <文件>` 回放；`always` 每次都存，`off` 关闭

## 通知（企业微信 / 钉钉 / 通用 webhook）

```yaml
notify:
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx   # 或 URL 列表
  only_on_failure: false
```

按域名自动识别消息格式；需要 `pip install "playflow[notify]"`。通知失败只记日志，不影响运行。

## 数据源任务（from_csv / from_xlsx）

```yaml
tasks:
  - name: 批量补录
    from_csv: 待办.csv             # 或 from_xlsx: 待办.xlsx（可加 sheet）
    encoding: gbk                  # 仅 csv；默认 utf-8-sig
    label_column: 单号
    max_tasks: 99
    url: "http://平台/detail?no={{ row.单号 }}"
    steps:
      - uses: fill
        with: { selector: "#city", text: "{{ row.城市 }}" }
```

每行数据执行一次 steps；列名直接是变量（`{{ 单号 }}`），整行在 `{{ row }}` 下，
`{{ label }}`/`{{ row_text }}`/`{{ row_index }}` 照常可用。xlsx 需要 `playflow[xlsx]`。

## 子流程复用（partials / include）

```yaml
partials:
  归档:
    - uses: download
      with: { optional: true }
  公共登录后: { file: common.yaml }

steps:
  - include: 公共登录后
```

支持嵌套（带循环引用检测），可用于 steps / login.steps / tasks[].steps 与 then/else/do 内部；
`playflow validate` 会检查引用完整性。

## 登录态与人工暂停

1. 启动先看 `state.json`（或 `login.state_file`）：有效则跳过登录与人工验证
2. 失效或站点不符则自动填账密、点登录；`manual_pause: true` 时暂停等人处理 UKey/短信
3. 校验通过把登录态写回，并写 `.meta.json` 记录站点防跨流程误用
4. 批量运行中被踢回登录页会自动重登后继续
5. 换账号：删除 `state.json` 与 `state.json.meta.json`
