# -*- coding: utf-8 -*-
"""模板渲染单元测试（无浏览器）。"""
from playflow.template import as_bool, dig, render


def test_render_keeps_type_for_single_template():
    vars_ = {"n": 3, "f": 1.5, "b": True, "d": {"a": {"b": 7}}}
    assert render("{{ n }}", vars_) == 3
    assert render("{{ f }}", vars_) == 1.5
    assert render("{{ b }}", vars_) is True
    assert render("{{ d.a.b }}", vars_) == 7


def test_render_interpolates_in_string():
    assert render("x{{ n }}y", {"n": 3}) == "x3y"
    assert render("{{ a }}-{{ b }}", {"a": 1, "b": "二"}) == "1-二"


def test_render_missing_becomes_empty():
    assert render("x{{ nope }}y", {}) == "xy"


def test_render_recursive_structures():
    vars_ = {"name": "测试", "items": [1, 2]}
    out = render({"msg": "你好 {{ name }}", "list": ["{{ name }}", "{{ items.0 }}"]}, vars_)
    assert out == {"msg": "你好 测试", "list": ["测试", 1]}


def test_dig_paths():
    data = {"a": {"b": [10, 20]}}
    assert dig(data, "a.b.1") == 20
    assert dig(data, "a.x") is None
    assert dig(data, "a.b.9") is None
    assert dig("flat", "a.b") is None


def test_as_bool():
    assert as_bool(True) is True
    assert as_bool("false") is False
    assert as_bool("0") is False
    assert as_bool("") is False
    assert as_bool(None) is False
    assert as_bool(1) is True
    assert as_bool("yes") is True
