# -*- coding: utf-8 -*-
"""条件表达式求值单元测试（无浏览器，用 FakeEngine）。"""
import pytest

from playflow.conditions import eval_condition
from playflow.errors import ConfigError


class FakeEngine:
    vars = {"n": 3, "s": "申请权限", "lst": [1, 2, 3], "type": "报修",
            "st": {"status": 200}, "page_title": "任务列表 - 平台"}

    def has_text(self, text, page=None):
        return text == "任务列表"

    def selector_exists(self, selector, frame=None):
        return selector == "#ok"

    def selector_visible(self, selector, frame=None):
        return selector == "#vis"

    def count_elements(self, selector, frame=None):
        return 4 if selector == "a[href]" else 0

    def get_text(self, selector, frame=None):
        return "任务列表" if selector == "h1" else ""

    def get_attr(self, selector, attr, frame=None):
        return "x" if selector == "#ok" else None

    def get_value(self, selector, frame=None):
        return ""

    @property
    def current(self):
        class P:
            url = "http://x/list"
            def title(self):
                return "任务列表"
        return P()

    @property
    def ctx(self):
        class C:
            pages = [1, 2]
        return C()


@pytest.mark.parametrize("cond,expected", [
    ("n == 3", True), ("n >= 3", True), ("n < 3", False),
    ("s contains 权限", True), ("s not contains 驳回", True),
    ("exists(#ok)", True), ("exists(#no)", False), ("visible(#vis)", True),
    ("has_text(任务列表)", True), ("not exists(#no)", True),
    ("exists(#no) or exists(#ok)", True), ("exists(#ok) and n == 3", True),
    ("exists(#no) or exists(#nope)", False), ("n == 9 or n == 3", True),
    ("type in ['报修','故障']", True), ("n in lst", True),
    ("s startswith 申请", True), ("count(a[href]) >= 1", True),
    ("text(h1) contains 任务列表", True), ("st.status == 200", True),
    ("page_title contains 任务列表", True),
    ("n > 2 and (n < 4 or n == 9)", True),
    ("url() contains /list", True), ("title() contains 任务列表", True),
    ("page_count() == 2", True),
    ("s contains ' a or b '", False),
    ("{{ n }} == 3", True),
])
def test_condition_matrix(cond, expected):
    assert eval_condition(cond, FakeEngine()) is expected


@pytest.mark.parametrize("cond,expected", [
    ("n==3", True), ("n>=3", True), ("n<2", False), ("n!=4", True),
    ("count(a[href])>=1", True), ("exists(#no)||exists(#ok)", True),
    ("exists(#ok)&&n==3", True), ("s not in ['驳回']", True),
    ("st.status==200", True), ("n>2 and (n<4 or n==9)", True),
])
def test_compact_operators_without_spaces(cond, expected):
    assert eval_condition(cond, FakeEngine()) is expected


@pytest.mark.parametrize("cond", [
    "n = 3", "n ! 3", "n ==", "has_tex(#ok)", "has_tex(#ok) == true",
])
def test_malformed_condition_raises(cond):
    with pytest.raises(ConfigError):
        eval_condition(cond, FakeEngine())


def test_structured_conditions():
    fe = FakeEngine()
    assert eval_condition({"and": [{"contains": ["{{ s }}", "权限"]},
                                   {"exists": "#ok"}]}, fe)
    assert eval_condition({"or": [{"exists": "#no"}, {"absent": "#nope"}]}, fe)
    assert eval_condition({"not": {"exists": "#no"}}, fe)
    assert not eval_condition({"and": [{"exists": "#ok"}, {"exists": "#no"}]}, fe)


def test_none_and_literal_conditions():
    assert eval_condition(None, FakeEngine()) is True
    assert eval_condition(True, FakeEngine()) is True
    assert eval_condition(0, FakeEngine()) is False
