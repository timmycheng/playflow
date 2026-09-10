# -*- coding: utf-8 -*-
"""registry 读/写标注 + dry-run 判定 + 断点续跑记录的单元测试（无浏览器）。"""
import json

from playflow import ACTIONS
from playflow.engine import WorkflowEngine
from playflow.registry import action_mode


def test_actions_registered():
    assert len(ACTIONS) >= 44
    for name, fn in ACTIONS.items():
        assert getattr(fn, "action_name", None) == name
        assert getattr(fn, "action_mode", None) in ("read", "write")


def test_read_write_classification():
    for name in ("goto", "extract", "evaluate", "wait", "assert", "download",
                 "if", "for_each", "log", "set_var"):
        assert action_mode(name) == "read", name
    for name in ("click", "click_text", "fill", "upload", "write_file",
                 "pick_radio", "select_option", "pause", "record"):
        assert action_mode(name) == "write", name


def test_unknown_action_defaults_to_write():
    assert action_mode("不存在") == "write"


def test_dry_run_skip_decisions():
    e = WorkflowEngine({"name": "t"}, dry_run=True)
    assert e._dry_run_skip("click", {})
    assert e._dry_run_skip("fill", {})
    assert not e._dry_run_skip("goto", {"url": "http://x"})
    assert not e._dry_run_skip("extract", {"selector": "#a"})
    # request 按方法细分：GET 执行，POST 跳过
    assert not e._dry_run_skip("request", {"method": "GET"})
    assert not e._dry_run_skip("request", {"url": "x"})   # 缺省 GET
    assert e._dry_run_skip("request", {"method": "POST"})
    # 开关本身挂在 run_step 上：非 dry-run 引擎 dry_run 标志为 False
    e2 = WorkflowEngine({"name": "t"})
    assert e2.dry_run is False


def test_checkpoint_roundtrip(tmp_path):
    stem = str(tmp_path / "demo")
    e = WorkflowEngine({"name": "演示流程"})
    e.set_checkpoint_paths(stem)
    e.record_task_result("类别一", "T101", True)
    e.record_task_result("类别一", "T102", False, "找不到按钮")
    e.record_task_result("类别二", "T201", True)

    prog = json.loads((tmp_path / "demo.progress.json").read_text(encoding="utf-8"))
    assert prog["workflow"] == "演示流程"
    assert prog["done"]["类别一"] == ["T101"]
    failed = json.loads((tmp_path / "demo.failed.json").read_text(encoding="utf-8"))
    assert failed["failed"]["类别一"]["T102"] == "找不到按钮"

    # 断点续跑：加载进度后 should_skip 命中
    e2 = WorkflowEngine({"name": "演示流程"}, resume=True)
    e2.set_checkpoint_paths(stem)
    e2.load_progress()
    assert e2.should_skip("类别一", "T101")
    assert not e2.should_skip("类别一", "T999")

    # 失败重试：仅处理清单里的标签
    e3 = WorkflowEngine({"name": "演示流程"}, retry_failed=True)
    e3.set_checkpoint_paths(stem)
    e3.load_failed()
    assert not e3.should_skip("类别一", "T102")     # 失败过的要重跑
    assert e3.should_skip("类别一", "T101")         # 成功过的不跑
    assert e3.should_skip("类别二", "T999")         # 类别二没有失败项，全跳过


def test_retry_success_clears_failed(tmp_path):
    stem = str(tmp_path / "retry")
    e = WorkflowEngine({"name": "t"})
    e.set_checkpoint_paths(stem)
    e.record_task_result("t", "A", False, "err")
    e.record_task_result("t", "A", True)
    failed = json.loads((tmp_path / "retry.failed.json").read_text(encoding="utf-8"))
    assert failed["failed"].get("t", {}).get("A") is None


def test_record_task_without_paths_is_noop():
    e = WorkflowEngine({"name": "t"})   # 未 set_checkpoint_paths
    e.record_task_result("t", "A", True)   # 不应抛异常
