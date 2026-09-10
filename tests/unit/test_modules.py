# -*- coding: utf-8 -*-
"""report / notify / heal 纯逻辑与录制验证注释的单元测试（无浏览器）。"""
import json

import yaml

from playflow.engine import WorkflowEngine
from playflow.heal import _ratio, _suggest_for_selector, _suggest_for_text
from playflow.notify import build_payload, build_summary_text
from playflow.recorder import write_record_file

# ---------------------------------------------------------------- report

def _fake_summary_engine(dry_run=False):
    e = WorkflowEngine({"name": "演示"}, dry_run=dry_run)
    e.summary = {"类别一": {"成功": ["T101"], "失败": ["T102"], "跳过": [], "错误": ""},
                 "类别二": {"成功": [], "失败": [], "跳过": ["T201"], "错误": "HTTP 500"}}
    return e


def test_build_report(tmp_path, monkeypatch):
    from playflow import report as report_mod
    monkeypatch.setattr(report_mod, "base_dir", lambda: tmp_path)
    e = _fake_summary_engine()
    jp, hp = report_mod.write_reports(e, "completed", 12.34)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["workflow"] == "演示"
    assert data["counts"] == {"成功": 1, "失败": 1, "跳过": 1}
    assert data["tasks"]["类别二"]["错误"] == "HTTP 500"
    html = hp.read_text(encoding="utf-8")
    assert "演示" in html and "T102" in html and "HTTP 500" in html


# ---------------------------------------------------------------- notify

def test_build_payload_by_platform():
    wecom = build_payload("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k", "t", "正文")
    assert wecom["msgtype"] == "markdown" and "正文" in wecom["markdown"]["content"]
    ding = build_payload("https://oapi.dingtalk.com/robot/send?access_token=x", "标题", "正文")
    assert ding["markdown"]["title"] == "标题"
    other = build_payload("https://example.com/hook", "标题", "正文")
    assert other == {"title": "标题", "text": "正文"}


def test_build_summary_text():
    e = _fake_summary_engine()
    text = build_summary_text(e, "completed", 5.0, "r.html")
    assert "成功 1 · 失败 1 · 跳过 1" in text
    assert "类别一" in text and "T102" in text


def test_send_notify_unreachable_is_swallowed():
    from playflow.notify import send_notify
    results = send_notify({"webhook": "http://127.0.0.1:1/hook"}, "t", "x")
    assert len(results) == 1 and results[0]["ok"] is False


# ---------------------------------------------------------------- heal 纯逻辑

def test_ratio_ignores_whitespace():
    assert _ratio("签 收", "签收") == 1.0
    assert _ratio("提交", "提交 ") == 1.0
    assert _ratio("签收", "驳回") < 0.5


def test_suggest_for_text():
    candidates = [{"kind": "button", "text": "签 收 并 提交"},
                  {"kind": "button", "text": "驳回"},
                  {"kind": "input", "text": "", "selector": "#a"}]
    out = _suggest_for_text(["签收"], candidates)
    assert out and out[0]["value"] == "签 收 并 提交"
    assert out[0]["score"] >= 0.5


def test_suggest_for_selector():
    candidates = [{"kind": "input", "selector": "input[name='username']",
                   "name": "username", "placeholder": "", "text": "", "label": ""},
                  {"kind": "button", "selector": "button:has-text('登录')",
                   "text": "登录", "name": "", "placeholder": "", "label": ""}]
    out = _suggest_for_selector("input[name='usrname']", candidates)
    assert out[0]["value"] == "input[name='username']"


# ---------------------------------------------------------------- record 验证注释

def test_write_record_file_with_verify_results(tmp_path, monkeypatch):
    from playflow import recorder as rec_mod
    monkeypatch.setattr(rec_mod, "base_dir", lambda: tmp_path)
    steps = [{"uses": "click_text", "name": "点击提交", "with": {"text": ["提交"]}},
             {"uses": "fill", "name": "填写", "with": {"selector": "#a", "text": "x"}}]
    results = [(True, ""), (False, "选择器未命中任何元素：#a")]
    p = write_record_file(steps, path=tmp_path / "rec.yaml", verify_results=results)
    text = p.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert [s["uses"] for s in doc["steps"]] == ["click_text", "fill"]
    assert "回放验证失败" in text and "选择器未命中" in text
    # 注释必须位于失败步骤上方（注释行先于 fill 步骤出现）
    assert text.index("# ⚠ 回放验证失败") < text.index("uses: fill")
