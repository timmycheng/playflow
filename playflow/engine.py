# -*- coding: utf-8 -*-
"""工作流引擎：加载 YAML、渲染变量、执行步骤与任务循环。"""
import random
import re
import time
import traceback
from pathlib import Path

import yaml

from .browser import is_login_page, launch, login, perform_login, safe_goto
from .conditions import eval_condition
from .dom import find_row, norm_text
from .errors import AbortError, ConfigError, LoopBreak, LoopContinue, StepError
from .registry import ACTIONS
from .template import as_bool, render
from .utils import base_dir, log, set_base_dir, shot
from .actions import ensure_registered as _ensure_actions

_ensure_actions()   # 导入即注册全部内置动作

DEFAULT_SETTINGS = {
    "screenshot": True,
    "max_tasks": 1,
    "task_delay_seconds": [1, 3],
    "attachment_dir": "附件",
    "dialog": "accept",
    "dialog_text": "",
    "timeout": 20000,
}

TASK_MODES = ("once", "first_row", "each_row")


class WorkflowEngine:
    """执行工作流：持有浏览器上下文、当前页面、变量与统计。"""

    def __init__(self, workflow, logf=log):
        self.wf = workflow or {}
        self.settings = {**DEFAULT_SETTINGS, **(self.wf.get("settings") or {})}
        self.logf = logf
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


# ---------------------------------------------------------------- 任务循环

def _row_label(task, text, done):
    regex = task.get("label_regex")
    if regex:
        m = re.search(str(regex), text or "")
        if m:
            return m.group(int(task.get("label_group", 0)))
    label = " ".join(str(text or "").split())[:40]
    return label or "row%d" % (done + 1)


def _close_opened(engine):
    for pg in list(engine.opened_pages):
        try:
            pg.close()
        except Exception:
            pass
    engine.opened_pages = []


def _run_one_task(engine, steps, label, stat, snap_stage=None):
    """执行一个任务单元的 steps；失败记入 stat，不让整个任务中止。"""
    engine.opened_pages = []
    if engine.list_page is not None:
        engine.current = engine.list_page
    ok = False
    try:
        if snap_stage:
            engine.snap(snap_stage)
        engine.run_steps(steps)
        ok = True
        engine.logf("任务 %s 处理成功" % label, echo=True)
    except (ConfigError, AbortError):
        raise
    except StepError as ex:
        engine.logf("任务 %s 失败：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    except Exception as ex:
        engine.logf("任务 %s 异常：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    finally:
        _close_opened(engine)
        if engine.list_page is not None:
            engine.current = engine.list_page
    (stat["成功"] if ok else stat["失败"]).append(label)
    return ok


def _finish_task_log(engine, name, stat):
    engine.logf("========== 任务 %s 结束：成功 %d，失败 %d，跳过 %d =========="
                % (name, len(stat["成功"]), len(stat["失败"]), len(stat["跳过"])),
                echo=True)


def run_task(engine, task):
    """执行一个 tasks 定义。mode：
      once       只执行一次（不找列表行；适合单页/接口流程）
      first_row  队列循环：反复取第一行 → steps → 回列表（默认）
      each_row   表格循环：remove_after=true 始终取第一行；false 按行号依次取
    """
    name = str(task.get("name") or "未命名任务")
    mode = str(task.get("mode") or "first_row").lower()
    if mode not in TASK_MODES:
        raise ConfigError("tasks[%s].mode 只能是 once / first_row / each_row" % name)
    steps = task.get("steps") or []
    stat = engine.summary.setdefault(name, {"成功": [], "失败": [], "跳过": [], "错误": ""})
    max_tasks = int(task.get("max_tasks", engine.settings["max_tasks"]))
    remove_after = as_bool(task.get("remove_after", True))
    engine.vars["category"] = name
    engine.vars["category_url"] = task.get("url", "")
    engine.logf("========== 任务：%s（%s，mode=%s）=========="
                % (name, task.get("url", ""), mode), echo=True)

    list_page = engine.current
    engine.list_page = list_page
    if task.get("url"):
        try:
            resp = safe_goto(list_page, task["url"], "列表页")
        except AbortError as ex:
            stat["错误"] = str(ex).split("\n")[0]
            engine.logf("%s：%s" % (name, stat["错误"]), echo=True)
            engine.list_page = None
            return
        if resp is not None and getattr(resp, "status", 0) >= 400:
            stat["错误"] = "列表页返回 HTTP %d" % resp.status
            engine.logf("%s：%s" % (name, stat["错误"]), echo=True)
            engine.list_page = None
            return

    if mode == "once":
        engine.row = None
        engine.vars.update({"label": "", "task_no": "", "row_text": "", "row_index": 0})
        _run_one_task(engine, steps, name, stat)
        _finish_task_log(engine, name, stat)
        engine.list_page = None
        return

    seen, done = set(), 0
    while done < max_tasks:
        if is_login_page(list_page):
            engine.logf("检测到登录态失效，重新登录（可能需要人工验证）…", echo=True)
            perform_login(engine, list_page)
            if task.get("url"):
                safe_goto(list_page, task["url"], "列表页")

        row_index = 0 if remove_after else done
        info = find_row(list_page, task.get("row_selector", ""),
                        task.get("link_selector", ""), index=row_index)
        if not info:
            engine.logf("任务 %s：列表已空，处理结束。" % name, echo=True)
            break

        label = _row_label(task, info["text"], done)
        if remove_after and label in seen:
            stat["跳过"].append(label)
            engine.logf("行 %s 反复无法完成，终止该任务以免死循环。" % label, echo=True)
            break
        seen.add(label)
        engine.vars.update({"label": label, "task_no": label, "row_text": info["text"],
                            "row_index": row_index, "index": done})
        engine.row = dict(info)
        engine.row["task_no"] = label

        _run_one_task(engine, steps, label, stat, snap_stage="%s_列表行" % label)
        done += 1

        if task.get("url"):
            try:
                safe_goto(list_page, task["url"], "列表页")
            except AbortError as ex:
                stat["错误"] = str(ex).split("\n")[0]
                engine.logf("返回列表页失败：%s" % stat["错误"], echo=True)
                break
        try:
            lo, hi = engine.settings["task_delay_seconds"]
        except Exception:
            lo, hi = 1, 3
        delay = random.uniform(float(lo), float(hi))
        engine.logf("随机等待 %.1f 秒…" % delay)
        try:
            list_page.wait_for_timeout(int(delay * 1000))
        except Exception:
            time.sleep(delay)

    _finish_task_log(engine, name, stat)
    engine.list_page = None


# ---------------------------------------------------------------- 运行入口

def load_workflow(path):
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


def run_workflow(path=None, workflow=None, base_dir=None, headless=None,
                 channel=None, headed=False, logf=log):
    """执行工作流并返回汇总。

    path       工作流文件路径（workflow 参数优先）
    workflow   已解析的工作流 dict（可直接从代码调用）
    base_dir   相对路径锚定目录；默认取工作流文件所在目录，否则当前目录
    headless   覆盖 browser.headless
    channel    覆盖 browser.channel（默认 chrome）
    headed     强制显示浏览器窗口
    """
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
    if not workflow.get("steps") and not workflow.get("tasks"):
        raise ConfigError("工作流既没有 steps 也没有 tasks，无事可做")

    br = workflow.get("browser") or {}
    if channel is None:
        channel = br.get("channel") or "chrome"
    if headless is None:
        headless = bool(br.get("headless", False))
    if headed:
        headless = False

    engine = WorkflowEngine(workflow, logf=logf)
    print("工作流：%s" % (workflow.get("name") or "(未命名)"))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = launch(pw, channel=channel, headless=headless)
        try:
            ctx, page = login(engine, browser)
            engine.ctx, engine.current = ctx, page
            if workflow.get("steps"):
                engine.logf("执行主流程 steps（%d 步）" % len(workflow["steps"]), echo=True)
                engine.run_steps(workflow["steps"])
            for task in (workflow.get("tasks") or []):
                run_task(engine, task)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    print_summary(engine.summary)
    return engine.summary


def print_summary(summary):
    print()
    print("=" * 62)
    print("处理汇总")
    print("=" * 62)
    all_failed = []
    for name, st in summary.items():
        line = "%s：成功 %d，失败 %d，跳过 %d" % (
            name, len(st["成功"]), len(st["失败"]), len(st["跳过"]))
        if st.get("错误"):
            line += "（任务错误：%s）" % st["错误"]
        print("  " + line)
        if st["失败"]:
            print("    失败：%s" % "、".join(str(x) for x in st["失败"]))
            all_failed += st["失败"]
        if st["跳过"]:
            print("    跳过：%s" % "、".join(str(x) for x in st["跳过"]))
    if all_failed:
        print("请结合 shots/ 截图与 logs/ 日志排查失败项。")
    print("日志目录：%s" % (base_dir() / "logs"))
    print("截图目录：%s" % (base_dir() / "shots"))
    print("=" * 62)


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


def validate_workflow(source):
    """校验工作流（dict 或文件路径），返回 (errors, warnings)。"""
    wf = source if isinstance(source, dict) else load_workflow(source)
    errs, warns = [], []
    if not isinstance(wf, dict):
        return ["工作流顶层应为映射（含 name / steps / tasks）"], []

    if not wf.get("steps") and not wf.get("tasks"):
        errs.append("既没有 steps 也没有 tasks，工作流不会执行任何东西")
    if "browser" in wf and not isinstance(wf["browser"], dict):
        errs.append("browser 应为映射（channel / headless）")

    def walk(steps, where):
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
                        walk(st[key], "%s 的 %s" % (tag, key))
                    elif st[key] is not None:
                        errs.append("%s 的 %s 应为步骤列表" % (tag, key))

    walk(wf.get("steps"), "steps")
    login = wf.get("login")
    if login is not None and not isinstance(login, dict):
        errs.append("login 应为映射（url / username / steps 等）")
    elif isinstance(login, dict) and login.get("steps"):
        walk(login["steps"], "login.steps")
    for i, task in enumerate(wf.get("tasks") or [], 1):
        if not isinstance(task, dict) or not task.get("name"):
            errs.append("tasks 第 %d 项需要 name" % i)
            continue
        mode = str(task.get("mode") or "first_row").lower()
        if mode not in TASK_MODES:
            errs.append("tasks[%s].mode 只能是 once / first_row / each_row（当前 %r）"
                        % (task.get("name"), mode))
        elif mode != "once" and not task.get("url"):
            warns.append("tasks[%s]（%s）未配置 url，将在当前页面查找列表行"
                         % (task.get("name"), mode))
        walk(task.get("steps"), "tasks[%s].steps" % task.get("name"))
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
