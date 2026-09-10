# -*- coding: utf-8 -*-
"""命令行入口：playflow run / validate / probe / record；无子命令时显示菜单。"""
import argparse
import sys
import traceback
from pathlib import Path

from . import __version__
from .engine import WorkflowEngine, collect_actions, load_workflow, run_workflow, \
    validate_workflow
from .errors import AbortError, ConfigError
from .utils import init_stdio, log, set_base_dir

MENU = """
==============================================
   playflow —— YAML 驱动的 Playwright 自动化
==============================================
  1. run       执行 workflow.yaml
  2. probe     页面探测（生成选择器建议 / YAML 草稿）
  3. record    操作录制（在浏览器里操作，生成 YAML 步骤）
  4. validate  校验 workflow.yaml
  0. 退出
----------------------------------------------
  命令行用法：
    playflow run 流程.yaml [--headed] [--headless] [--channel chrome]
    playflow validate 流程.yaml
    playflow probe [--workflow 流程.yaml] [--url 网址]
    playflow record out.yaml [--workflow 流程.yaml] [--url 网址]
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="playflow", description="YAML 驱动的 Playwright 自动化引擎",
        epilog="无子命令时进入交互菜单。")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="执行工作流")
    p_run.add_argument("file", nargs="?", default="workflow.yaml", help="工作流 YAML 文件")
    p_run.add_argument("--validate", action="store_true", help="只校验，不执行")
    p_run.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    p_run.add_argument("--headless", action="store_true", help="无头模式")
    p_run.add_argument("--channel", default=None, help="浏览器渠道，默认 chrome")

    p_val = sub.add_parser("validate", help="校验工作流")
    p_val.add_argument("file", nargs="?", default="workflow.yaml", help="工作流 YAML 文件")

    p_probe = sub.add_parser("probe", help="交互式页面探测")
    p_probe.add_argument("--workflow", "-w", default=None, help="复用其 login 配置的工作流")
    p_probe.add_argument("--url", "-u", default=None, help="登录后先打开该网址")

    p_rec = sub.add_parser("record", help="录制操作生成 YAML")
    p_rec.add_argument("out", help="输出的工作流 YAML 路径")
    p_rec.add_argument("--workflow", "-w", default=None, help="复用其 login 配置的工作流")
    p_rec.add_argument("--url", "-u", default=None, help="录制前先打开该网址")
    return parser


def _abs(path):
    p = Path(path)
    return p if p.is_absolute() else Path.cwd() / p


def cmd_run(args):
    if args.validate:
        return cmd_validate(args)
    headless = True if args.headless else (False if args.headed else None)
    run_workflow(path=args.file, headless=headless, channel=args.channel, headed=args.headed)
    return 0


def cmd_validate(args):
    p = _abs(args.file)
    print("校验工作流：%s" % p)
    wf = load_workflow(p)
    errs, warns = validate_workflow(wf)
    for e in errs:
        print("  [X] %s" % e)
    for w in warns:
        print("  ! %s" % w)
    if errs:
        print("\n发现 %d 个错误。" % len(errs))
        return 2
    acts = sorted({st.get("uses") for st in collect_actions(wf) if st.get("uses")})
    print("\n[OK] 校验通过。")
    if acts:
        print("  动作：%s" % "、".join(acts))
    tasks = wf.get("tasks") or []
    if tasks:
        print("  任务：%s" % "、".join(str(t.get("name")) for t in tasks))
    return 0


def _prepare_browser_workflow(path):
    """probe/record 共用的准备：读工作流、强制可见窗口、关闭截图。"""
    wf = {}
    if path:
        p = _abs(path)
        if p.exists():
            wf = load_workflow(p)
            set_base_dir(p.parent)
    if not isinstance(wf, dict):
        wf = {}
    wf = dict(wf)
    wf["settings"] = {**(wf.get("settings") or {}), "screenshot": False}
    wf["browser"] = {**(wf.get("browser") or {}), "headless": False}
    wf.pop("steps", None)
    wf.pop("tasks", None)
    return wf


def cmd_probe(args):
    from playwright.sync_api import sync_playwright

    from .browser import launch, login, safe_goto
    from .probe import interactive_probe

    wf = _prepare_browser_workflow(args.workflow)
    engine = WorkflowEngine(wf)
    br = wf.get("browser") or {}
    with sync_playwright() as pw:
        browser = launch(pw, channel=br.get("channel") or "chrome", headless=False)
        try:
            ctx, page = login(engine, browser)
            engine.ctx, engine.current = ctx, page
            if args.url:
                safe_goto(page, args.url, "页面")
            interactive_probe(engine)
        finally:
            browser.close()
    return 0


def cmd_record(args):
    wf = _prepare_browser_workflow(args.workflow)
    steps = []
    if args.url:
        steps.append({"uses": "goto", "with": {"url": args.url}})
    steps.append({"uses": "record", "with": {"file": args.out}})
    wf["steps"] = steps
    run_workflow(workflow=wf, headed=True)
    return 0


def cmd_menu():
    print(MENU)
    while True:
        try:
            choice = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        try:
            if choice == "1":
                run_workflow(path="workflow.yaml")
            elif choice == "2":
                cmd_probe(argparse.Namespace(workflow=None, url=None))
            elif choice == "3":
                out = input("录制输出文件名（默认 shots/record.yaml）: ").strip() \
                    or "shots/record.yaml"
                cmd_record(argparse.Namespace(workflow=None, url=None, out=out))
            elif choice == "4":
                cmd_validate(argparse.Namespace(file="workflow.yaml"))
            elif choice == "0":
                break
            elif choice == "":
                continue
            else:
                print("无效选择：%r，请输入 0-4。" % choice)
                continue
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
        except AbortError as e:
            print("\n【致命错误】%s\n" % e)
        except KeyboardInterrupt:
            print("\n已取消当前操作。")
        except Exception as e:
            log("未预期异常：%s\n%s" % (e, traceback.format_exc()))
            print("\n【未预期错误】%s" % str(e).split("\n")[0])
            print("详细堆栈已写入日志。")
        print(MENU)
    print("再见。")
    return 0


def main(argv=None):
    init_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd == "probe":
            return cmd_probe(args)
        if args.cmd == "record":
            return cmd_record(args)
        return cmd_menu()
    except ConfigError as e:
        print("\n【配置错误】%s\n" % e)
        return 2
    except AbortError as e:
        print("\n【致命错误】%s\n" % e)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as e:
        log("未预期异常：%s\n%s" % (e, traceback.format_exc()))
        print("\n【未预期错误】%s" % str(e).split("\n")[0])
        print("详细堆栈已写入日志。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
