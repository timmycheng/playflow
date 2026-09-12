# -*- coding: utf-8 -*-
"""CLI 退出码与库控制台日志的单元测试（无浏览器）。"""
import argparse

from playflow import cli, utils


def test_summary_exit_code():
    ok = {"类别一": {"成功": ["A"], "失败": [], "跳过": [], "错误": ""}}
    assert cli._summary_exit_code(ok) == 0

    fail = {"类别一": {"成功": [], "失败": ["A"], "跳过": [], "错误": ""}}
    assert cli._summary_exit_code(fail) == 1

    err = {"类别一": {"成功": [], "失败": [], "跳过": [], "错误": "HTTP 500"}}
    assert cli._summary_exit_code(err) == 1


def test_cmd_run_returns_nonzero_on_failure(monkeypatch):
    summary = {"t": {"成功": [], "失败": ["x"], "跳过": [], "错误": ""}}
    monkeypatch.setattr(cli, "run_workflow", lambda **kw: summary)
    args = argparse.Namespace(validate=False, headless=False, headed=False, channel=None,
                              file="x.yaml", dry_run=False, resume=False, retry_failed=False)
    assert cli.cmd_run(args) == 1


def test_cmd_run_returns_zero_on_success(monkeypatch):
    summary = {"t": {"成功": ["x"], "失败": [], "跳过": [], "错误": ""}}
    monkeypatch.setattr(cli, "run_workflow", lambda **kw: summary)
    args = argparse.Namespace(validate=False, headless=False, headed=False, channel=None,
                              file="x.yaml", dry_run=False, resume=False, retry_failed=False)
    assert cli.cmd_run(args) == 0


def test_console_logging_attached_and_disable(monkeypatch):
    monkeypatch.setattr(utils, "_CONSOLE_HANDLER", None)
    monkeypatch.setattr(utils, "_CONSOLE_DISABLED", False)
    utils.ensure_console_logging()
    assert utils._CONSOLE_HANDLER is not None
    utils.disable_console_logging()
    assert utils._CONSOLE_HANDLER is None
    # 显式 disable 后，run_workflow 的自动挂载不会把 handler 加回来
    utils.ensure_console_logging()
    assert utils._CONSOLE_HANDLER is None
