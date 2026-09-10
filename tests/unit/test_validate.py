# -*- coding: utf-8 -*-
"""工作流校验 / partials 展开 / 数据源任务的单元测试（无浏览器）。"""
import yaml

from playflow.engine import expand_partials, load_workflow, validate_workflow

# ---------------------------------------------------------------- 校验


def test_sample_workflow_valid():
    wf = {
        "name": "样例",
        "steps": [{"uses": "log", "with": {"message": "hi"}}],
        "tasks": [{"name": "t1", "mode": "once", "steps": [{"uses": "wait", "with": {"ms": 1}}]}],
    }
    errs, warns = validate_workflow(wf)
    assert errs == []


def test_unknown_action_missing_uses_and_params():
    bad = {"name": "坏流程", "steps": [
        {"uses": "不存在的动作"},
        {"name": "缺uses", "with": {}},
        {"uses": "goto", "with": {}},
    ]}
    errs, warns = validate_workflow(bad)
    assert any("未知动作" in e for e in errs)
    assert any("缺少 uses" in e for e in errs)
    assert any("缺少参数" in w for w in warns)


def test_bad_task_mode():
    errs, _ = validate_workflow(
        {"name": "x", "tasks": [{"name": "t", "mode": "乱写", "steps": []}]})
    assert any("mode" in e for e in errs)


def test_empty_workflow():
    errs, _ = validate_workflow({"name": "空"})
    assert any("既没有 steps" in e for e in errs)


# ---------------------------------------------------------------- partials / include


def test_expand_partials_inline():
    wf = {"name": "t",
          "partials": {"公共": [{"uses": "log", "with": {"message": "hi"}}]},
          "steps": [{"include": "公共"}, {"uses": "wait", "with": {"ms": 1}}]}
    out = expand_partials(wf)
    assert out["steps"] == [{"uses": "log", "with": {"message": "hi"}},
                            {"uses": "wait", "with": {"ms": 1}}]
    assert out["steps"][0] is not wf["partials"]["公共"][0]   # 深拷贝，不改原 partial


def test_expand_partials_nested_in_containers():
    wf = {"name": "t",
          "partials": {"p": [{"uses": "log", "with": {"message": "x"}}]},
          "steps": [{"uses": "if", "with": {"condition": "1 == 1"},
                     "then": [{"include": "p"}]}]}
    out = expand_partials(wf)
    assert out["steps"][0]["then"] == [{"uses": "log", "with": {"message": "x"}}]


def test_expand_partials_from_file(tmp_path):
    f = tmp_path / "common.yaml"
    f.write_text(yaml.safe_dump({"公共": [{"uses": "wait", "with": {"ms": 2}}]},
                                allow_unicode=True), encoding="utf-8")
    wf = {"name": "t",
          "partials": {"公共": {"file": str(f)}},
          "steps": [{"include": "公共"}]}
    out = expand_partials(wf)
    assert out["steps"] == [{"uses": "wait", "with": {"ms": 2}}]


def test_expand_partials_unknown_and_cycle():
    wf = {"name": "t", "steps": [{"include": "不存在"}]}
    try:
        expand_partials(wf)
        raised = False
    except Exception as ex:
        raised = "未定义" in str(ex) or "循环" in str(ex)
    assert raised

    wf2 = {"name": "t",
           "partials": {"a": [{"include": "b"}], "b": [{"include": "a"}]},
           "steps": [{"include": "a"}]}
    try:
        expand_partials(wf2)
        raised = False
    except Exception as ex:
        raised = "循环" in str(ex) or "嵌套" in str(ex)
    assert raised


def test_validate_include():
    wf = {"name": "t",
          "partials": {"p": [{"uses": "log", "with": {"message": "x"}}]},
          "steps": [{"include": "p"}]}
    errs, _ = validate_workflow(wf)
    assert errs == []
    errs2, _ = validate_workflow({"name": "t", "steps": [{"include": "没有的"}]})
    assert any("未定义的 partial" in e for e in errs2)
    errs3, _ = validate_workflow(
        {"name": "t", "partials": {"p": []},
         "steps": [{"include": "p", "uses": "log"}]})
    assert any("只能二选一" in e for e in errs3)


# ---------------------------------------------------------------- 数据源任务


def test_validate_data_task(tmp_path):
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("单号,备注\nA1,x\nA2,y\n", encoding="utf-8")
    wf = {"name": "t", "tasks": [
        {"name": "补录", "from_csv": str(csv_file),
         "steps": [{"uses": "log", "with": {"message": "{{ 单号 }}"}}]}]}
    errs, warns = validate_workflow(wf)
    assert errs == []
    assert not any("数据文件不存在" in w for w in warns)

    wf_bad = {"name": "t", "tasks": [
        {"name": "补录", "from_csv": str(tmp_path / "没有.csv"),
         "from_xlsx": "也不存在.xlsx", "mode": "once",
         "steps": [{"uses": "log", "with": {"message": "x"}}]}]}
    errs2, warns2 = validate_workflow(wf_bad)
    assert any("只能二选一" in e for e in errs2)
    assert any("数据文件不存在" in w for w in warns2)
    assert any("mode" in w for w in warns2)


def test_load_data_rows_csv(tmp_path):
    import pytest

    from playflow.engine import load_data_rows
    from playflow.errors import ConfigError
    f = tmp_path / "d.csv"
    f.write_text("单号,金额\nA1,5\nA2,6\n", encoding="utf-8")
    rows = load_data_rows({"from_csv": str(f)})
    assert rows == [{"单号": "A1", "金额": "5"}, {"单号": "A2", "金额": "6"}]
    with pytest.raises(ConfigError):
        load_data_rows({"from_csv": str(tmp_path / "没有.csv")})


def test_load_data_rows_csv_missing(tmp_path):
    import pytest

    from playflow.engine import load_data_rows
    from playflow.errors import ConfigError
    with pytest.raises(ConfigError):
        load_data_rows({"from_csv": str(tmp_path / "没有.csv")})


def test_load_workflow_missing():
    import pytest

    from playflow.errors import ConfigError
    with pytest.raises(ConfigError):
        load_workflow("没有这个文件.yaml")
