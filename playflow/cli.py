# -*- coding: utf-8 -*-
"""命令行入口：playflow run / validate / probe / record / heal；无子命令时显示菜单。"""
import argparse
import sys
import traceback
from pathlib import Path

from . import __version__
from .engine import WorkflowEngine, collect_actions, load_workflow, run_workflow, validate_workflow
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
  5. heal      检查步骤可达性，生成修复建议
  0. 退出
----------------------------------------------
  命令行用法：
    playflow run 流程.yaml [--headed] [--headless] [--channel chrome]
                           [--dry-run] [--resume] [--retry-failed]
    playflow validate 流程.yaml
    playflow probe [--workflow 流程.yaml] [--url 网址]
    playflow record out.yaml [--workflow 流程.yaml] [--url 网址] [--verify]
    playflow heal 流程.yaml [--url 网址] [--fix]
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
    p_run.add_argument("--dry-run", action="store_true",
                       help="只执行读动作（导航/取值/断言），写动作跳过")
    p_run.add_argument("--resume", action="store_true",
                       help="断点续跑：跳过进度文件里已完成的任务")
    p_run.add_argument("--retry-failed", action="store_true",
                       help="只补跑上次失败清单里的任务")

    p_val = sub.add_parser("validate", help="校验工作流")
    p_val.add_argument("file", nargs="?", default="workflow.yaml", help="工作流 YAML 文件")

    p_probe = sub.add_parser("probe", help="交互式页面探测")
    p_probe.add_argument("--workflow", "-w", default=None, help="复用其 login 配置的工作流")
    p_probe.add_argument("--url", "-u", default=None, help="登录后先打开该网址")

    p_rec = sub.add_parser("record", help="录制操作生成 YAML")
    p_rec.add_argument("out", help="输出的工作流 YAML 路径")
    p_rec.add_argument("--workflow", "-w", default=None, help="复用其 login 配置的工作流")
    p_rec.add_argument("--url", "-u", default=None, help="录制前先打开该网址")
    p_rec.add_argument("--verify", action="store_true",
                       help="录制完成后逐步回放验证（会再次真实执行操作）")

    p_heal = sub.add_parser("heal", help="检查步骤在页面上的可达性并给出修复建议")
    p_heal.add_argument("file", nargs="?", default="workflow.yaml", help="工作流 YAML 文件")
    p_heal.add_argument("--url", "-u", default=None,
                        help="登录后先打开该网址（默认取当前页面 / 第一个任务的 url）")
    p_heal.add_argument("--fix", action="store_true",
                        help="把高于阈值的建议写成 <名字>.healed.yaml（不改原文件）")
    p_heal.add_argument("--min-score", type=float, default=0.55,
                        help="自动修复的相似度阈值，默认 0.55")
    p_heal.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    return parser


def _abs(path):
    p = Path(path)
    return p if p.is_absolute() else Path.cwd() / p


def _summary_exit_code(summary):
    """任务失败/任务级错误 → 退出码 1，便于脚本与 CI 感知结果。"""
    failed = sum(len(st.get("失败") or []) for st in (summary or {}).values())
    errored = sum(1 for st in (summary or {}).values() if st.get("错误"))
    if failed or errored:
        print("\n运行结束：失败 %d 个任务，%d 个任务出错（退出码 1）。" % (failed, errored))
        return 1
    return 0


def cmd_run(args):
    if args.validate:
        return cmd_validate(args)
    headless = True if args.headless else (False if args.headed else None)
    summary = run_workflow(path=args.file, headless=headless, channel=args.channel,
                           headed=args.headed, dry_run=args.dry_run, resume=args.resume,
                           retry_failed=args.retry_failed)
    return _summary_exit_code(summary)


def cmd_validate(args):
    p = _abs(args.file)
    print("校验工作流：%s" % p)
    wf = load_workflow(p)
    errs, warns = validate_workflow(wf, base=p.parent)
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
    rec_with = {"file": args.out}
    if args.verify:
        rec_with["verify"] = True
    steps.append({"uses": "record", "with": rec_with})
    wf["steps"] = steps
    run_workflow(workflow=wf, headed=True)
    return 0


def cmd_heal(args):
    from playwright.sync_api import sync_playwright

    from .browser import launch, login
    from .heal import run_heal

    p = _abs(args.file)
    if not p.exists():
        raise ConfigError("未找到工作流文件：%s" % p)
    wf = load_workflow(p)
    set_base_dir(p.parent)
    wf = dict(wf)
    wf["settings"] = {**(wf.get("settings") or {}), "screenshot": False}
    wf["browser"] = {**(wf.get("browser") or {})}
    if args.headed:
        wf["browser"]["headless"] = False
    engine = WorkflowEngine(wf)
    br = wf.get("browser") or {}
    with sync_playwright() as pw:
        browser = launch(pw, channel=br.get("channel") or "chrome",
                         headless=bool(br.get("headless", False)))
        try:
            ctx, page = login(engine, browser)
            engine.ctx, engine.current = ctx, page
            run_heal(engine, wf, url=args.url, fix=args.fix, min_score=args.min_score,
                     out_path=p.with_name(p.stem + ".healed.yaml"))
        finally:
            browser.close()
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
                cmd_record(argparse.Namespace(workflow=None, url=None, out=out,
                                              verify=False))
            elif choice == "4":
                cmd_validate(argparse.Namespace(file="workflow.yaml"))
            elif choice == "5":
                cmd_heal(argparse.Namespace(file="workflow.yaml", url=None, fix=False,
                                            min_score=0.55, headed=True))
            elif choice == "0":
                break
            elif choice == "":
                continue
            else:
                print("无效选择：%r，请输入 0-5。" % choice)
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
        if args.cmd == "heal":
            return cmd_heal(args)
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
