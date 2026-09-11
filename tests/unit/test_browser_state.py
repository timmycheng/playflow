# -*- coding: utf-8 -*-
"""登录态复用判断与复用校验观察窗口的单元测试（无浏览器）。"""
import json
import time

import playflow.browser as pfb


def _write_state(tmp_path, cookies=None, origins=None, meta=None):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "cookies": cookies or [],
        "origins": origins or [],
    }), encoding="utf-8")
    if meta is not None:
        (tmp_path / "state.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return state


def test_state_reusable_live_cookie(tmp_path):
    state = _write_state(tmp_path, cookies=[
        {"name": "sid", "value": "x", "domain": "app.example.com", "path": "/",
         "expires": time.time() + 3600}])
    ok, why = pfb.state_reusable(state, "http://app.example.com/home")
    assert ok and why == ""


def test_state_reusable_session_cookie_without_expires(tmp_path):
    state = _write_state(tmp_path, cookies=[
        {"name": "sid", "value": "x", "domain": ".example.com", "path": "/"}])
    ok, _ = pfb.state_reusable(state, "http://app.example.com/home")
    assert ok


def test_state_reusable_expired_cookie(tmp_path):
    state = _write_state(tmp_path, cookies=[
        {"name": "sid", "value": "x", "domain": "app.example.com", "path": "/",
         "expires": time.time() - 10}])
    ok, why = pfb.state_reusable(state, "http://app.example.com/home")
    assert not ok and "过期" in why


def test_state_reusable_expired_cookie_but_live_other(tmp_path):
    state = _write_state(tmp_path, cookies=[
        {"name": "old", "value": "x", "domain": "app.example.com", "path": "/",
         "expires": time.time() - 10},
        {"name": "sid", "value": "y", "domain": "app.example.com", "path": "/",
         "expires": -1}])
    ok, _ = pfb.state_reusable(state, "http://app.example.com/home")
    assert ok


def test_state_reusable_localstorage_origin(tmp_path):
    state = _write_state(tmp_path, origins=[{"origin": "http://app.example.com"}])
    ok, _ = pfb.state_reusable(state, "http://app.example.com/home")
    assert ok


def test_state_reusable_meta_other_site(tmp_path):
    state = _write_state(tmp_path,
                         cookies=[{"name": "sid", "value": "x",
                                   "domain": "app.example.com", "path": "/",
                                   "expires": -1}],
                         meta={"origin": "other.example.com"})
    ok, why = pfb.state_reusable(state, "http://app.example.com/home")
    assert not ok and "不是同一站点" in why


def test_state_reusable_missing_file(tmp_path):
    ok, why = pfb.state_reusable(tmp_path / "nope.json", "http://x/home")
    assert not ok and why == "没有 state 文件"


class _FakePage:
    def __init__(self):
        self.slept = 0

    def wait_for_timeout(self, ms):
        self.slept += ms


def test_wait_login_markers_detects_late_redirect(monkeypatch):
    page = _FakePage()
    calls = {"n": 0}

    def fake_is_login_page(_page):
        calls["n"] += 1
        return calls["n"] >= 3

    monkeypatch.setattr(pfb, "is_login_page", fake_is_login_page)
    monkeypatch.setattr(pfb, "_needs_verification", lambda p, cfg: False)
    assert pfb.wait_login_markers(page, {}, timeout=3000, interval=250)
    assert page.slept == 500


def test_wait_login_markers_stays_false_when_clean(monkeypatch):
    monkeypatch.setattr(pfb, "is_login_page", lambda p: False)
    monkeypatch.setattr(pfb, "_needs_verification", lambda p, cfg: False)
    assert not pfb.wait_login_markers(_FakePage(), {}, timeout=500, interval=100)


def test_wait_login_markers_verification_text(monkeypatch):
    monkeypatch.setattr(pfb, "is_login_page", lambda p: False)
    monkeypatch.setattr(pfb, "_needs_verification", lambda p, cfg: True)
    assert pfb.wait_login_markers(_FakePage(), {"verification_text": "请验证"},
                                  timeout=500, interval=100)
