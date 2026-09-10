# playflow

YAML 驱动的 Playwright 自动化引擎。把浏览器操作写成工作流文件：

```yaml
name: 我的流程
login:
  url: http://10.0.0.1/login
  username: alice
  password: "{{ env.password }}"
  success_url: http://10.0.0.1/home
env:
  password: secret
steps:
  - uses: goto
    with: { url: "http://10.0.0.1/list" }
  - uses: click_text
    with: { text: [签收, 受理], optional: true }
  - uses: pick_radio
    with: { text: 同意 }
  - uses: click_text
    with: { text: [提交, 确定] }
```

```bash
playflow run workflow.yaml          # 执行
playflow validate workflow.yaml     # 只校验
```

- **一个执行引擎**：`步骤 + 条件 + 循环 + 变量`，页面再复杂也只改 YAML、不改代码。
- **面向真实老平台**：文字匹配自动忽略空格（“提 交”→“提交”）、自动遍历所有 iframe、兼容新标签页与同页跳转、穿透开放 Shadow DOM。
- **可选能力**：跨流程复用的登录态（`state.json`）、人工验证暂停点（UKey/短信）、任务队列循环、附件归档、截图与日志。
- **创作工具**：`probe` 扫描页面给出选择器建议与 YAML 草稿；`record` 录制人工操作生成步骤。
- 依赖只有 `playwright` + `PyYAML`，驱动系统已装的 Chrome，无需下载浏览器。

---

## 安装

```bash
pip install playwright pyyaml
# 若需要 request 动作
pip install requests
```

本机需已安装 Chrome（默认 `channel: chrome`）；只有 Edge 时把 `browser.channel` 配成 `msedge`。

源码方式安装本组件（可得到 `playflow` 命令）：

```bash
pip install -e .
```

## 快速开始（本地演练，不需要内网）

```bash
# 终端 1：启动模拟平台（SSO + UKey + iframe 列表 + 附件 + confirm）
python tests/mock_platform.py

# 终端 2：跑示例工作流（账号 admin/123456，UKey 环节点页面按钮后回终端按回车）
playflow run examples/demo.yaml
```

跑完可在 `附件/`、`logs/`、`shots/` 查看归档、日志与截图。改大 `examples/demo.yaml` 里的
`max_tasks` 可批量处理全部 10 条。

真实站点示例：`examples/deepseek_usage.yaml`（登录 DeepSeek 开放平台，抓当月用量/费用/余额并落盘 JSON），
`examples/advanced.yaml`（条件、循环、接口、断言）。

## 工作流结构

```yaml
name: 流程名称                     # 显示用

settings:                          # 全部可省略
  max_tasks: 1                     # 每个任务处理条数（任务级 max_tasks 可覆盖）
  task_delay_seconds: [1, 3]       # 每条之间随机等待
  screenshot: true                 # 关键动作截图到 shots/
  attachment_dir: 附件              # 附件保存根目录
  dialog: accept                   # 原生弹窗：accept（默认）/ dismiss
  dialog_text: ""                  # dialog=prompt 时的输入文本
  timeout: 20000                   # 元素默认超时（毫秒）

browser:
  channel: chrome                  # chrome / msedge
  headless: false

login:                             # 可选；不需要登录的流程整段删除
  url: http://sso/login
  username: 工号
  password: "密码"                  # 支持 {{ env.password }}
  username_selector: ""            # 可省略，自动找第一个文本框
  password_selector: ""            # 可省略
  button_text: [登录, 登 录]
  manual_pause: true               # 点登录后暂停等人处理 UKey/短信，回车继续
  pause_hint: 请在 UKey 窗口完成验证
  success_url: http://平台/home     # 登录成功校验地址（也用于登录态归属判断）
  verify_url: ""                   # 校验地址（默认取 success_url）
  state_file: state.json           # 登录态文件，多流程可各用一份
  steps: []                        # 登录页很特殊时，用自定义步骤替代以上简写

env:                               # 自定义变量；也可用 {{ env.xxx }} 引用
  operator: 自动处理

steps:                             # 主流程：登录后、逐任务之前执行一次
  - uses: log
    with: { message: "操作人 {{ operator }}" }

tasks:                             # 任务：列表循环或单页流程
  - name: 待办列表
    url: http://平台/list
    mode: first_row                # first_row（默认）/ once / each_row
    max_tasks: 1
    row_selector: ""               # 省略时自动识别 tr/li/div 内的链接行
    link_selector: ""              # 省略时取行内第一个 <a href>
    label_regex: '[A-Za-z]{1,6}-?\d{2,}'   # 从行文本提取任务号作汇总标签
    steps: [...]
```

### 步骤通用字段

| 字段 | 说明 |
|---|---|
| `uses` | **必填**，动作名 |
| `name` | 步骤名，出现在日志与截图中 |
| `with` | 动作参数 |
| `if` | 条件，不满足则跳过本步 |
| `id` | 把本步返回值存入 `steps.<id>`（同时写入 `steps_<id>`） |
| `continue_on_error` | `true` 时本步失败也不中断当前任务 |

### tasks 三种模式

| mode | 行为 | 适用 |
|---|---|---|
| `first_row`（默认） | 反复取列表第一行 → 跑 steps → 回列表，直到列表空或达到 `max_tasks`；同一行反复出现会终止防死循环 | 处理完行就消失的队列 |
| `once` | 不找列表行，steps 只执行一次 | 单页流程、报表导出、纯接口 |
| `each_row` | `remove_after: true`（默认）始终取第一行；`false` 按行号依次取第 0、1、2… 行 | 行不消失的表格 |

循环内可用变量：`row_text`、`row_index`、`label`（或 `task_no`）、`category`、`category_url`、`index`。

## 动作参考

### 变量

| 动作 | 参数 | 说明 |
|---|---|---|
| `log` | `message` | 打印并写日志 |
| `set_var` | `name`, `value` | 设置变量 |
| `parse_var` | `from`/`selector`, `regex`, `group`, `default`, `name` | 用正则从文本提取变量；`from` 省略时取 `row_text` |
| `write_file` | `path`/`file`, `content`, `append`, `encoding` | 写文本（对象自动转 JSON），相对路径锚定工作流目录 |

### 导航与页面

| 动作 | 参数 | 说明 |
|---|---|---|
| `goto` | `url`, `new_tab`, `wait_until`, `timeout`, `settle` | 打开地址 |
| `wait_for_url` | `pattern`/`url`, `regex`, `timeout` | 等 URL 变化 |
| `reload` | `wait_until`, `timeout` | 刷新 |
| `wait` | `ms` 或 `seconds` | 等待 |
| `wait_for` | `selector`, `frame`, `state`, `timeout` | 等元素出现/消失 |
| `switch_page` | `index` / `url_contains` / `title_contains` | 切换当前页面 |
| `close_page` | `which`: `current`（默认）/ `others` / `all_task` | 关页面 |
| `close_task_page` | — | 关掉本任务打开的全部标签页，回列表 |
| `set_dialog` | `mode`: `accept`/`dismiss`, `text` | 修改原生弹窗策略 |

### 点击

| 动作 | 参数 | 说明 |
|---|---|---|
| `click` | `selector`, `frame`, `nth`, `capture_new_page`, `timeout`, `no_wait` | 精确选择器点击 |
| `click_text` | `text`(关键词或数组), `optional`, `capture_new_page`, `timeout` | 按文字点击，自动容错空格、遍历 iframe |
| `click_row_link` | `capture_timeout` | 点击当前任务行内的链接（仅 tasks 循环内） |

### 表单

| 动作 | 参数 | 说明 |
|---|---|---|
| `fill` | `selector` 或 `placeholder`/`name`/`id`, `text`/`value`, `clear`, `secret`, `nth`, `frame` | 填输入框 |
| `check` | `selector`, `nth`, `frame` | 勾选复选框 |
| `pick_radio` | `text`/`value`/`selector`, `nth`, `frame` | 选择单选框（按 value/label/父级文本匹配） |
| `select_option` | `selector`, `label`/`value`/`index`/`option`, `frame` | 下拉选择 |
| `press` | `key`, `selector` | 按键（Enter/Escape…） |
| `hover` | `selector` 或 `text`, `nth`, `frame` | 悬停 |
| `scroll` | `selector` 或 `by: [x, y]` 或 `bottom: true`, `ms` | 滚动 |
| `upload` | `selector`, `file`/`files` | 上传文件（相对路径锚定到工作流目录） |

### 取值、接口与下载

| 动作 | 参数 | 说明 |
|---|---|---|
| `extract` | `selector`, `what`: `text`(默认)/`text_content`/`html`/`value`/`attr`/`count`/`href`, `attr`, `all`, `nth`, `join`, `name`, `default`, `until`, `interval`, `timeout`, `frame` | 取页面内容存变量；`nth` 0 起始；`until` 未满足时轮询重试（如等数字渲染出来）；隐藏元素自动用 textContent 兜底 |
| `evaluate` | `js`/`code`/`script`, `name`, `frame` | 执行 JS 并存返回值 |
| `request` | `url`, `method`, `params`, `headers`, `json`, `data`, `timeout`, `verify`, `output`, `name` | HTTP 请求（需 requests），结果含 `status/text/json/headers` |
| `download` | `selector`, `text`, `dir`, `subdir`, `filename_prefix`, `optional`, `timeout` | 下载附件；缺省自动找“附件/下载”链接或 `download` 属性 |

### 断言与人工介入

| 动作 | 参数 | 说明 |
|---|---|---|
| `expect_text` | `text` | 页面必须出现文字 |
| `expect_visible` | `selector`, `frame` | 元素必须可见 |
| `expect_url` | `pattern`/`url`, `regex` | URL 必须匹配（默认 glob） |
| `assert` | `condition`, `message` | 通用断言 |
| `screenshot` | `name` | 手动截图 |
| `fail` | `message` | 主动失败 |
| `pause` | `message` | 暂停等人工操作，回车继续 |

### 控制流

| 动作 | 参数 | 说明 |
|---|---|---|
| `if` | `with: {condition}` + `then:` / `else:` | 条件分支 |
| `for_each` | `over`/`list`/`rows`/`range`, `as`, `frame` + `do:` | 列表遍历；`{{ item }}`、`{{ index }}` |
| `repeat` | `times` + `do:` | 固定次数循环；`{{ index }}` |
| `while` | `condition`, `as`, `max`, `init` + `do:` | 条件循环；`max` 防死循环（默认 100） |
| `break` / `continue` | — | 循环控制 |
| `run_steps` | + `do:` / `steps:` | 内联子步骤 |

### 创作工具

| 动作 | 参数 | 说明 |
|---|---|---|
| `probe` | `file`, `all_pages`, `max`, `steps` | 扫描当前页面并打印元素清单；`file` 省略时写 `shots/probe_*.yaml`（含 suggested_steps） |
| `record` | `file` | 记录人工操作并生成 YAML 步骤（密码自动替换为 `{{ env.password }}`） |

## 条件与函数

`if`、`while`、`assert` 支持三种写法。

**字符串表达式**（最常用）：

```yaml
if: "body_text contains 申请"          # 变量名直接引用，字符串建议不加引号
if: "exists('#submit') or visible('.ok')"
if: "count('table tr') >= 5 and url() contains /list"
if: "title == '任务详情'"               # 带空格的字符串用引号
if: "st.status == 200"
```

运算符：`== != > < >= <=`，`contains / not contains`，`matches`（正则），
`startswith / endswith`，`in / not in`；逻辑 `and / or / not` 与括号。

**条件函数**：`exists(sel[, frame])`、`visible`、`absent`、`has_text(text)`、
`count(sel[, frame])`、`text(sel[, frame])`、`attr(sel, attr[, frame])`、
`value(sel[, frame])`、`page_count()`、`url()`、`title()`。

**结构化映射**：

```yaml
if:
  and:
    - {contains: ["{{ body_text }}", "申请"]}
    - {exists: "#submit"}
```

## 变量

- `env:` 下的键既可直接引用 `{{ operator }}`，也在 `{{ env.operator }}` 下。
- 步骤 `id: xx` 把返回值存入 `{{ steps.xx }}` 与 `{{ steps_xx }}`。
- 任务循环提供 `{{ row_text }}` / `{{ row_index }}` / `{{ label }}` / `{{ category }}`。
- 整串是单个模板时保留原始类型（数字/布尔/字典），如 `{{ resp.status }} == 200`。

## 登录态与人工暂停

1. 启动先看 `state.json`（或 `login.state_file`）：能验证通过就跳过登录与人工验证。
2. 失效或站点不符则走 `login` 简写：自动填账密、点登录；
   `manual_pause: true` 时暂停等人处理 UKey/短信，回车后回 `success_url` 校验。
3. 校验通过把登录态写回 `state.json`（并写 `.meta.json` 记录站点，防止跨流程误用）。
4. 批量运行中途被踢回登录页：自动重新登录后继续当前任务。
5. 想换账号重登：删除 `state.json` 与 `state.json.meta.json`。

## 命令行

```bash
playflow run workflow.yaml [--headed|--headless] [--channel chrome]
playflow run workflow.yaml --validate        # 只校验
playflow validate workflow.yaml
playflow probe [--workflow workflow.yaml] [--url 网址]
playflow record out.yaml [--workflow workflow.yaml] [--url 网址]
python -m playflow                           # 等价；不带子命令进交互菜单
```

## Python API

```python
from playflow import run_workflow, WorkflowEngine, action

summary = run_workflow("workflow.yaml")
summary = run_workflow(workflow=my_dict, base_dir=".", headless=True)

# 自定义动作：函数签名 (engine, params, step)
@action("我的动作")
def my_action(engine, params, step):
    engine.get_text(params["selector"])
    engine.logf("自定义动作执行完成", echo=True)
```

`WorkflowEngine` 提供 `current`（当前 Page）、`ctx`（BrowserContext）、
`vars`、`settings`、`locator()`、`frames_of()`、`selector_exists()` 等；注册动作后
即可在 YAML 中 `uses: 我的动作`。

## 产物与目录

| 产物 | 说明 |
|---|---|
| `state.json` + `state.json.meta.json` | 登录态及其站点记录 |
| `logs/run_日期.log` | 全程日志：动作、任务号、结果、异常堆栈 |
| `shots/序号_阶段_时间戳.png` | 关键动作截图（`settings.screenshot: false` 可关） |
| `附件/` | 下载的附件，默认 `类别/任务号_文件名` |
| `shots/probe_*.yaml`、`shots/record_*.yaml` | 探测/录制生成的 YAML 草稿 |

相对路径（登录态、附件、上传文件等）都锚定到工作流文件所在目录。

## 打包 exe / 内网离线

```bat
pip install pyinstaller
pyinstaller -F -n playflow --collect-all playwright run_playflow.py
```

产物 `dist\playflow.exe`，与 `workflow.yaml` 放同一目录双击即可。
离线环境可用 `pip download playwright pyyaml -d wheels` 制作离线包，
内网 `pip install --no-index --find-links=wheels playwright pyyaml`。
引擎通过 `channel` 驱动系统 Chrome，无需 `playwright install` 下载浏览器。

## 测试

```bash
python tests/mock_platform.py     # 终端 1
python tests/selftest.py          # 终端 2：47 项端到端验收（需本机 Chrome）
```

## 目录结构

```
playflow/            引擎包
  engine.py          工作流引擎：步骤执行、条件、tasks 循环、校验
  actions.py         44 个内置动作
  conditions.py      条件表达式求值
  template.py        {{ }} 变量模板
  browser.py         启动/登录/登录态/人工暂停
  dom.py             文字匹配、列表行、单选框、附件下载
  probe.py           页面探测
  recorder.py        操作录制
  cli.py             命令行入口
examples/            demo（mock 平台）、advanced（高级语法）、deepseek_usage（真实站点）
tests/
  mock_platform.py   本地模拟平台（演练用）
  selftest.py        自动验收脚本
```
