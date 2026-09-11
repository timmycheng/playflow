# playflow

[![CI](https://github.com/timmycheng/playflow/actions/workflows/ci.yml/badge.svg)](https://github.com/timmycheng/playflow/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/playflow)](https://pypi.org/project/playflow/)
[![Python](https://img.shields.io/pypi/pyversions/playflow)](https://pypi.org/project/playflow/)
[![License](https://img.shields.io/pypi/l/playflow)](https://github.com/timmycheng/playflow/blob/main/LICENSE)

简体中文 | **English**

A YAML-driven Playwright automation engine. Write browser automation as workflow files — built for automating repetitive batch tasks on dated intranet web platforms (legacy UIs, iframes everywhere, quirky buttons).

```yaml
name: 我的流程 (My workflow)
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
playflow run workflow.yaml          # run
playflow validate workflow.yaml     # validate only
```

- **One execution engine**: `steps + conditions + loops + variables` — complex pages need YAML edits, not code.
- **Built for real, old platforms**: text matching ignores whitespace (“提 交” matches “提交”), scans all iframes automatically, handles new tabs and same-page navigations, pierces open Shadow DOM.
- **Enterprise-friendly**: reusable login state (`state.json`), manual pause points (UKey dongles / SMS 2FA), task-queue loops, attachment archiving, screenshots and logs.
- **Unattended operation**: checkpoint/resume (`--resume`), retry only failed tasks (`--retry-failed`), JSON/HTML run reports, WeCom/DingTalk webhooks, Playwright trace on abort.
- **Authoring tools**: `probe` scans pages and suggests selectors; `record` turns manual clicks into YAML (with replay verification); `heal` checks step reachability after site redesigns; `--dry-run` executes read-only actions only.
- **More task shapes**: `from_csv` / `from_xlsx` run one task per table row; `partials` + `include` share step fragments.
- Only depends on `playwright` + `PyYAML`; drives your installed Chrome, no browser downloads.

---

## Installation

```bash
pip install playflow                 # from PyPI (includes playwright + pyyaml)
# optional extras:
pip install "playflow[http]"         # request action
pip install "playflow[notify]"       # webhook notifications
pip install "playflow[xlsx]"         # from_xlsx data-source tasks
```

Chrome must be installed locally (default `channel: chrome`); for Edge-only machines set `browser.channel` to `msedge`.

Install from source (development):

```bash
pip install -e .[dev]
```

## Quick start (offline rehearsal, no intranet needed)

```bash
python tests/mock_platform.py     # start the mock platform (SSO + UKey + iframe lists + attachments)

playflow run examples/demo.yaml   # account admin/123456, press Enter at the UKey pause
```

A real-site example: `examples/deepseek_usage.yaml` (logs into the DeepSeek open platform and exports usage/cost/balance to JSON), and `examples/advanced.yaml` (conditions, loops, HTTP calls, assertions).

## Workflow structure

```yaml
name: 流程名称                     # display name

settings:                          # all optional
  max_tasks: 1                     # tasks processed per task-block
  task_delay_seconds: [1, 3]       # random delay between tasks
  screenshot: true                 # screenshots into shots/
  attachment_dir: 附件              # attachment root dir
  dialog: accept                   # native dialogs: accept (default) / dismiss
  dialog_text: ""                  # input text for dialog=prompt
  timeout: 20000                   # default element timeout (ms)
  trace: on_error                  # off / on_error (default) / always

notify:                            # optional; push a summary to chat bots
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
  only_on_failure: false

partials:                          # optional; reusable step fragments
  登录后处理: [...]

steps:                             # main flow: runs once after login
  - uses: log
    with: { message: "操作人 {{ operator }}" }

tasks:                             # page-list loops or single-page flows
  - name: 待办列表
    url: http://平台/list
    mode: first_row                # first_row (default) / once / each_row
    max_tasks: 1
    row_selector: ""               # auto-detected tr/li/div rows by default
    link_selector: ""              # first <a href> in the row by default
    label_regex: '[A-Za-z]{1,6}-?\d{2,}'   # extract a task label from row text
    steps: [...]
  - name: 批量补录                 # or drive tasks from a table (see below)
    from_csv: todo.csv
    steps: [...]
```

### Common step fields

| Field | Description |
|---|---|
| `uses` | **required**, action name |
| `name` | step name, shows up in logs and screenshots |
| `with` | action parameters |
| `if` | condition; step is skipped when false |
| `id` | store the return value into `steps.<id>` (also `steps_<id>`) |
| `continue_on_error` | when true, failures don't abort the current task |

### Task modes

| mode | behavior | use case |
|---|---|---|
| `first_row` (default) | repeatedly take the first row → run steps → back to list, until empty or `max_tasks` reached; a stuck row terminates the loop | queues where processed rows disappear |
| `once` | no row lookup; steps run exactly once | single-page flows, exports, pure API |
| `each_row` | `remove_after: true` (default) always takes the first row; `false` walks rows by index | tables whose rows persist |

Loop variables: `row_text`, `row_index`, `label` (alias `task_no`), `category`, `category_url`, `index`.

## Action reference

### Variables

| Action | Params | Description |
|---|---|---|
| `log` | `message` | print and log |
| `set_var` | `name`, `value` | set a variable |
| `parse_var` | `from`/`selector`, `regex`, `group`, `default`, `name` | extract a variable with regex; falls back to `row_text` |
| `write_file` | `path`/`file`, `content`, `append`, `encoding` | write text (objects are JSON-dumped); relative to workflow dir |

### Navigation & pages

| Action | Params | Description |
|---|---|---|
| `goto` | `url`, `new_tab`, `wait_until`, `timeout`, `settle` | open a URL |
| `wait_for_url` | `pattern`/`url`, `regex`, `timeout` | wait for URL change |
| `reload` | `wait_until`, `timeout` | reload |
| `wait` | `ms` or `seconds` | wait |
| `wait_for` | `selector`, `frame`, `state`, `timeout` | wait for an element to appear/disappear |
| `switch_page` | `index` / `url_contains` / `title_contains` | switch current page |
| `close_page` | `which`: `current` (default) / `others` / `all_task` | close pages |
| `close_task_page` | — | close all task tabs, return to the list |
| `set_dialog` | `mode`: `accept`/`dismiss`, `text` | change native dialog policy |

### Clicking

| Action | Params | Description |
|---|---|---|
| `click` | `selector`, `frame`, `nth`, `capture_new_page`, `timeout`, `no_wait` | click by selector |
| `click_text` | `text` (keyword or list), `optional`, `capture_new_page`, `timeout` | click by text; whitespace-tolerant, iframe-aware |
| `click_row_link` | `capture_timeout` | click the current task row's link (inside task loops) |

### Forms

| Action | Params | Description |
|---|---|---|
| `fill` | `selector` or `placeholder`/`name`/`id`, `text`/`value`, `clear`, `secret`, `nth`, `frame` | fill an input |
| `check` | `selector`, `nth`, `frame` | tick a checkbox |
| `pick_radio` | `text`/`value`/`selector`, `nth`, `frame` | pick a radio by value/label/parent text |
| `select_option` | `selector`, `label`/`value`/`index`/`option`, `frame` | select a dropdown option |
| `press` | `key`, `selector` | press keys (Enter/Escape…) |
| `hover` | `selector` or `text`, `nth`, `frame` | hover |
| `scroll` | `selector` or `by: [x, y]` or `bottom: true`, `ms` | scroll |
| `upload` | `selector`, `file`/`files` | upload files (relative to workflow dir) |

### Extract / HTTP / download

| Action | Params | Description |
|---|---|---|
| `extract` | `selector`, `what`: `text`(default)/`text_content`/`html`/`value`/`attr`/`count`/`href`, `attr`, `all`, `nth`, `join`, `name`, `default`, `until`, `interval`, `timeout`, `frame` | extract page content into a variable; `until` retries polling |
| `evaluate` | `js`/`code`/`script`, `name`, `frame` | run JS and store the result |
| `request` | `url`, `method`, `params`, `headers`, `json`, `data`, `timeout`, `verify`, `output`, `name` | HTTP request (needs requests), result has `status/text/json/headers` |
| `download` | `selector`, `text`, `dir`, `subdir`, `filename_prefix`, `optional`, `timeout` | download attachments; auto-finds “附件/下载” links or `download` attributes |

### Assertions & human-in-the-loop

| Action | Params | Description |
|---|---|---|
| `expect_text` | `text` | page must contain the text |
| `expect_visible` | `selector`, `frame` | element must be visible |
| `expect_url` | `pattern`/`url`, `regex` | URL must match (glob by default) |
| `assert` | `condition`, `message` | generic assertion |
| `screenshot` | `name` | manual screenshot |
| `fail` | `message` | fail explicitly |
| `pause` | `message` | pause for manual actions, resume with Enter |

### Control flow

| Action | Params | Description |
|---|---|---|
| `if` | `with: {condition}` + `then:` / `else:` | conditional branch |
| `for_each` | `over`/`list`/`rows`/`range`, `as`, `frame` + `do:` | iterate; `{{ item }}`, `{{ index }}` |
| `repeat` | `times` + `do:` | fixed-count loop |
| `while` | `condition`, `as`, `max`, `init` + `do:` | while loop; `max` guards dead loops |
| `break` / `continue` | — | loop control |
| `run_steps` | + `do:` / `steps:` | inline sub-steps |

### Authoring tools

| Action | Params | Description |
|---|---|---|
| `probe` | `file`, `all_pages`, `max`, `steps` | scan the page and print elements + selector suggestions; writes `shots/probe_*.yaml` |
| `record` | `file`, `verify` | turn manual operations into YAML steps (passwords masked); `verify: true` replays recorded steps and annotates failures in the YAML |

## Conditions & functions

`if`, `while`, and `assert` accept three styles.

**String expressions** (most common):

```yaml
if: "body_text contains 申请"
if: "exists('#submit') or visible('.ok')"
if: "count('table tr') >= 5 and url() contains /list"
if: "title == '任务详情'"
if: "st.status == 200"
```

Operators: `== != > < >= <=`, `contains / not contains`, `matches` (regex), `startswith / endswith`, `in / not in`; logic `and / or / not` with parentheses.

**Condition functions**: `exists(sel[, frame])`, `visible`, `absent`, `has_text(text)`, `count(sel[, frame])`, `text(sel[, frame])`, `attr(sel, attr[, frame])`, `value(sel[, frame])`, `page_count()`, `url()`, `title()`.

**Structured maps**:

```yaml
if:
  and:
    - {contains: ["{{ body_text }}", "申请"]}
    - {exists: "#submit"}
```

## Variables

- Keys under `env:` are referenced as `{{ operator }}` or `{{ env.operator }}`.
- A step's `id: xx` stores its return value into `{{ steps.xx }}` and `{{ steps_xx }}`.
- Task loops provide `{{ row_text }}` / `{{ row_index }}` / `{{ label }}` / `{{ category }}`.
- A whole-string single template keeps the original type (`{{ resp.status }} == 200`).

## Login state & manual pause

1. On start, playflow checks `state.json` (or `login.state_file`); a file alone never means "skip": it opens `success_url`, waits out client-side/late redirects to login, and only skips when no login/verification markers appear. Expired cookies are treated as invalid up front.
2. If invalid or from a different site, it runs the `login` shortcut: fills credentials, clicks login; with `manual_pause: true` it pauses for UKey/SMS, resumes with Enter and validates via `success_url`.
3. On success the session is written back to `state.json` (plus a `.meta.json` site record to prevent cross-workflow misuse).
4. If kicked back to a login page mid-run, it re-logs-in automatically and continues the current task.
5. To switch accounts: delete `state.json` and `state.json.meta.json`.

## Checkpoint & retry

Progress is written next to the workflow file:

- `<workflow>.progress.json` — completed task labels per task-block, flushed after every task;
- `<workflow>.failed.json` — failed task labels with error messages.

```bash
playflow run workflow.yaml --resume         # skip completed tasks
playflow run workflow.yaml --retry-failed   # process only tasks recorded as failed
```

## Dry-run: read-only rehearsal

```bash
playflow run workflow.yaml --dry-run
```

Every built-in action is tagged read or write (`goto`/`extract`/assertions are reads; `click`/`fill`/`upload` are writes; `request` is classified by HTTP method). In dry-run, write actions are skipped and logged, inter-task delays are zeroed, and each list task processes 1 row — useful to validate a flow against a live platform safely (login still runs for real).

## heal: self-check after site redesigns

```bash
playflow heal workflow.yaml --url http://平台/list --fix
playflow heal workflow.yaml --headed --min-score 0.6
```

After login it opens `--url` and checks every step's `selector` / text parameters against the page; failing steps get similarity-ranked candidates (button text / id / name / placeholder). `--fix` never touches the original file; it writes `workflow.healed.yaml` with suggestions above a threshold (default 0.55). heal inspects a single page state (after login + `--url`); elements that only appear mid-flow need a second pass per page.

## Run reports & trace

Every run (including aborts) writes `reports/run_<time>.json` and `.html` (task lists, success/failure/skip, duration; the HTML is a single file ready to share). On fatal aborts, `settings.trace: on_error` (default) saves a Playwright trace to `shots/trace_*.zip` — replay with `playwright show-trace <file>`; `always` saves every run, `off` disables.

## Notifications (WeCom / DingTalk / generic webhook)

```yaml
notify:
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx   # or a list of URLs
  only_on_failure: false      # true = only push on failure/abort
```

Payload format is auto-detected by domain (WeCom / DingTalk markdown; others get `{title, text}` JSON). Requires `pip install "playflow[notify]"`; notification failures are logged but never fail the run.

## Data-source tasks (from_csv / from_xlsx)

```yaml
tasks:
  - name: 批量补录
    from_csv: todo.csv             # or from_xlsx: todo.xlsx (optionally sheet: Sheet1)
    encoding: gbk                  # csv only; defaults to utf-8-sig
    label_column: 单号             # optional; defaults to the first column
    max_tasks: 99
    url: "http://平台/detail?no={{ row.单号 }}"   # optional; per-row page
    steps:
      - uses: fill
        with: { selector: "#city", text: "{{ row.城市 }}" }
```

Each data row runs the steps once; column names become variables (`{{ 单号 }}`), the whole row is `{{ row }}`, and `{{ label }}` / `{{ row_text }}` / `{{ row_index }}` work as usual. Data-source tasks ignore `mode`; checkpoint/retry applies too. xlsx needs `pip install "playflow[xlsx]"`.

## Reusable fragments (partials / include)

```yaml
partials:
  归档:                                   # a plain list of steps
    - uses: download
      with: { optional: true }
    - uses: close_task_page
  公共登录后: { file: common.yaml }        # or loaded from another YAML file

steps:
  - include: 公共登录后
tasks:
  - name: 待办
    url: http://平台/list
    steps:
      - uses: click_row_link
      - include: 归档
```

`include` may nest (with cycle detection) and works inside `steps`, `login.steps`, `tasks[].steps`, and `then/else/do` children; `playflow validate` checks references.

## CLI

```bash
playflow run workflow.yaml [--headed|--headless] [--channel chrome]
playflow run workflow.yaml --dry-run         # read-only rehearsal
playflow run workflow.yaml --resume          # checkpoint resume
playflow run workflow.yaml --retry-failed    # rerun only failed tasks
playflow run workflow.yaml --validate        # validate only
playflow validate workflow.yaml
playflow probe [--workflow workflow.yaml] [--url URL]
playflow record out.yaml [--workflow workflow.yaml] [--url URL] [--verify]
playflow heal workflow.yaml [--url URL] [--fix] [--headed] [--min-score 0.55]
python -m playflow                           # same; no subcommand = interactive menu
```

## Python API

```python
from playflow import run_workflow, WorkflowEngine, action

summary = run_workflow("workflow.yaml")
summary = run_workflow(workflow=my_dict, base_dir=".", headless=True)
summary = run_workflow("workflow.yaml", dry_run=True)     # read-only
summary = run_workflow("workflow.yaml", resume=True)      # checkpoint resume

# custom action: signature (engine, params, step); @action defaults to mode="write"
@action("我的动作")
def my_action(engine, params, step):
    engine.get_text(params["selector"])
    engine.logf("done", echo=True)

@action("只读抓取", mode="read")     # still runs under dry-run
def my_reader(engine, params, step):
    return engine.get_text(params["selector"])
```

`WorkflowEngine` exposes `current` (current Page), `ctx` (BrowserContext), `vars`, `settings`, `locator()`, `frames_of()`, `selector_exists()`, and more. Library output goes through standard `logging` (logger name `playflow`); a default console handler is attached automatically for scripts, and `playflow.disable_console_logging()` removes it. File logs (`logs/`) are unaffected.

## Artifacts & directories

| Artifact | Description |
|---|---|
| `state.json` + `state.json.meta.json` | login state and its site record |
| `<workflow>.progress.json` / `<workflow>.failed.json` | checkpoint progress / failure list (for `--resume` / `--retry-failed`) |
| `logs/run_<date>.log` | full run log: actions, task labels, results, stack traces |
| `shots/<n>_<stage>_<ts>.png` | screenshots of key actions (`settings.screenshot: false` to disable) |
| `shots/trace_*.zip` | Playwright trace saved on fatal aborts |
| `reports/run_<time>.json` / `.html` | structured run reports |
| `附件/` | downloaded attachments, default `category/taskno_filename` |
| `shots/probe_*.yaml`, `shots/record_*.yaml`, `*.healed.yaml` | probe / record / heal generated YAML drafts |

Relative paths (login state, attachments, uploads) are anchored to the workflow file's directory.

## Packaging / offline intranets

```bat
pip install pyinstaller
pyinstaller -F -n playflow --collect-all playwright run_playflow.py
```

The resulting `dist\playflow.exe` runs next to a `workflow.yaml`. For offline machines, build offline wheels from PyPI (see [RELEASING.md](RELEASING.md)):

```bash
pip download playflow -d wheels                                # internet machine
pip install --no-index --find-links=wheels playflow            # intranet machine
```

The engine drives system Chrome via `channel`, so no `playwright install` browser download is needed.

## AI usage & disclosure

- 🤖 **Guide for AI agents**: if you are an AI agent or coding assistant, read the
  [AI usage guide](docs/ai.md) first — installation, invocation, YAML writing rules,
  action cheat sheet, and the validate → dry-run → run workflow.
- 📝 **AI-assisted development disclosure**: requirements and acceptance by
  [@timmycheng](https://github.com/timmycheng); code/tests/docs/CI largely generated by
  the **ZCode** CLI coding agent powered by **GLM** (trained by Z.ai), gated by
  pytest/selftest/ruff and CI. See [docs/ai.md](docs/ai.md).

## Development & testing

```bash
pip install -e .[dev]
pytest                        # 60+ unit tests, no browser needed
python tests/selftest.py      # 60+ e2e checks; auto-starts the mock platform, needs local Chrome
ruff check .                  # lint
```

GitHub Actions runs ruff + unit tests (Ubuntu/Windows) + build checks + e2e on push. See [RELEASING.md](RELEASING.md) for the release process and [CHANGELOG.md](CHANGELOG.md) for changes.

## Repository layout

```
playflow/            engine package
  engine.py          workflow engine: steps, conditions, task/data loops, checkpoints, partials, validation
  actions.py         45 built-in actions (read/write tagged)
  conditions.py      condition expression evaluation
  template.py        {{ }} variable templates
  browser.py         launch / login / session state / manual pause
  dom.py             whitespace-tolerant text matching, list rows, radios, downloads
  probe.py           page probing
  recorder.py        operation recording (with replay verification comments)
  heal.py            step reachability checks & fix suggestions
  report.py          run reports (JSON/HTML)
  notify.py          WeCom / DingTalk / generic webhooks
  cli.py             CLI entry (run/validate/probe/record/heal)
examples/            demo (mock platform), advanced (advanced syntax), deepseek_usage (real site)
tests/
  unit/              pytest unit tests (no browser)
  mock_platform.py   local mock platform
  selftest.py        e2e acceptance (auto-starts the mock platform)
```

## License

[MIT](LICENSE)
