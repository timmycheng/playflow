# -*- coding: utf-8 -*-
"""工作流引擎：加载 YAML、渲染变量、执行步骤与任务循环。"""
from __future__ import annotations

import copy
import datetime
import json
import random
import re
import time
import traceback
from pathlib import Path

import yaml

from .actions import ensure_registered as _ensure_actions
from .browser import is_login_page, launch, login, perform_login, safe_goto
from .conditions import eval_condition
from .dom import find_row, norm_text
from .errors import AbortError, ConfigError, LoopBreak, LoopContinue, StepError
from .registry import ACTIONS, action_mode
from .template import as_bool, render
from .utils import anchor, base_dir, ensure_console_logging, log, set_base_dir, shot
from .utils import base_dir as get_base_dir  # run_workflow 参数会遮蔽同名函数

_ensure_actions()   # 导入即注册全部内置动作

DEFAULT_SETTINGS = {
    "screenshot": True,
    "max_tasks": 1,
    "task_delay_seconds": [1, 3],
    "attachment_dir": "附件",
    "dialog": "accept",
    "dialog_text": "",
    "timeout": 20000,
    "trace": "on_error",   # off / on_error / always
}

TASK_MODES = ("once", "first_row", "each_row")
DATA_SOURCES = ("from_csv", "from_xlsx")
_MAX_INCLUDE_DEPTH = 16


class WorkflowEngine:
    """执行工作流：持有浏览器上下文、当前页面、变量与统计。"""

    def __init__(self, workflow, logf=log, dry_run=False, resume=False, retry_failed=False):
        self.wf = workflow or {}
        self.settings = {**DEFAULT_SETTINGS, **(self.wf.get("settings") or {})}
        self.logf = logf
        self.dry_run = bool(dry_run)
        env = dict(self.wf.get("env") or {})
        self.vars = dict(env)
        self.vars["env"] = env
        self.vars["settings"] = self.settings
        self.ctx = None
        self.current = None
        self.list_page = None
        self.row = None
        self.opened_pages = []
        self.state_path = None
        self.summary = {}
        # 断点续跑 / 失败重试
        self.resume = bool(resume)
        self.retry_failed = bool(retry_failed)
        self.done = {}          # {任务名: set(label)} 已完成
        self.failed = {}        # {任务名: {label: 错误信息}}
        self.retry_labels = None  # retry_failed 时：{任务名: set(label)} 仅处理这些
        self.progress_path = None
        self.failed_path = None
        self._skip_reason = ""

    # ------------------------------------------------ 页面 / frame

    def frames_of(self, page=None):
        page = page or self.current
        try:
            return list(page.frames)
        except Exception:
            return []

    def resolve_frame(self, spec=None, page=None):
        """frame 规格：None/auto=全 frame 扫描、main、#N 或数字、URL 子串。"""
        page = page or self.current
        frames = self.frames_of(page)
        if not frames:
            raise StepError("当前没有可用页面")
        if spec is None or spec in ("auto", "all", ""):
            return None
        if spec == "main":
            return page.main_frame
        s = str(spec)
        if s.startswith("#"):
            s = s[1:]
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(frames):
                return frames[idx]
            raise StepError("frame 序号 %d 超出范围（共 %d 个）" % (idx, len(frames)))
        for fr in frames:
            if s in (fr.url or ""):
                return fr
        raise StepError("找不到 URL 包含 %r 的 frame" % spec)

    def locator(self, selector, frame=None, page=None):
        """返回 (frame, locator)；frame 为空时跨所有 frame 找第一个命中的。"""
        fr = self.resolve_frame(frame, page)
        if fr is not None:
            return fr, fr.locator(selector)
        frames = self.frames_of(page)
        for f in frames:
            try:
                loc = f.locator(selector)
                if loc.count() > 0:
                    return f, loc
            except Exception:
                continue
        return frames[0], frames[0].locator(selector)

    def selector_exists(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc.count() > 0
        except Exception:
            return False

    def selector_visible(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return True
            return False
        except Exception:
            return False

    def count_elements(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc.count()
        except Exception:
            return 0

    def _first_locator(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc if loc.count() > 0 else None
        except Exception:
            return None

    def get_text(self, selector, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.inner_text() if loc is not None else ""
        except Exception:
            return ""

    def get_attr(self, selector, attr, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.get_attribute(str(attr)) if loc is not None else None
        except Exception:
            return None

    def get_value(self, selector, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.input_value() if loc is not None else ""
        except Exception:
            return ""

    def has_text(self, text, page=None):
        nk = norm_text(text)
        if not nk:
            return False
        for fr in self.frames_of(page):
            try:
                body = fr.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                continue
            if nk in norm_text(body):
                return True
        return False

    def timeout(self, params=None, default=None):
        if params and params.get("timeout") is not None:
            return int(params["timeout"])
        if default is not None:
            return int(default)
        return int(self.settings.get("timeout", 20000))

    def snap(self, stage):
        if self.settings.get("screenshot", True) and self.current is not None:
            shot(self.current, stage)

    def wait_after_nav(self, params=None):
        if (params or {}).get("no_wait"):
            return
        try:
            self.current.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    def click_capture(self, target, params=None, source_page=None):
        """点击并捕获可能的新标签页；无新页则视为当前页跳转。"""
        params = params or {}
        source = source_page or self.current
        got = []

        def _on_page(pg):
            got.append(pg)

        self.ctx.on("page", _on_page)
        try:
            target.click(timeout=self.timeout(params))
        except Exception:
            self.ctx.remove_listener("page", _on_page)
            raise
        deadline = time.time() + float(params.get("capture_timeout", 5000)) / 1000.0
        while time.time() < deadline and not got:
            try:
                source.wait_for_timeout(120)
            except Exception:
                break
        self.ctx.remove_listener("page", _on_page)
        if got:
            np = got[0]
            try:
                np.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            self.opened_pages.append(np)
            self.current = np
            self.logf("捕获到新标签页：%s" % (np.url or ""))
        else:
            self.wait_after_nav(params)
            self.logf("无新标签页，按当前页跳转处理：%s" % (self.current.url or ""))
        self.snap("click_capture")
        return self.current.url

    # ------------------------------------------------ 步骤执行

    def run_steps(self, steps, vars_extra=None):
        if not steps:
            return
        if vars_extra:
            self.vars.update(vars_extra)
        for step in steps:
            self.run_step(step)

    def run_step(self, step):
        if not isinstance(step, dict):
            raise ConfigError("步骤必须是映射（含 uses 字段），实际为：%r" % (step,))
        name = step.get("name") or step.get("uses") or "?"
        if "if" in step and not eval_condition(step["if"], self):
            self.logf("跳过步骤 %s（条件不满足）" % name)
            return
        uses = step.get("uses")
        if not uses:
            raise ConfigError("步骤 %r 缺少 uses 字段" % name)
        if uses not in ACTIONS:
            raise ConfigError("未知步骤 uses: %r（可用：%s）"
                              % (uses, "、".join(sorted(ACTIONS))))
        params = render(step.get("with") or {}, self.vars)
        if self.dry_run and self._dry_run_skip(uses, params):
            self.logf("[dry-run] 跳过写动作 %s（%s）" % (name, uses), echo=True)
            return
        try:
            result = ACTIONS[uses](self, params, step)
        except (LoopBreak, LoopContinue):
            raise
        except StepError:
            if step.get("continue_on_error"):
                self.logf("步骤 %s 出错但已忽略（continue_on_error）" % name)
                return
            raise
        except (AbortError, ConfigError):
            raise
        except Exception as ex:
            if step.get("continue_on_error"):
                self.logf("步骤 %s 异常但已忽略：%s" % (name, ex))
                return
            self.logf(traceback.format_exc())
            raise StepError("步骤 %s 执行失败：%s" % (name, ex))
        if step.get("id"):
            self.vars.setdefault("steps", {})[step["id"]] = result
            self.vars["steps_" + step["id"]] = result
        if step.get("set") and isinstance(result, dict):
            self.vars.update(result)

    def _dry_run_skip(self, uses, params) -> bool:
        """dry-run 时是否跳过该动作：写动作跳过，request 再按方法细分。"""
        if uses == "request":
            return str(params.get("method", "GET")).upper() not in ("GET", "HEAD", "OPTIONS")
        return action_mode(uses) == "write"

    # ------------------------------------------------ 断点续跑

    def set_checkpoint_paths(self, stem):
        """设置进度/失败清单文件路径（相对路径锚定 base_dir）。"""
        self.progress_path = anchor((stem or "playflow") + ".progress.json")
        self.failed_path = anchor((stem or "playflow") + ".failed.json")

    def load_progress(self):
        """--resume：读取进度文件，跳过已完成的任务标签。"""
        if self.progress_path is None or not self.progress_path.exists():
            return
        try:
            data = json.loads(self.progress_path.read_text(encoding="utf-8-sig"))
        except Exception as ex:
            self.logf("进度文件读取失败，忽略（%s）：%s" % (self.progress_path.name, ex), echo=True)
            return
        wf_name = self.wf.get("name") or ""
        owner = str(data.get("workflow") or "")
        if owner and wf_name and owner != wf_name:
            self.logf("进度文件属于工作流 %r（当前 %r），忽略。" % (owner, wf_name), echo=True)
            return
        for cat, labels in (data.get("done") or {}).items():
            self.done.setdefault(str(cat), set()).update(str(x) for x in (labels or []))
        total = sum(len(v) for v in self.done.values())
        self.logf("断点续跑：已加载 %d 个已完成任务（%s）" % (total, self.progress_path.name),
                  echo=True)

    def load_failed(self):
        """--retry-failed：读取失败清单，只处理其中记录的任务标签。"""
        if self.failed_path is None or not self.failed_path.exists():
            self.logf("没有失败清单文件（%s），本次按正常流程执行。" % (self.failed_path or "failed.json"),
                      echo=True)
            return
        try:
            data = json.loads(self.failed_path.read_text(encoding="utf-8-sig"))
        except Exception as ex:
            self.logf("失败清单读取失败，忽略：%s" % ex, echo=True)
            return
        self.retry_labels = {}
        for cat, labels in (data.get("failed") or {}).items():
            self.retry_labels[str(cat)] = set(str(x) for x in (labels or {}))
        total = sum(len(v) for v in self.retry_labels.values())
        self.logf("失败重试：仅处理清单中的 %d 个任务（%s）" % (total, self.failed_path.name),
                  echo=True)

    def record_task_result(self, category, label, ok, error=""):
        if ok:
            self.done.setdefault(category, set()).add(label)
            self.failed.get(category, {}).pop(label, None)
        else:
            self.failed.setdefault(category, {})[str(label)] = str(error)
        self.save_checkpoint()

    def save_checkpoint(self):
        """把进度与失败清单落盘（每次任务结束调用；未设置路径时为空操作）。"""
        if self.progress_path is None:
            return
        data = {
            "workflow": self.wf.get("name") or "",
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "done": {cat: sorted(labels) for cat, labels in self.done.items()},
        }
        try:
            self.progress_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.failed_path is not None:
                self.failed_path.write_text(json.dumps(
                    {"workflow": self.wf.get("name") or "",
                     "updated_at": data["updated_at"],
                     "failed": self.failed}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as ex:
            self.logf("进度文件写入失败：%s" % ex)

    def should_skip(self, category, label) -> bool:
        """本任务标签是否应跳过（断点续跑已完成 / 不在失败重试清单）。"""
        self._skip_reason = ""
        if self.resume and label in self.done.get(category, set()):
            self._skip_reason = "断点续跑：已完成"
            return True
        if self.retry_labels is not None and label not in (
                self.retry_labels.get(category) or set()):
            self._skip_reason = "不在失败重试清单"
            return True
        return False

    def total_counts(self):
        """全部任务的 成功/失败/跳过 计数。"""
        succ = fail = skip = 0
        for st in self.summary.values():
            succ += len(st.get("成功") or [])
            fail += len(st.get("失败") or [])
            skip += len(st.get("跳过") or [])
        return {"成功": succ, "失败": fail, "跳过": skip}


# ---------------------------------------------------------------- partials / include

def _load_partial_steps(pdef, name, base=None):
    """把 partials 项解析成步骤列表：列表原样；{file: 路径} 从文件加载。"""
    if isinstance(pdef, dict) and "file" in pdef:
        p = Path(str(pdef["file"]))
        if not p.is_absolute():
            p = (base or base_dir()) / p
        data = load_workflow(p)
        key = pdef.get("key") or name
        if isinstance(data, list):
            steps = data
        elif isinstance(data, dict) and key in data:
            steps = data[key]
        else:
            raise ConfigError("partial 文件 %s 应包含步骤列表，或含 %r 键的映射" % (p, key))
    elif isinstance(pdef, list):
        steps = pdef
    else:
        raise ConfigError("partials[%r] 应为步骤列表，或 {file: 路径}（可加 key）" % (name,))
    if not isinstance(steps, list):
        raise ConfigError("partials[%r] 应解析为步骤列表" % (name,))
    return steps


def expand_partials(workflow: dict, base: str | Path | None = None) -> dict:
    """把工作流中所有 include 步骤展开为 partials 内容（深拷贝，支持嵌套）。"""
    wf = workflow if isinstance(workflow, dict) else {}
    partials = wf.get("partials") or {}
    if not partials:
        # 没有 partials 时仍需检查：include 了未定义的 partial 要报错而不是静默跳过
        if "include" not in repr(wf):
            return wf
        partials = {}

    def walk(steps, depth, stack):
        out = []
        for st in steps or []:
            if isinstance(st, dict) and "include" in st:
                name = st["include"]
                if "uses" in st:
                    raise ConfigError("include 步骤不能同时包含 uses（%r）" % st.get("name"))
                if name not in partials:
                    raise ConfigError("include 引用了未定义的 partial：%r（已定义：%s）"
                                      % (name, "、".join(str(x) for x in partials)))
                if depth >= _MAX_INCLUDE_DEPTH or name in stack:
                    raise ConfigError("include 嵌套过深或循环引用：%s → %r"
                                      % (" → ".join(str(s) for s in stack), name))
                sub = copy.deepcopy(_load_partial_steps(partials.get(name), name, base))
                out.extend(walk(sub, depth + 1, tuple(stack) + (name,)))
            elif isinstance(st, dict):
                st = dict(st)
                for key in ("then", "else", "do", "steps"):
                    if key in st and isinstance(st[key], list):
                        st[key] = walk(st[key], depth, stack)
                out.append(st)
            else:
                out.append(st)
        return out

    wf2 = copy.deepcopy(wf)
    if isinstance(wf2.get("steps"), list):
        wf2["steps"] = walk(wf2["steps"], 0, ())
    login = wf2.get("login")
    if isinstance(login, dict) and isinstance(login.get("steps"), list):
        login["steps"] = walk(login["steps"], 0, ())
    for task in (wf2.get("tasks") or []):
        if isinstance(task, dict) and isinstance(task.get("steps"), list):
            task["steps"] = walk(task["steps"], 0, ())
    return wf2


# ---------------------------------------------------------------- 数据源任务

def load_data_rows(task):
    """读取 from_csv / from_xlsx 的数据行，返回 {列名: 值} 列表。"""
    if task.get("from_csv"):
        import csv
        path = anchor(task["from_csv"])
        if not path.exists():
            raise ConfigError("from_csv 文件不存在：%s" % path)
        with open(path, encoding=str(task.get("encoding") or "utf-8-sig"),
                  newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    path = anchor(task["from_xlsx"])
    if not path.exists():
        raise ConfigError("from_xlsx 文件不存在：%s" % path)
    try:
        import openpyxl
    except ImportError:
        raise ConfigError("未安装 openpyxl，无法读取 xlsx（pip install playflow[xlsx]）")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb[task["sheet"]] if task.get("sheet") else wb.active
        rows = list(ws.values)
    finally:
        wb.close()
    if not rows:
        return []
    header = [str(h or "").strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        out.append({header[i]: ("" if v is None else v)
                    for i, v in enumerate(r) if i < len(header) and header[i]})
    return out


# ---------------------------------------------------------------- 任务循环

def _row_label(task, text, done):
    regex = task.get("label_regex")
    if regex:
        m = re.search(str(regex), text or "")
        if m:
            return m.group(int(task.get("label_group", 0)))
    label = " ".join(str(text or "").split())[:40]
    return label or "row%d" % (done + 1)


def _data_label(row, label_column, index):
    if label_column:
        val = row.get(label_column)
        if val not in (None, ""):
            return str(val)
    for v in row.values():
        if v not in (None, ""):
            return str(v)[:40]
    return "row%d" % (index + 1)


def _close_opened(engine):
    for pg in list(engine.opened_pages):
        try:
            pg.close()
        except Exception:
            pass
    engine.opened_pages = []


def _task_delay(engine, page):
    try:
        lo, hi = engine.settings["task_delay_seconds"]
    except Exception:
        lo, hi = 1, 3
    delay = random.uniform(float(lo), float(hi))
    if delay <= 0:
        return
    engine.logf("随机等待 %.1f 秒…" % delay)
    try:
        page.wait_for_timeout(int(delay * 1000))
    except Exception:
        time.sleep(delay)


def _run_one_task(engine, steps, label, stat, snap_stage=None, category=None):
    """执行一个任务单元的 steps；失败记入 stat，不让整个任务中止。"""
    engine.opened_pages = []
    if engine.list_page is not None:
        engine.current = engine.list_page
    ok, err_msg = False, ""
    try:
        if snap_stage:
            engine.snap(snap_stage)
        engine.run_steps(steps)
        ok = True
        engine.logf("任务 %s 处理成功%s" % (label, "（dry-run）" if engine.dry_run else ""),
                    echo=True)
    except (ConfigError, AbortError):
        raise
    except StepError as ex:
        err_msg = str(ex)
        engine.logf("任务 %s 失败：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    except Exception as ex:
        err_msg = str(ex)
        engine.logf("任务 %s 异常：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    finally:
        _close_opened(engine)
        if engine.list_page is not None:
            engine.current = engine.list_page
    (stat["成功"] if ok else stat["失败"]).append(label)
    if category:
        engine.record_task_result(category, label, ok, err_msg)
    return ok


def _finish_task_log(engine, name, stat):
    engine.logf("========== 任务 %s 结束：成功 %d，失败 %d，跳过 %d =========="
                % (name, len(stat["成功"]), len(stat["失败"]), len(stat["跳过"])),
                echo=True)


def _goto_list_page(engine, list_page, task, name, stat):
    if not task.get("url"):
        return True
    try:
        resp = safe_goto(list_page, task["url"], "列表页")
    except AbortError as ex:
        stat["错误"] = str(ex).split("\n")[0]
        engine.logf("%s：%s" % (name, stat["错误"]), echo=True)
        return False
    if resp is not None and getattr(resp, "status", 0) >= 400:
        stat["错误"] = "列表页返回 HTTP %d" % resp.status
        engine.logf("%s：%s" % (name, stat["错误"]), echo=True)
        return False
    return True


def run_data_task(engine, task):
    """from_csv / from_xlsx：把每一行数据当作一次任务执行。"""
    name = str(task.get("name") or "数据任务")
    steps = task.get("steps") or []
    stat = engine.summary.setdefault(name, {"成功": [], "失败": [], "跳过": [], "错误": ""})
    engine.vars["category"] = name
    engine.vars["category_url"] = task.get("url", "")
    max_tasks = int(task.get("max_tasks", engine.settings["max_tasks"]))
    if engine.dry_run:
        max_tasks = min(max_tasks, 1)
    engine.logf("========== 数据任务：%s（%s）=========="
                % (name, task.get("from_csv") or task.get("from_xlsx")), echo=True)
    try:
        rows = load_data_rows(task)
    except ConfigError as ex:
        stat["错误"] = str(ex)
        engine.logf("%s：%s" % (name, ex), echo=True)
        _finish_task_log(engine, name, stat)
        return
    engine.logf("数据任务 %s：共 %d 行" % (name, len(rows)), echo=True)
    label_col = task.get("label_column")
    done = 0
    for i, row in enumerate(rows):
        if done >= max_tasks:
            break
        label = _data_label(row, label_col, i)
        if engine.should_skip(name, label):
            stat["跳过"].append(label)
            engine.logf("数据任务 %s：跳过 %s（%s）" % (name, label, engine._skip_reason),
                        echo=True)
            continue
        engine.vars.update({
            "row": row, "label": label, "task_no": label,
            "row_text": json.dumps(row, ensure_ascii=False),
            "row_index": i, "index": done})
        if task.get("url"):
            url = render(task["url"], engine.vars)
            try:
                safe_goto(engine.current, str(url), "数据行页面")
            except AbortError as ex:
                engine.record_task_result(name, label, False, str(ex))
                stat["失败"].append(label)
                engine.logf("打开 %s 失败：%s" % (url, ex), echo=True)
                continue
        engine.list_page = None
        _run_one_task(engine, steps, label, stat, snap_stage="%s_数据行" % label, category=name)
        done += 1
        _task_delay(engine, engine.current)
    _finish_task_log(engine, name, stat)


def run_task(engine, task):
    """执行一个 tasks 定义。mode：
      once       只执行一次（不找列表行；适合单页/接口流程）
      first_row  队列循环：反复取第一行 → steps → 回列表（默认）
      each_row   表格循环：remove_after=true 始终取第一行；false 按行号依次取
    数据源任务（from_csv / from_xlsx）不使用 mode，每一行数据执行一次 steps。
    """
    name = str(task.get("name") or "未命名任务")
    if task.get("from_csv") or task.get("from_xlsx"):
        run_data_task(engine, task)
        return

    mode = str(task.get("mode") or "first_row").lower()
    if mode not in TASK_MODES:
        raise ConfigError("tasks[%s].mode 只能是 once / first_row / each_row" % name)
    steps = task.get("steps") or []
    stat = engine.summary.setdefault(name, {"成功": [], "失败": [], "跳过": [], "错误": ""})
    max_tasks = int(task.get("max_tasks", engine.settings["max_tasks"]))
    if engine.dry_run:
        max_tasks = min(max_tasks, 1)   # 写动作被跳过，列表行不会消失，防止死循环
    remove_after = as_bool(task.get("remove_after", True))
    engine.vars["category"] = name
    engine.vars["category_url"] = task.get("url", "")
    engine.logf("========== 任务：%s（%s，mode=%s%s）=========="
                % (name, task.get("url", ""), mode,
                   "，dry-run" if engine.dry_run else ""), echo=True)

    list_page = engine.current
    engine.list_page = list_page
    if not _goto_list_page(engine, list_page, task, name, stat):
        engine.list_page = None
        return

    if mode == "once":
        engine.row = None
        engine.vars.update({"label": "", "task_no": "", "row_text": "", "row_index": 0})
        if engine.should_skip(name, name):
            stat["跳过"].append(name)
            engine.logf("任务 %s：跳过 %s（%s）" % (name, name, engine._skip_reason), echo=True)
        else:
            _run_one_task(engine, steps, name, stat, category=name)
        _finish_task_log(engine, name, stat)
        engine.list_page = None
        return

    seen, done, pos = set(), 0, 0
    while done < max_tasks:
        if is_login_page(list_page):
            engine.logf("检测到登录态失效，重新登录（可能需要人工验证）…", echo=True)
            perform_login(engine, list_page)
            if task.get("url"):
                safe_goto(list_page, task["url"], "列表页")

        # remove_after 模式：处理过的行会从列表消失，始终看第一行；
        # 只有跳行（断点续跑/失败重试）后才用游标 pos 越过该跳过的行。
        row_index = pos if (pos > 0 or not remove_after) else 0
        info = find_row(list_page, task.get("row_selector", ""),
                        task.get("link_selector", ""), index=row_index)
        if not info:
            engine.logf("任务 %s：列表已空，处理结束。" % name, echo=True)
            break

        label = _row_label(task, info["text"], done)
        if engine.should_skip(name, label):
            stat["跳过"].append(label)
            engine.logf("任务 %s：跳过 %s（%s）" % (name, label, engine._skip_reason),
                        echo=True)
            pos = row_index + 1   # 该行不会被处理掉，游标越过它
            continue
        if remove_after and pos == 0 and label in seen:
            stat["跳过"].append(label)
            engine.logf("行 %s 反复无法完成，终止该任务以免死循环。" % label, echo=True)
            break
        seen.add(label)
        engine.vars.update({"label": label, "task_no": label, "row_text": info["text"],
                            "row_index": row_index, "index": done})
        engine.row = dict(info)
        engine.row["task_no"] = label

        _run_one_task(engine, steps, label, stat, snap_stage="%s_列表行" % label, category=name)
        done += 1
        if not remove_after:
            pos += 1   # 行不消失，下一轮看下一行

        if task.get("url"):
            try:
                safe_goto(list_page, task["url"], "列表页")
            except AbortError as ex:
                stat["错误"] = str(ex).split("\n")[0]
                engine.logf("返回列表页失败：%s" % stat["错误"], echo=True)
                break
        _task_delay(engine, list_page)

    _finish_task_log(engine, name, stat)
    engine.list_page = None


# ---------------------------------------------------------------- 运行入口

def load_workflow(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError("未找到工作流文件：%s" % p)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as ex:
        raise ConfigError("YAML 语法错误（%s）：%s\n请检查缩进与冒号后是否有空格。"
                          % (p.name, ex))
    except Exception as ex:
        raise ConfigError("无法读取工作流 %s：%s" % (p.name, ex))
    return data if data is not None else {}


def _normalize_trace_mode(value):
    s = str(value if value is not None else "on_error").strip().lower()
    if s in ("true", "always", "1", "yes"):
        return "always"
    if s in ("false", "none", "off", "0", "no", ""):
        return "off"
    return "on_error"


def _save_trace(ctx, engine, tag):
    try:
        d = base_dir() / "shots"
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = d / ("trace_%s_%s.zip" % (tag, ts))
        ctx.tracing.stop(path=str(path))
        engine.logf("已保存 trace（playwright show-trace %s 可回放）：%s" % (path.name, path),
                    echo=True)
    except Exception as ex:
        engine.logf("保存 trace 失败：%s" % ex)


def run_workflow(path: str | Path | None = None, workflow: dict | None = None,
                 base_dir: str | Path | None = None, headless: bool | None = None,
                 channel: str | None = None, headed: bool = False, logf=log,
                 dry_run: bool = False, resume: bool = False,
                 retry_failed: bool = False) -> dict:
    """执行工作流并返回汇总。

    path          工作流文件路径（workflow 参数优先）
    workflow      已解析的工作流 dict（可直接从代码调用）
    base_dir      相对路径锚定目录；默认取工作流文件所在目录，否则当前目录
    headless      覆盖 browser.headless
    channel       覆盖 browser.channel（默认 chrome）
    headed        强制显示浏览器窗口
    dry_run       只执行读动作，写动作跳过（安全演练）
    resume        断点续跑：跳过进度文件里已完成的任务
    retry_failed  只处理失败清单里的任务
    """
    ensure_console_logging()
    if workflow is None:
        p = Path(path or "workflow.yaml")
        if not p.is_absolute():
            p = Path.cwd() / p
        workflow = load_workflow(p)
        if base_dir is None:
            base_dir = p.parent
    if not isinstance(workflow, dict):
        raise ConfigError("工作流顶层应是一个映射（含 name / steps / tasks 等字段）")
    if base_dir is not None:
        set_base_dir(base_dir)
    workflow = expand_partials(workflow, get_base_dir())
    if not workflow.get("steps") and not workflow.get("tasks"):
        raise ConfigError("工作流既没有 steps 也没有 tasks，无事可做")

    br = workflow.get("browser") or {}
    if channel is None:
        channel = br.get("channel") or "chrome"
    if headless is None:
        headless = bool(br.get("headless", False))
    if headed:
        headless = False

    engine = WorkflowEngine(workflow, logf=logf, dry_run=dry_run,
                            resume=resume, retry_failed=retry_failed)
    # 进度/失败清单文件锚定 base_dir；path 缺省时用统一名 playflow.progress.json
    stem = Path(path).stem if path else "playflow"
    engine.set_checkpoint_paths(stem)
    if resume:
        engine.load_progress()
    if retry_failed:
        engine.load_failed()
    if dry_run:
        engine.settings["task_delay_seconds"] = [0, 0]
        log("dry-run 模式：只执行读动作，写动作（点击/填写/上传等）会跳过。", echo=True)

    started_at = datetime.datetime.now()
    status = "completed"
    log("工作流：%s" % (workflow.get("name") or "(未命名)"), echo=True)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser = launch(pw, channel=channel, headless=headless)
            try:
                ctx, page = login(engine, browser)
                engine.ctx, engine.current = ctx, page
                trace_mode = _normalize_trace_mode(engine.settings.get("trace"))
                tracing_on = False
                if trace_mode in ("on_error", "always"):
                    try:
                        ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
                        tracing_on = True
                    except Exception as ex:
                        engine.logf("trace 启动失败（忽略）：%s" % ex)
                body_err = None
                try:
                    if workflow.get("steps"):
                        engine.logf("执行主流程 steps（%d 步）" % len(workflow["steps"]),
                                    echo=True)
                        engine.run_steps(workflow["steps"])
                    for task in (workflow.get("tasks") or []):
                        run_task(engine, task)
                except BaseException as ex:
                    body_err = ex
                    status = "aborted"
                    if tracing_on:
                        _save_trace(ctx, engine, "中止")
                    raise
                finally:
                    if tracing_on and body_err is None and trace_mode == "always":
                        _save_trace(ctx, engine, "完成")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    finally:
        engine.save_checkpoint()
        duration = (datetime.datetime.now() - started_at).total_seconds()
        _after_run(engine, status, duration, started_at)
    print_summary(engine.summary)
    return engine.summary


def _after_run(engine, status, duration, started_at):
    """收尾：写运行报告 + 发送通知（失败不影响主流程结果）。"""
    try:
        from .report import write_reports
        report = write_reports(engine, status, duration, started_at)
    except Exception as ex:
        log("生成运行报告失败：%s" % ex)
        report = None
    try:
        cfg = render(engine.wf.get("notify") or {}, engine.vars) or {}
        if isinstance(cfg, dict) and (cfg.get("webhook") or cfg.get("webhooks")):
            from .notify import notify_run_result
            counts = engine.total_counts()
            has_failure = status != "completed" or counts["失败"] > 0
            if has_failure or not as_bool(cfg.get("only_on_failure", False)):
                notify_run_result(cfg, engine, status, duration, report)
    except Exception as ex:
        log("发送通知失败：%s" % ex)


def print_summary(summary):
    lines = ["", "=" * 62, "处理汇总", "=" * 62]
    all_failed = []
    for name, st in summary.items():
        line = "%s：成功 %d，失败 %d，跳过 %d" % (
            name, len(st["成功"]), len(st["失败"]), len(st["跳过"]))
        if st.get("错误"):
            line += "（任务错误：%s）" % st["错误"]
        lines.append("  " + line)
        if st["失败"]:
            lines.append("    失败：%s" % "、".join(str(x) for x in st["失败"]))
            all_failed += st["失败"]
        if st["跳过"]:
            lines.append("    跳过：%s" % "、".join(str(x) for x in st["跳过"]))
    if all_failed:
        lines.append("请结合 shots/ 截图与 logs/ 日志排查失败项；"
                     "可用 playflow run --retry-failed 只重跑失败任务。")
    lines.append("日志目录：%s" % (base_dir() / "logs"))
    lines.append("截图目录：%s" % (base_dir() / "shots"))
    lines.append("=" * 62)
    log("\n".join(lines), echo=True)


# ---------------------------------------------------------------- 校验

PARAM_HINTS = {
    "goto": ["url"],
    "wait_for_url": ["pattern", "url"],
    "click": ["selector"],
    "fill": ["selector", "placeholder", "name", "id"],
    "check": ["selector"],
    "pick_radio": ["text", "selector", "value"],
    "select_option": ["selector"],
    "press": ["key"],
    "wait_for": ["selector"],
    "upload": ["selector", "file"],
    "extract": ["selector"],
    "evaluate": ["js", "code", "script"],
    "request": ["url"],
    "expect_text": ["text"],
    "expect_visible": ["selector"],
    "expect_url": ["pattern", "url"],
    "assert": ["condition"],
    "if": ["condition"],
    "set_var": ["name", "var"],
    "write_file": ["path", "file"],
    "for_each": ["over", "list", "rows", "range"],
    "while": ["condition"],
    "repeat": ["times"],
}


def validate_workflow(source: object, base: str | Path | None = None) -> tuple[list[str], list[str]]:
    """校验工作流（dict 或文件路径），返回 (errors, warnings)。"""
    wf = source if isinstance(source, dict) else load_workflow(source)
    if base is None and not isinstance(source, dict) and source:
        base = Path(source).parent
    base = Path(base) if base else base_dir()
    errs, warns = [], []
    if not isinstance(wf, dict):
        return ["工作流顶层应为映射（含 name / steps / tasks）"], []

    if not wf.get("steps") and not wf.get("tasks"):
        errs.append("既没有 steps 也没有 tasks，工作流不会执行任何东西")
    if "browser" in wf and not isinstance(wf["browser"], dict):
        errs.append("browser 应为映射（channel / headless）")

    def walk_steps(steps, where):
        if steps is None:
            return
        if not isinstance(steps, list):
            errs.append("%s 应为步骤列表" % where)
            return
        for i, st in enumerate(steps, 1):
            tag = "%s 第 %d 步" % (where, i)
            if not isinstance(st, dict):
                errs.append("%s 不是映射" % tag)
                continue
            if "include" in st:
                inc = st["include"]
                if "uses" in st:
                    errs.append("%s 同时包含 include 与 uses，只能二选一" % tag)
                elif inc not in partials:
                    errs.append("%s include 引用了未定义的 partial：%r（已定义：%s）"
                                % (tag, inc, "、".join(str(x) for x in partials)))
                for key in ("then", "else", "do", "steps"):
                    if key in st:
                        errs.append("%s include 步骤不应包含 %s 字段" % (tag, key))
                continue
            uses = st.get("uses")
            if not uses:
                errs.append("%s 缺少 uses" % tag)
                continue
            if uses not in ACTIONS:
                errs.append("%s 未知动作 uses=%r（可用：%s）"
                            % (tag, uses, "、".join(sorted(ACTIONS))))
            else:
                hints = PARAM_HINTS.get(uses)
                w = st.get("with") or {}
                if hints and isinstance(w, dict) and not any(k in w for k in hints):
                    warns.append("%s（%s）缺少参数：需要 %s 之一"
                                 % (tag, uses, " / ".join(hints)))
            for key in ("then", "else", "do", "steps"):
                if key in st:
                    if isinstance(st[key], list):
                        walk_steps(st[key], "%s 的 %s" % (tag, key))
                    elif st[key] is not None:
                        errs.append("%s 的 %s 应为步骤列表" % (tag, key))

    partials = wf.get("partials") or {}
    if not isinstance(partials, dict):
        errs.append("partials 应为映射（名称 → 步骤列表 或 {file: 路径}）")
        partials = {}
    else:
        for pname, pdef in partials.items():
            try:
                walk_steps(_load_partial_steps(pdef, pname, base),
                           "partials[%s]" % pname)
            except ConfigError as ex:
                errs.append(str(ex))

    walk_steps(wf.get("steps"), "steps")
    login = wf.get("login")
    if login is not None and not isinstance(login, dict):
        errs.append("login 应为映射（url / username / steps 等）")
    elif isinstance(login, dict) and login.get("steps"):
        walk_steps(login["steps"], "login.steps")
    for i, task in enumerate(wf.get("tasks") or [], 1):
        if not isinstance(task, dict) or not task.get("name"):
            errs.append("tasks 第 %d 项需要 name" % i)
            continue
        tname = task.get("name")
        has_csv, has_xlsx = bool(task.get("from_csv")), bool(task.get("from_xlsx"))
        if has_csv and has_xlsx:
            errs.append("tasks[%s] 的 from_csv 与 from_xlsx 只能二选一" % tname)
        if has_csv or has_xlsx:
            if task.get("mode"):
                warns.append("tasks[%s] 是数据源任务，mode 字段不会生效" % tname)
            f = Path(str(task.get("from_csv") or task.get("from_xlsx")))
            if not f.is_absolute():
                f = base / f
            if not f.exists():
                warns.append("tasks[%s] 数据文件不存在：%s" % (tname, f))
            if not task.get("steps"):
                errs.append("tasks[%s] 数据源任务需要 steps" % tname)
        else:
            mode = str(task.get("mode") or "first_row").lower()
            if mode not in TASK_MODES:
                errs.append("tasks[%s].mode 只能是 once / first_row / each_row（当前 %r）"
                            % (tname, mode))
            elif mode != "once" and not task.get("url"):
                warns.append("tasks[%s]（%s）未配置 url，将在当前页面查找列表行"
                             % (tname, mode))
        walk_steps(task.get("steps"), "tasks[%s].steps" % tname)
    return errs, warns


def collect_actions(workflow):
    """收集工作流里用到的全部动作步骤（校验输出用）。"""
    out = []

    def walk(steps):
        for st in (steps or []):
            if isinstance(st, dict):
                out.append(st)
                for k in ("then", "else", "do", "steps"):
                    walk(st.get(k))

    walk(workflow.get("steps"))
    walk((workflow.get("login") or {}).get("steps"))
    for task in (workflow.get("tasks") or []):
        walk((task or {}).get("steps"))
    return out
