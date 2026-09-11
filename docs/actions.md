# 动作参考

45 个内置动作。每个动作带读/写标注（`mode`）：dry-run 时只执行读动作，写动作跳过。

## 变量 / 基础

| 动作 | 参数 | 说明 |
|---|---|---|
| `log` | `message` | 打印并写日志 |
| `set_var` | `name`, `value` | 设置变量 |
| `parse_var` | `from`/`selector`, `regex`, `group`, `default`, `name` | 正则抽取变量 |
| `write_file` | `path`/`file`, `content`, `append`, `encoding` | 写文本（对象转 JSON） |

## 导航与页面

| 动作 | 参数 | 说明 |
|---|---|---|
| `goto` | `url`, `new_tab`, `wait_until`, `timeout`, `settle` | 打开地址 |
| `wait_for_url` | `pattern`/`url`, `regex`, `timeout` | 等 URL 变化 |
| `reload` | `wait_until`, `timeout` | 刷新 |
| `wait` | `ms` 或 `seconds` | 等待 |
| `wait_for` | `selector`, `frame`, `state`, `timeout` | 等元素出现/消失 |
| `switch_page` | `index` / `url_contains` / `title_contains` | 切换页面 |
| `close_page` | `which`: `current`/`others`/`all_task` | 关页面 |
| `close_task_page` | — | 关掉本任务打开的标签页，回列表 |
| `set_dialog` | `mode`, `text` | 原生弹窗策略 |
| `save_state` | `path` | 保存当前登录态 |

## 点击

| 动作 | 参数 | 说明 |
|---|---|---|
| `click` | `selector`, `frame`, `nth`, `capture_new_page`, `timeout`, `no_wait` | 精确选择器点击 |
| `click_text` | `text`, `optional`, `capture_new_page`, `timeout` | 按文字点击（容错空格、遍历 iframe） |
| `click_row_link` | `capture_timeout` | 点击当前任务行链接 |

## 表单

| 动作 | 参数 | 说明 |
|---|---|---|
| `fill` | `selector` 或 `placeholder`/`name`/`id`, `text`/`value`, `clear`, `secret`, `nth`, `frame` | 填输入框 |
| `check` | `selector`, `nth`, `frame` | 勾选复选框 |
| `pick_radio` | `text`/`value`/`selector`, `nth`, `frame` | 选择单选框 |
| `select_option` | `selector`, `label`/`value`/`index`/`option`, `frame` | 下拉选择 |
| `press` | `key`, `selector` | 按键 |
| `hover` | `selector` 或 `text`, `nth`, `frame` | 悬停 |
| `scroll` | `selector` 或 `by` 或 `bottom`, `ms` | 滚动 |
| `upload` | `selector`, `file`/`files` | 上传文件 |

## 取值 / 请求 / 下载

| 动作 | 参数 | 说明 |
|---|---|---|
| `extract` | `selector`, `what`, `attr`, `all`, `nth`, `join`, `name`, `default`, `until`, `interval`, `timeout`, `frame` | 取页面内容存变量 |
| `evaluate` | `js`/`code`/`script`, `name`, `frame` | 执行 JS 并存返回值 |
| `request` | `url`, `method`, `params`, `headers`, `json`, `data`, `timeout`, `verify`, `output`, `name` | HTTP 请求（需 requests） |
| `download` | `selector`, `text`, `dir`, `subdir`, `filename_prefix`, `optional`, `timeout` | 下载附件 |

## 断言与人工介入

| 动作 | 参数 | 说明 |
|---|---|---|
| `expect_text` | `text` | 页面必须出现文字 |
| `expect_visible` | `selector`, `frame` | 元素必须可见 |
| `expect_url` | `pattern`/`url`, `regex` | URL 必须匹配 |
| `assert` | `condition`, `message` | 通用断言 |
| `screenshot` | `name` | 手动截图 |
| `fail` | `message` | 主动失败 |
| `pause` | `message` | 暂停等人工操作（dry-run 会跳过） |

## 页面管理

| 动作 | 参数 | 说明 |
|---|---|---|
| `switch_page` | `index` / `url_contains` / `title_contains` | 切换当前页面 |
| `close_page` | `which` | 关页面 |
| `close_task_page` | — | 关掉任务标签页回列表 |
| `set_dialog` | `mode`, `text` | 弹窗策略 |

## 创作工具

| 动作 | 参数 | 说明 |
|---|---|---|
| `probe` | `file`, `all_pages`, `max`, `steps` | 扫描页面输出元素清单与选择器建议 |
| `record` | `file`, `verify` | 录制人工操作生成 YAML；`verify: true` 录完回放验证 |

## 控制流

| 动作 | 参数 | 说明 |
|---|---|---|
| `if` | `condition` + `then`/`else` | 条件分支 |
| `for_each` | `over`/`list`/`rows`/`range`, `as`, `frame` + `do` | 列表遍历 |
| `repeat` | `times` + `do` | 固定次数循环 |
| `while` | `condition`, `as`, `max`, `init` + `do` | 条件循环 |
| `break` / `continue` | — | 循环控制 |
| `run_steps` | + `do`/`steps` | 内联子步骤 |

## 条件与函数

字符串表达式示例：

```yaml
if: "body_text contains 申请"
if: "exists('#submit') or visible('.ok')"
if: "count('table tr') >= 5 and url() contains /list"
if: "st.status == 200"
```

运算符：`== != > < >= <=`、`contains / not contains`、`matches`、`startswith / endswith`、
`in / not in`；逻辑 `and / or / not` 与括号。
条件函数：`exists`、`visible`、`absent`、`has_text`、`count`、`text`、`attr`、`value`、
`page_count`、`url`、`title`。

结构化映射：

```yaml
if:
  and:
    - {contains: ["{{ body_text }}", "申请"]}
    - {exists: "#submit"}
```

## 自定义动作

```python
from playflow import action

@action("我的动作")                # 默认 mode="write"，dry-run 时跳过
def my_action(engine, params, step):
    ...

@action("只读动作", mode="read")   # dry-run 下仍会执行
def my_reader(engine, params, step):
    ...
```
