# -*- coding: utf-8 -*-
"""
selftest.py —— playflow 在 mock 平台上的自动验收（外网彩排用）

前置：mock_platform.py 已在本机 8899 端口运行（python tests/mock_platform.py）
运行：在仓库根目录执行 python tests/selftest.py

说明：真实环境的 UKey/二次验证需要人工按回车；本脚本替换
playflow.browser.pause_for_manual，自动扮演人工去点 mock 平台的
“点击模拟UKey验证”按钮后再放行。
"""
import builtins
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import playflow as pf
from playflow import actions as pf_actions
from playflow import browser as pfb
from playflow import utils as pf_utils
from playflow.conditions import eval_condition

BASE = "http://127.0.0.1:8899"
PASS, FAIL = [], []

pf.set_base_dir(ROOT)


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [通过] %s" % name)
    else:
        FAIL.append((name, detail))
        print("  [失败] %s  %s" % (name, detail))


def http_json(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def reset_mock():
    urllib.request.urlopen(BASE + "/reset", timeout=5).read()


# ------------------------------------------------- 替换人工验证环节
ukey_clicks = 0


def fake_pause(hint=""):
    """自动扮演人工：在 mock 页面上点击模拟 UKey 验证按钮。"""
    global ukey_clicks
    ctx = pfb._ACTIVE_CONTEXT
    clicked = False
    if ctx:
        for pg in ctx.pages:
            if "ukey" in (pg.url or ""):
                pg.click("text=点击模拟UKey验证")
                pg.wait_for_load_state("domcontentloaded", timeout=10000)
                clicked = True
                break
    ukey_clicks += 1
    print("  （自动完成人工验证：%s）" % ("已点击" if clicked else "未找到验证页！"))


pfb.pause_for_manual = fake_pause
pf_actions.pause_for_manual = fake_pause


def clean_artifacts():
    for d in ("shots", "logs", "附件"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    (ROOT / "state.json").unlink(missing_ok=True)
    (ROOT / "state.meta.json").unlink(missing_ok=True)
    pf_utils.reset_log()
    pf_utils.reset_shots()


def attachments():
    root = ROOT / "附件"
    if not root.exists():
        return []
    return sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                  for p in root.rglob("*") if p.is_file())


def tasks_by_no():
    return {t["no"]: t for t in http_json("/status")["tasks"]}


# ------------------------------------------------- 工作流样例

TASK_STEPS = [
    {"uses": "click_row_link"},
    {"uses": "evaluate", "with": {"js": "() => document.body.innerText",
                                  "name": "body_text"}},
    {"uses": "click_text", "with": {"text": ["签收", "受理"], "optional": True}},
    {"uses": "pick_radio", "with": {"text": "驳回"}, "if": "body_text contains 申请"},
    {"uses": "pick_radio", "with": {"text": "同意"}, "if": "body_text not contains 申请"},
    {"uses": "download", "with": {"optional": True}},
    {"uses": "click_text", "with": {"text": ["提交", "确定"]}},
    {"uses": "expect_text", "with": {"text": "提交成功"}, "continue_on_error": True},
    {"uses": "close_task_page"},
]


def sample_workflow(max_tasks=1, headless=True):
    return {
        "name": "测试工作流",
        "settings": {
            "max_tasks": max_tasks,
            "attachment_dir": "附件",
            "task_delay_seconds": [0.05, 0.1],
            "screenshot": True,
        },
        "browser": {"channel": "chrome", "headless": headless},
        "login": {
            "url": BASE + "/login",
            "username": "admin",
            "password": "123456",
            "username_selector": "input[name='username']",
            "password_selector": "input[name='password']",
            "button_text": ["登录", "登 录"],
            "manual_pause": True,
            "success_url": BASE + "/home",
        },
        "env": {"operator": "自动处理"},
        "steps": [
            {"uses": "log", "with": {"message": "操作人 {{ operator }} / {{ env.operator }}"}},
        ],
        "tasks": [
            {"name": "类别一", "url": BASE + "/list1_page",
             "label_regex": "[A-Za-z]{1,6}-?\\d{2,}", "steps": TASK_STEPS},
            {"name": "类别二", "url": BASE + "/list2_page",
             "label_regex": "[A-Za-z]{1,6}-?\\d{2,}", "steps": TASK_STEPS},
        ],
    }


EXPECT_DECISION = {
    "T101": "同意", "T102": "同意", "T103": "同意", "T104": "驳回", "T105": "驳回",
    "T201": "驳回", "T202": "驳回", "T203": "驳回", "T204": "同意", "T205": "同意",
}


# ================================================================ 纯逻辑用例

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


def test_conditions_matrix():
    print("\n【1】条件表达式矩阵")
    fe = FakeEngine()
    cases = [
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
        ("s contains ' a or b '", False),
    ]
    bad = [c for c, exp in cases if eval_condition(c, fe) != exp]
    check("条件/函数矩阵 %d 项" % len(cases), not bad, str(bad))
    check("结构化条件", eval_condition({"and": [{"contains": ["{{ s }}", "权限"]},
                                                {"exists": "#ok"}]}, fe))
    from playflow.template import render
    check("模板渲染保留原始类型",
          render("{{ n }}", fe.vars) == 3 and render("x{{ n }}", fe.vars) == "x3"
          and render("{{ st.status }}", fe.vars) == 200)


def test_validate():
    print("\n【2】工作流校验")
    wf = sample_workflow()
    errs, warns = pf.validate_workflow(wf)
    check("样例工作流校验通过", not errs, str(errs))
    bad = {"name": "坏流程", "steps": [
        {"uses": "不存在的动作"},
        {"name": "缺uses", "with": {}},
        {"uses": "goto", "with": {}},
    ]}
    errs2, warns2 = pf.validate_workflow(bad)
    check("未知动作/缺 uses/缺参数 能被发现",
          any("未知动作" in e for e in errs2) and any("缺少 uses" in e for e in errs2)
          and any("缺少参数" in w for w in warns2), (errs2, warns2))
    bad_mode = {"name": "x", "tasks": [{"name": "t", "mode": "乱写", "steps": []}]}
    errs3, _ = pf.validate_workflow(bad_mode)
    check("非法 tasks.mode 能被发现", any("mode" in e for e in errs3), errs3)


# ================================================================ 端到端用例

def test_first_run_and_single_task():
    print("\n【3】首次运行：登录→人工验证→state.json→两类各 1 条")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    summary = pf.run_workflow(workflow=sample_workflow(1))

    check("人工验证环节暂停了一次", ukey_clicks == 1, "实际 %d 次" % ukey_clicks)
    check("state.json 已生成", (ROOT / "state.json").exists())
    st = tasks_by_no()
    check("类别一第 1 条已提交", st.get("T101", {}).get("status") == "submitted",
          str(st.get("T101")))
    check("类别二第 1 条已提交（iframe 列表生效）",
          st.get("T201", {}).get("status") == "submitted", str(st.get("T201")))
    check("if+pick_radio 组合：T101 报修→同意", st.get("T101", {}).get("decision") == "同意")
    check("if+pick_radio 组合：T201 权限申请→驳回", st.get("T201", {}).get("decision") == "驳回")
    atts = attachments()
    check("附件按 类别/任务号_文件名 归档（含中文文件名）",
          any(a.startswith("附件/类别一/T101_报告_设备报修_T101") for a in atts)
          and any(a.startswith("附件/类别二/T201_报告_权限申请_T201") for a in atts),
          str(atts))
    check("汇总成功 2 条", sum(len(s["成功"]) for s in summary.values()) == 2, str(summary))
    check("日志/截图已生成",
          bool(list((ROOT / "logs").glob("run_*.log")))
          and len(list((ROOT / "shots").glob("*.png"))) >= 4)


def test_second_run_reuses_state():
    print("\n【4】二次运行：复用 state.json，不再触碰人工验证")
    reset_mock()
    before = ukey_clicks
    pf.run_workflow(workflow=sample_workflow(1))
    check("二次运行未再人工验证", ukey_clicks == before,
          "次数 %d → %d" % (before, ukey_clicks))
    st = tasks_by_no()
    check("二次运行照常处理任务",
          st.get("T101", {}).get("status") == "submitted"
          and st.get("T201", {}).get("status") == "submitted")


def test_full_batch():
    print("\n【5】批量：两类 10 条全部处理，决策/附件/日志齐全")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    summary = pf.run_workflow(workflow=sample_workflow(99))
    st = tasks_by_no()
    submitted = [no for no, t in st.items() if t["status"] == "submitted"]
    check("全部 10 条任务处理完", len(submitted) == 10, "已提交：%s" % sorted(submitted))
    bad = {no: st[no]["decision"] for no in EXPECT_DECISION
           if st.get(no, {}).get("decision") != EXPECT_DECISION[no]}
    check("10 条任务决策全部符合期望", not bad, str(bad))
    atts = attachments()
    cat1 = [a for a in atts if a.startswith("附件/类别一/")]
    cat2 = [a for a in atts if a.startswith("附件/类别二/")]
    check("附件按类别归档（5+5）", len(cat1) == 5 and len(cat2) == 5,
          "类别一 %d 个，类别二 %d 个" % (len(cat1), len(cat2)))
    check("汇总成功 10 条", sum(len(s["成功"]) for s in summary.values()) == 10, str(summary))
    logf = list((ROOT / "logs").glob("run_*.log"))[0]
    content = logf.read_text(encoding="utf-8")
    check("日志覆盖任务号与弹窗处理", "任务 T101" in content and "自动" in content)


def test_error_handling():
    print("\n【6】错误处理：坏选择器只失败该任务；continue_on_error 生效")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["tasks"][0]["steps"] = [{"uses": "click", "with": {"selector": "#绝不存在的元素"}}] \
        + wf["tasks"][0]["steps"]
    summary = pf.run_workflow(workflow=wf)
    st = tasks_by_no()
    check("坏选择器 → 该任务记为失败而非崩溃",
          summary["类别一"]["失败"] == ["T101"], str(summary["类别一"]))
    check("被中断的任务未提交", st.get("T101", {}).get("status") == "pending")
    check("其它类别不受影响", st.get("T201", {}).get("status") == "submitted")

    reset_mock()
    wf2 = sample_workflow(1)
    wf2["tasks"][0]["steps"] = [
        {"uses": "click", "with": {"selector": "#不存在"}, "continue_on_error": True}
    ] + wf2["tasks"][0]["steps"]
    pf.run_workflow(workflow=wf2)
    st2 = tasks_by_no()
    check("continue_on_error → 任务仍成功", st2.get("T101", {}).get("status") == "submitted")

    bad = sample_workflow(1)
    bad["tasks"][0]["steps"] = [{"uses": "不存在的动作"}] + bad["tasks"][0]["steps"]
    err = None
    try:
        pf.run_workflow(workflow=bad)
    except pf.ConfigError as ex:
        err = str(ex)
    check("未知动作 → 中文 ConfigError", err is not None and "未知步骤" in err, str(err)[:100])

    reset_mock()
    bad_url = sample_workflow(1)
    bad_url["tasks"][0]["url"] = "http://127.0.0.1:59999/list"
    summary3 = pf.run_workflow(workflow=bad_url)
    check("坏列表 URL → 只记录该任务错误，其它任务继续",
          summary3["类别一"].get("错误") and
          summary3["类别二"]["成功"] == ["T201"], str(summary3))


def test_conditions_and_loops():
    print("\n【7】循环/条件：for_each + if/else + while + break + 浏览器条件函数")
    wf = {
        "steps": [
            {"uses": "set_var", "with": {"name": "total", "value": 0}},
            {"uses": "for_each", "with": {"over": [1, 2, 3], "as": "n"},
             "do": [
                 {"uses": "set_var", "with": {"name": "total", "value": "{{ n }}"}},
                 {"uses": "if", "with": {"condition": "n == 2"},
                  "then": [{"uses": "set_var", "with": {"name": "hit2", "value": "yes"}}],
                  "else": [{"uses": "set_var", "with": {"name": "other", "value": "{{ n }}"}}]},
             ]},
            {"uses": "while", "with": {"condition": "i < 9", "as": "i", "max": 9},
             "do": [
                 {"uses": "if", "with": {"condition": "i >= 1"},
                  "then": [{"uses": "break"}]},
             ]},
        ]
    }
    engine = pf.WorkflowEngine(wf)
    engine.run_steps(wf["steps"])
    check("for_each 遍历 + 变量渲染", engine.vars.get("total") == 3
          and engine.vars.get("hit2") == "yes")
    check("if/else 分支", str(engine.vars.get("other")) == "3",
          repr(engine.vars.get("other")))
    check("while + break", engine.vars.get("i") == 1, repr(engine.vars.get("i")))

    wf2 = {
        "login": sample_workflow()["login"],
        "steps": [
            {"uses": "goto", "with": {"url": BASE + "/list2_page"}},
            {"uses": "assert", "with": {"condition": "exists(iframe)", "message": "iframe 应存在"}},
            {"uses": "for_each", "with": {"rows": "iframe", "frame": "#1"}, "do": [
                {"uses": "log", "with": {"message": "frame 内元素：{{ item }}"}}]},
            {"uses": "assert", "with": {"condition": "has_text(任务列表)"}},
            {"uses": "assert", "with": {"condition": "count(iframe) == 1"}},
            {"uses": "assert", "with": {"condition": "url() contains list2_page"}},
        ],
    }
    pf.run_workflow(workflow=wf2)
    check("浏览器条件函数 exists/has_text/count/url 与 frame 定向可用", True)


def test_custom_login_steps():
    print("\n【8】自定义 login.steps 路径")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["login"] = {
        "steps": [
            {"uses": "goto", "with": {"url": BASE + "/login"}},
            {"uses": "fill", "with": {"selector": "input[name='username']", "text": "admin"}},
            {"uses": "fill", "with": {"selector": "input[name='password']",
                                      "text": "123456", "secret": True}},
            {"uses": "click_text", "with": {"text": ["登录", "登 录"]}},
            {"uses": "pause", "with": {"message": "请完成 UKey"}},
            {"uses": "goto", "with": {"url": BASE + "/home"}},
            {"uses": "assert", "with": {"condition": "has_text(任务处理平台)",
                                        "message": "登录后应看到平台首页"}},
        ],
    }
    pf.run_workflow(workflow=wf)
    check("自定义 login.steps 完成登录并保存 state",
          (ROOT / "state.json").exists() and ukey_clicks == 1, "ukey=%d" % ukey_clicks)
    st = tasks_by_no()
    check("自定义登录后任务照常处理", st.get("T101", {}).get("status") == "submitted")


def test_probe_action():
    print("\n【9】probe 动作：探测 iframe 列表并生成 YAML 草稿")
    import yaml
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["tasks"] = []
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/list2_page"}},
        {"uses": "probe", "with": {"file": "shots/list2_probe.yaml", "all_pages": True}},
    ]
    pf.run_workflow(workflow=wf)
    probe_file = ROOT / "shots" / "list2_probe.yaml"
    text = probe_file.read_text(encoding="utf-8") if probe_file.exists() else ""
    doc = yaml.safe_load(text) if text else None
    uses = [s.get("uses") for s in (doc or {}).get("suggested_steps", [])]
    frames = [fr for pg in (doc or {}).get("probe", []) for fr in pg.get("frames", [])]
    check("probe 生成 YAML 文件且含 click_row_link",
          probe_file.exists() and "click_row_link" in uses, uses)
    check("probe 识别 iframe 与列表行候选",
          any(fr.get("iframe") for fr in frames) and any(fr.get("rows") for fr in frames),
          [(fr.get("iframe"), bool(fr.get("rows"))) for fr in frames])


def test_generic_actions():
    print("\n【10】通用动作：extract/evaluate/request + tasks mode once/each_row")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/list1_page"}},
        {"uses": "extract", "with": {"selector": "table tr", "all": True,
                                     "join": " | ", "name": "rows_text"}},
        {"uses": "extract", "with": {"selector": "a[href]", "what": "count",
                                     "name": "link_count"}},
        {"uses": "extract", "with": {"selector": "table a[href]", "nth": 1,
                                     "name": "second_row_link", "until": "second_row_link != ''",
                                     "timeout": 3000}},
        {"uses": "assert", "with": {"condition": "second_row_link contains 网络",
                                    "message": "extract nth 应取到第 2 行链接"}},
        {"uses": "evaluate", "with": {"js": "() => document.title", "name": "page_title"}},
        {"uses": "request", "with": {"url": BASE + "/status", "name": "st"}},
        {"uses": "write_file", "with": {"path": "shots/status.json", "content": "{{ st }}"}},
        {"uses": "assert", "with": {"condition": "count(a[href]) >= 1"}},
        {"uses": "assert", "with": {"condition": "text(h1) contains 任务列表"}},
        {"uses": "assert", "with": {"condition": "st.status == 200"}},
        {"uses": "assert", "with": {"condition": "page_title contains 任务列表"}},
    ]
    wf["tasks"] = [
        {"name": "单页流程", "mode": "once", "steps": [
            {"uses": "goto", "with": {"url": BASE + "/list1_page"}},
            {"uses": "assert", "with": {"condition": "has_text(任务列表)"}},
        ]},
        {"name": "逐行模式", "mode": "each_row", "max_tasks": 2, "remove_after": False,
         "url": BASE + "/list1_page", "steps": [
             {"uses": "click_row_link"},
             {"uses": "close_task_page"},
         ]},
    ]
    summary = pf.run_workflow(workflow=wf)
    report = ROOT / "shots" / "status.json"
    check("write_file 落盘 JSON", report.exists()
          and json.loads(report.read_text(encoding="utf-8")).get("status") == 200)
    check("mode=once 执行成功", "单页流程" in summary, str(summary.keys()))
    check("mode=each_row 处理 2 行",
          len(summary.get("逐行模式", {}).get("成功", [])) == 2, str(summary))


def test_record_action():
    print("\n【11】record 动作：模拟点击 → 生成 YAML 步骤")
    import yaml
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["tasks"] = []
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/home"}},
        {"uses": "record", "with": {"file": "shots/rec_test.yaml"}},
    ]

    real_input = builtins.input

    def fake_record_input(prompt=""):
        ctx = pfb._ACTIVE_CONTEXT
        pg = ctx.pages[0]
        pg.evaluate("() => { const a = document.querySelector('a[href]');"
                    " if (a) a.click(); }")
        pg.wait_for_timeout(500)
        return ""

    builtins.input = fake_record_input
    try:
        pf.run_workflow(workflow=wf)
    finally:
        builtins.input = real_input

    rec = ROOT / "shots" / "rec_test.yaml"
    doc = yaml.safe_load(rec.read_text(encoding="utf-8")) if rec.exists() else None
    steps = (doc or {}).get("steps", [])
    check("record 捕获点击为 click_text",
          any(s.get("uses") == "click_text" for s in steps), steps[:3])
    check("record 产物含 login 配置且密码脱敏",
          isinstance((doc or {}).get("login"), dict)
          and doc["login"].get("password") == "{{ env.password }}",
          (doc or {}).get("login"))


def test_state_scoping():
    print("\n【12】state.json 站点隔离")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    st_path = ROOT / "state.json"
    foreign = {"cookies": [{"name": "sid", "value": "foreign",
                            "domain": "other.example.com", "path": "/"}],
               "origins": []}
    st_path.write_text(json.dumps(foreign), encoding="utf-8")
    (ROOT / "state.meta.json").write_text(json.dumps(
        {"origin": "other.example.com", "workflow": "别的流程"}, ensure_ascii=False),
        encoding="utf-8")
    pf.run_workflow(workflow=sample_workflow(1))
    check("带元数据的异站 state → 重新登录/验证", ukey_clicks == 1, "ukey=%d" % ukey_clicks)
    data = json.loads(st_path.read_text(encoding="utf-8"))
    domains = {str(c.get("domain") or "").lstrip(".") for c in data.get("cookies", [])}
    check("state.json 已覆盖为本站点登录态", "127.0.0.1" in domains, domains)

    clean_artifacts()
    st_path.write_text(json.dumps(foreign), encoding="utf-8")
    ukey_clicks = 0
    pf.run_workflow(workflow=sample_workflow(1))
    check("无元数据的异站 state → 也能识别并重登", ukey_clicks == 1, "ukey=%d" % ukey_clicks)

    clean_artifacts()
    reset_mock()
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["login"]["state_file"] = "shots/custom_state.json"
    pf.run_workflow(workflow=wf)
    check("自定义 state_file 首次走登录且文件+元数据生成",
          ukey_clicks == 1 and (ROOT / "shots" / "custom_state.json").exists()
          and (ROOT / "shots" / "custom_state.meta.json").exists())
    before = ukey_clicks
    reset_mock()
    pf.run_workflow(workflow=wf)
    check("自定义 state_file 二次复用", ukey_clicks == before,
          "ukey %d→%d" % (before, ukey_clicks))


def test_cli_validate(tmpdir=None):
    print("\n【13】CLI：validate 子命令")
    import subprocess
    wf_file = ROOT / "_cli_wf.yaml"
    import yaml
    wf_file.write_text(yaml.safe_dump(sample_workflow(), allow_unicode=True,
                                      sort_keys=False), encoding="utf-8")
    try:
        ret = subprocess.run([sys.executable, "-m", "playflow", "validate", str(wf_file)],
                             capture_output=True, text=True, cwd=str(ROOT))
        check("playflow validate 退出码 0 且输出 OK",
              ret.returncode == 0 and "校验通过" in (ret.stdout + ret.stderr),
              (ret.returncode, ret.stdout[-200:], ret.stderr[-200:]))
    finally:
        wf_file.unlink(missing_ok=True)


def main():
    pf.init_stdio()
    print("=" * 62)
    print("playflow 自动验收（目标：mock 平台 %s）" % BASE)
    print("=" * 62)
    try:
        http_json("/status")
    except Exception:
        print("!! mock 平台未运行，请先执行：python tests/mock_platform.py")
        sys.exit(2)
    test_conditions_matrix()
    test_validate()
    test_first_run_and_single_task()
    test_second_run_reuses_state()
    test_full_batch()
    test_error_handling()
    test_conditions_and_loops()
    test_custom_login_steps()
    test_probe_action()
    test_generic_actions()
    test_record_action()
    test_state_scoping()
    test_cli_validate()
    print("\n" + "=" * 62)
    print("验收结果：通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  x %s  %s" % (name, detail))
    print("=" * 62)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
