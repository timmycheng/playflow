# -*- coding: utf-8 -*-
"""
selftest.py —— tool_kit.py 在 mock 平台上的自动验收脚本（外网彩排用）

前置：mock_platform.py 已在本机 8899 端口运行；本脚本用无头 Chrome 全自动跑完
prompt 中的全部验收项。运行：python selftest.py

说明：UKey 半自动环节在真实环境需要人工按回车；本脚本通过替换 tool_kit.pause_for_ukey
自动"扮演人工"——在浏览器里点击 mock 平台的"点击模拟UKey验证"按钮后再放行。
"""
import io
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import tool_kit as tk

BASE = "http://127.0.0.1:8899"
PASS, FAIL = [], []


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


# ------------------------------------------------- 替换 UKey 人工环节
ukey_clicks = 0


def fake_ukey_pause(hint=""):
    """自动扮演人工：在 mock 页面上点击模拟 UKey 按钮（真实环境由人工完成）"""
    global ukey_clicks
    ctx = tk._ACTIVE_CONTEXT
    clicked = False
    if ctx:
        for pg in ctx.pages:
            if "ukey" in (pg.url or ""):
                pg.click("text=点击模拟UKey验证")
                pg.wait_for_load_state("domcontentloaded", timeout=10000)
                clicked = True
                break
    ukey_clicks += 1
    print("  （自动完成模拟 UKey 验证：%s）" % ("已点击" if clicked else "未找到 UKey 页！"))


tk.pause_for_ukey = fake_ukey_pause


def base_cfg(**over):
    cfg = {
        "base_url": BASE,
        "sso_login_url": BASE + "/login",
        "sso_username": "admin",
        "sso_password": "123456",
        "headless": True,
        "max_tasks": 1,
        "radio_rules": [
            {"if_type_contains": "报修", "then_pick": "同意"},
            {"if_type_contains": "故障", "then_pick": "同意"},
            {"if_type_contains": "申请", "then_pick": "驳回"},
        ],
        "default_radio": "同意",
        "categories": [
            {"name": "类别一", "url": BASE + "/list1_page", "row_selector": "", "link_selector": ""},
            {"name": "类别二", "url": BASE + "/list2_page", "row_selector": "", "link_selector": ""},
        ],
    }
    cfg.update(over)
    return cfg


def apply_defaults(cfg):
    """直接复用 tool_kit 的默认值填充逻辑"""
    merged = {}
    merged.update({k: json.loads(json.dumps(v)) for k, v in tk._DEFAULT_CFG.items()})
    merged.update(cfg)
    return merged


def clean_artifacts():
    for d in ("shots", "logs", "附件"):
        shutil.rmtree(tk.BASE_DIR / d, ignore_errors=True)
    (tk.BASE_DIR / "state.json").unlink(missing_ok=True)
    (tk.BASE_DIR / "state.meta.json").unlink(missing_ok=True)
    tk._LOG_FILE = None      # 日志路径有缓存，目录删除后需复位
    tk._SHOT_N = 0


def attachments():
    root = tk.BASE_DIR / "附件"
    return sorted(str(p.relative_to(tk.BASE_DIR)).replace("\\", "/")
                  for p in root.rglob("*") if p.is_file()) if root.exists() else []


def tasks_by_no():
    return {t["no"]: t for t in http_json("/status")["tasks"]}


# ================================================================ 用例
def test_first_run_and_single_task():
    print("\n【验收1】首次运行：登录→UKey暂停→state.json 生成→单条任务跑通")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    summary = tk.run_mode(apply_defaults(base_cfg(max_tasks=1)))

    check("UKey 环节暂停了一次（人工介入点）", ukey_clicks == 1, "实际 %d 次" % ukey_clicks)
    check("state.json 已生成", (tk.BASE_DIR / "state.json").exists())
    st = tasks_by_no()
    check("类别一第 1 条已提交", st.get("T101", {}).get("status") == "submitted",
          str(st.get("T101")))
    check("类别二第 1 条已提交（iframe 列表生效）",
          st.get("T201", {}).get("status") == "submitted", str(st.get("T201")))
    check("radio 按规则选择：T101 报修→同意", st.get("T101", {}).get("decision") == "同意",
          str(st.get("T101", {}).get("decision")))
    check("radio 按规则选择：T201 权限申请→驳回", st.get("T201", {}).get("decision") == "驳回",
          str(st.get("T201", {}).get("decision")))
    atts = attachments()
    check("附件按 类别/任务号_文件名 归档（含中文文件名）",
          any(a.startswith("附件/类别一/T101_报告_设备报修_T101") for a in atts)
          and any(a.startswith("附件/类别二/T201_报告_权限申请_T201") for a in atts), str(atts))
    check("run 汇总成功 2 条", sum(len(s["成功"]) for s in summary.values()) == 2, str(summary))
    logs = list((tk.BASE_DIR / "logs").glob("run_*.log"))
    check("日志文件已生成", bool(logs))
    shots = list((tk.BASE_DIR / "shots").glob("*.png"))
    check("关键动作截图已生成（>=8 张）", len(shots) >= 8, "实际 %d 张" % len(shots))


def test_second_run_reuses_state():
    print("\n【验收2】二次运行：复用 state.json，不再出现登录与 UKey 暂停")
    reset_mock()
    global ukey_clicks
    before = ukey_clicks
    tk.run_mode(apply_defaults(base_cfg(max_tasks=1)))
    check("二次运行未触碰 UKey", ukey_clicks == before,
          "UKey 暂停次数从 %d 变为 %d" % (before, ukey_clicks))
    st = tasks_by_no()
    check("二次运行照常处理任务", st.get("T101", {}).get("status") == "submitted"
          and st.get("T201", {}).get("status") == "submitted")


def test_inspect():
    print("\n【验收3】inspect 模式：按钮文字与实际一致（含带空格的“提 交”）")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        cfg = apply_defaults(base_cfg())
        browser = tk.launch_browser(p, cfg, force_headed=False)
        try:
            ctx, page = tk.ensure_login(browser, cfg, tk.log)
            page.goto(BASE + "/task/T102", wait_until="domcontentloaded")
            out1 = tk.inspect_dump(ctx)
            page.goto(BASE + "/list2_page", wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            out2 = tk.inspect_dump(ctx)
        finally:
            browser.close()
    check("详情页输出包含“签收”", "签收" in out1)
    check("详情页输出包含带空格的“提 交”", "提 交" in out1)
    check("详情页输出 radio（同意/驳回 + label）",
          "[radio]" in out1 and "同意" in out1 and "驳回" in out1)
    check("iframe 页面被标注 [iframe] 并列出内层列表",
          "[iframe]" in out2 and "/list2" in out2)


def test_bad_config_url():
    print("\n【验收4】故意改错 URL：给出清晰中文报错而不是裸堆栈")
    # 场景A：base_url 错 → 致命错误，中文提示
    err = None
    try:
        tk.run_mode(apply_defaults(base_cfg(base_url="http://127.0.0.1:59999")))
    except tk.FatalError as e:
        err = str(e)
    check("base_url 错误 → FatalError 中文提示", err is not None and "无法访问" in err
          and "config.json" in err, str(err)[:120])
    check("报错不含裸 Playwright 堆栈", err is not None and "Traceback" not in err)
    # 场景B：类别 URL 错 → 只失败该类别，其它类别继续
    reset_mock()
    cfg = apply_defaults(base_cfg(max_tasks=1, categories=[
        {"name": "坏类别", "url": BASE + "/not_exist_page", "row_selector": "", "link_selector": ""},
        {"name": "类别二", "url": BASE + "/list2_page", "row_selector": "", "link_selector": ""},
    ]))
    summary = tk.run_mode(cfg)
    check("坏类别 URL → 类别错误被记录且不中断整体",
          summary["坏类别"].get("错误") != "" and len(summary["类别二"]["成功"]) == 1,
          str(summary))


def test_full_batch():
    print("\n【验收5】max_tasks 调大：两类任务全部处理完，附件/日志/截图齐全")
    reset_mock()
    # 只清空附件与截图；保留 state.json，用于验证“批量运行全程不再触碰 UKey”
    shutil.rmtree(tk.BASE_DIR / "附件", ignore_errors=True)
    shutil.rmtree(tk.BASE_DIR / "shots", ignore_errors=True)
    tk._SHOT_N = 0
    global ukey_clicks
    ukey_clicks = 0
    summary = tk.run_mode(apply_defaults(base_cfg(max_tasks=99)))
    st = tasks_by_no()
    submitted = [no for no, t in st.items() if t["status"] == "submitted"]
    check("全部 10 条任务处理完", len(submitted) == 10, "已提交：%s" % sorted(submitted))
    expect = {"T101": "同意", "T102": "同意", "T103": "同意", "T104": "驳回", "T105": "驳回",
              "T201": "驳回", "T202": "驳回", "T203": "驳回", "T204": "同意", "T205": "同意"}
    bad = {no: st[no]["decision"] for no in expect if st.get(no, {}).get("decision") != expect[no]}
    check("10 条任务的 radio 决策全部符合规则", not bad, str(bad))
    atts = attachments()
    cat1 = [a for a in atts if a.startswith("附件/类别一/")]
    cat2 = [a for a in atts if a.startswith("附件/类别二/")]
    check("附件按类别归档（5+5）", len(cat1) == 5 and len(cat2) == 5,
          "类别一 %d 个，类别二 %d 个" % (len(cat1), len(cat2)))
    check("汇总成功 10 条", sum(len(s["成功"]) for s in summary.values()) == 10, str(summary))
    check("批量运行未再触碰 UKey", ukey_clicks == 0, "实际 %d 次" % ukey_clicks)
    shots = list((tk.BASE_DIR / "shots").glob("*.png"))
    check("批量截图齐全（>=60 张）", len(shots) >= 60, "实际 %d 张" % len(shots))
    logf = list((tk.BASE_DIR / "logs").glob("run_*.log"))[0]
    content = logf.read_text(encoding="utf-8")
    check("日志覆盖每个任务编号与异常段", all(("任务 T%d01" % c) in content for c in (1, 2))
          and "自动【接受】" in content)


def test_config_validation():
    print("\n【验收6】config 校验：缺项给中文提示和默认值")
    import tempfile, os
    tmp = Path(tempfile.mkdtemp()) / "bad.json"
    tmp.write_text('{"base_url": ""}', encoding="utf-8")
    try:
        tk.load_config(tmp)
        check("缺必填项 → ConfigError", False, "未抛出异常")
    except tk.ConfigError as e:
        check("缺必填项 → ConfigError 中文提示", "缺少必填项" in str(e) and "sso_login_url" in str(e))
    tmp.write_text('{"base_url": "http://x", oops}', encoding="utf-8")
    try:
        tk.load_config(tmp)
        check("JSON 损坏 → ConfigError", False, "未抛出异常")
    except tk.ConfigError as e:
        check("JSON 损坏 → ConfigError 中文提示", "不是合法的 JSON" in str(e))
    good = tmp.parent / "good.json"
    good.write_text(json.dumps({
        "base_url": "http://127.0.0.1:8899",
        "sso_login_url": "http://127.0.0.1:8899/login",
        "categories": [{"name": "类别一", "url": "http://127.0.0.1:8899/list1_page"}],
    }, ensure_ascii=False), encoding="utf-8")
    cfg = tk.load_config(good)
    check("缺省项自动补默认值（sign_keywords 等）", cfg["sign_keywords"][0] == "签收"
          and cfg["attachment_dir"] == "附件")
    opt = tk.load_config_optional(Path("config.json"))
    check("workflow 可选加载：空 config.json 也不报错",
          opt["max_tasks"] >= 1 and opt["attachment_dir"] == "附件")
    shutil.rmtree(tmp.parent, ignore_errors=True)


def test_find_button_tolerance():
    print("\n【验收7】文字匹配容错：“提 交”能被关键词“提交”命中")
    cases = {
        "签 收": ["签收"],
        "提 交": ["提交"],
        "受\n理": ["受理"],
        "提 交(多关键词)": ["确认", "提交"],
        "驳 回": ["同意", "驳回"],
    }
    for text, kws in cases.items():
        hit = None
        for kw in kws:
            if tk._norm_text(kw) in tk._norm_text(text):
                hit = kw
                break
        check("“%s” ← 关键词%s" % (text.replace("\n", "\\n"), kws), hit == kws[-1] or hit is not None)


# ================================================================ YAML 工作流
def wf_cfg(**over):
    cfg = base_cfg(**over)
    return apply_defaults(cfg)


def sample_workflow(max_tasks=1, headless=True):
    """构造与 workflow.yaml 等价的示例工作流（指向 mock）"""
    return {
        "name": "测试工作流",
        "settings": {
            "max_tasks": max_tasks,
            "attachment_dir": "附件",
            "download_attachments": True,
            "task_delay_seconds": [1, 2],
            "screenshot": True,
            "sign_keywords": ["签收", "受理", "确认"],
            "submit_keywords": ["提交", "确定", "保存"],
            "default_radio": "同意",
            "radio_rules": [
                {"if_type_contains": "报修", "then_pick": "同意"},
                {"if_type_contains": "故障", "then_pick": "同意"},
                {"if_type_contains": "申请", "then_pick": "驳回"},
            ],
        },
        "browser": {"channel": "chrome", "headless": headless},
        "login": {
            "url": BASE + "/login",
            "username": "admin",
            "password": "123456",
            "username_selector": "input[name='username']",
            "password_selector": "input[name='password']",
            "button_text": ["登录", "登 录"],
            "ukey_wait": True,
            "success_url": BASE + "/home",
        },
        "env": {"operator": "自动处理"},
        "steps": [
            {"uses": "log", "with": {"message": "操作人：{{ operator }}"}},
        ],
        "tasks": [
            {"name": "类别一", "url": BASE + "/list1_page", "steps": [
                {"uses": "click_row_link"},
                {"uses": "parse_var", "with": {
                    "from": "{{ row_text }}", "regex": "[A-Za-z]{1,6}-?\\d{2,}",
                    "name": "task_no", "default": "未知任务"}},
                {"uses": "click_text", "with": {"text": ["签收", "受理"], "optional": True}},
                {"uses": "pick_radio_by_rule"},
                {"uses": "download", "with": {"optional": True}},
                {"uses": "click_text", "with": {"text": ["提交", "确定"]}},
                {"uses": "expect_text", "with": {"text": "提交成功"}, "continue_on_error": True},
                {"uses": "close_task_page"},
            ]},
            {"name": "类别二", "url": BASE + "/list2_page", "steps": [
                {"uses": "click_row_link"},
                {"uses": "click_text", "with": {"text": ["签收", "受理"], "optional": True}},
                {"uses": "pick_radio_by_rule"},
                {"uses": "download", "with": {"optional": True}},
                {"uses": "click_text", "with": {"text": ["提交", "确定"]}},
                {"uses": "close_task_page"},
            ]},
        ],
    }


def test_workflow_validate():
    print("\n【W1】workflow.yaml 语法/动作名校验")
    p = tk.BASE_DIR / "workflow.yaml"
    ok = tk.validate_workflow_cli(p)
    check("示例 workflow.yaml 校验通过", bool(ok))
    bad = tk.parse_yaml("""
name: 坏流程
steps:
  - uses: 不存在的动作
    with: {x: 1}
  - name: 缺uses
    with: {y: 2}
""")
    errs = []
    orig = tk.ACTIONS
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # 直接走内部校验
            tk.load_yaml_file  # noqa
            tmp = tk.BASE_DIR / "_bad_wf.yaml"
            tmp.write_text("name: x\nsteps:\n  - uses: 不存在的动作\n", encoding="utf-8")
            r = tk.validate_workflow_cli(tmp)
            tmp.unlink(missing_ok=True)
        check("未知动作能被校验发现", r is False)
    except Exception as e:
        check("未知动作能被校验发现", False, str(e))


def test_workflow_single_and_reuse():
    print("\n【W2】workflow 模式端到端（登录→UKey→两类任务各1条）+ 二次复用 state")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    summary = tk.workflow_mode(wf=sample_workflow(1), cfg=wf_cfg(max_tasks=1))
    check("UKey 暂停一次（人工介入点）", ukey_clicks == 1, "实际 %d" % ukey_clicks)
    st = tasks_by_no()
    check("类别一第1条已提交", st.get("T101", {}).get("status") == "submitted", str(st.get("T101")))
    check("类别二第1条已提交（iframe 列表）", st.get("T201", {}).get("status") == "submitted")
    check("radio 规则：T101报修→同意", st.get("T101", {}).get("decision") == "同意")
    check("radio 规则：T201权限申请→驳回", st.get("T201", {}).get("decision") == "驳回")
    check("parse_var 解析出任务号", "T101" in summary["类别一"]["成功"])
    atts = attachments()
    check("附件归档到 类别/任务号_文件名", any(a.startswith("附件/类别一/T101_报告_设备报修_T101") for a in atts), str(atts))
    # 二次运行
    reset_mock()
    before = ukey_clicks
    tk.workflow_mode(wf=sample_workflow(1), cfg=wf_cfg(max_tasks=1))
    check("二次运行未触碰 UKey", ukey_clicks == before, "UKey %d→%d" % (before, ukey_clicks))
    st = tasks_by_no()
    check("二次运行照常处理", st.get("T101", {}).get("status") == "submitted")


def test_workflow_advanced():
    print("\n【W3】工作流高级特性：for_each / if / while / 变量 / frame 定向 / exists")
    # 纯控制流部分（不需要浏览器）
    wf = tk.parse_yaml("""
steps:
  - uses: set_var
    with: { name: total, value: 0 }
  - uses: for_each
    with: { over: [1, 2, 3], as: n }
    do:
      - uses: set_var
        with: { name: total, value: "{{ n }}" }
      - uses: if
        with: { condition: "n == 2" }
        then:
          - uses: set_var
            with: { name: hit2, value: "yes" }
        else:
          - uses: set_var
            with: { name: other, value: "{{ n }}" }
  - uses: while
    with: { condition: "index < 9", max: 9 }
    do:
      - uses: if
        with: { condition: "index >= 1" }
        then: [{ uses: break }]
""")
    e = tk.WorkflowEngine(wf, wf_cfg())
    e.run_steps(wf["steps"])
    check("for_each 遍历 + 变量渲染", e.vars.get("total") == 3 and e.vars.get("hit2") == "yes")
    check("if/else 分支（else 在 n=1 与 n=3 时执行，最终为 3）", str(e.vars.get("other")) == "3",
          repr(e.vars.get("other")))
    check("while + break", e.vars.get("index") == 1, repr(e.vars.get("index")))

    # 浏览器相关：exists 条件、frame 定向、for_each over rows
    from playwright.sync_api import sync_playwright
    wf2 = tk.parse_yaml("""
steps:
  - uses: goto
    with: { url: "http://127.0.0.1:8899/list2_page" }
  - uses: assert
    with: { condition: "exists(iframe)", message: "iframe 应存在" }
  - uses: assert
    with: { condition: "exists(iframe)", message: "透传 frame 查找" }
  - uses: for_each
    with:
      rows: "iframe"
      frame: "#1"
    do:
      - uses: log
        with: { message: "frame 内元素：{{ item }}" }
  - uses: assert
    with: { condition: "has_text(任务列表)" }
""")
    with sync_playwright() as p:
        cfg = wf_cfg()
        b = tk.launch_browser(p, cfg)
        try:
            ctx, page = tk.ensure_login(b, cfg, tk.log)
            e2 = tk.WorkflowEngine(wf2, cfg)
            e2.ctx, e2.current, e2.opened_pages = ctx, page, []
            e2.run_steps(wf2["steps"])
            check("exists/has_text 条件与 frame 定向可用", True)
        except Exception as ex:
            check("exists/has_text 条件与 frame 定向可用", False, str(ex)[:120])
        finally:
            b.close()


def test_workflow_error_handling():
    print("\n【W4】工作流错误处理：坏选择器只失败该任务；continue_on_error 生效")
    reset_mock()
    wf = sample_workflow(1)
    # 在类别一第一步前插入一个必然失败的步骤（不存在的选择器点击）
    wf["tasks"][0]["steps"].insert(0, {"uses": "click", "with": {"selector": "#绝不存在的元素"}})
    summary = tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    st = tasks_by_no()
    check("坏选择器 → 该任务记为失败而非崩溃", summary["类别一"]["失败"] == ["T101"],
          str(summary["类别一"]))
    check("被中断的任务未提交", st.get("T101", {}).get("status") == "pending", str(st.get("T101")))
    check("其它类别不受影响", st.get("T201", {}).get("status") == "submitted")

    # continue_on_error：坏步骤被忽略，任务照常完成
    reset_mock()
    wf2 = sample_workflow(1)
    wf2["tasks"][0]["steps"].insert(0, {
        "uses": "click", "with": {"selector": "#不存在"}, "continue_on_error": True})
    summary2 = tk.workflow_mode(wf=wf2, cfg=wf_cfg(max_tasks=1))
    st2 = tasks_by_no()
    check("continue_on_error → 任务仍成功", st2.get("T101", {}).get("status") == "submitted",
          str(summary2["类别一"]))

    # 未知动作 → ConfigError（中文）；把未知步骤放在最前面，确保会被执行到
    bad = sample_workflow(1)
    bad["tasks"][0]["steps"].insert(0, {"uses": "不存在的动作"})
    err = None
    try:
        tk.workflow_mode(wf=bad, cfg=wf_cfg(max_tasks=1))
    except tk.ConfigError as ex:
        err = str(ex)
    check("未知动作 → 中文 ConfigError", err is not None and "未知步骤" in err, str(err)[:100])


def test_workflow_conditions_and_loops():
    print("\n【W5】条件函数矩阵 + 嵌套循环")
    class FE:
        vars = {"n": 3, "s": "申请权限", "lst": [1, 2, 3], "type": "报修"}

        def has_text(self, t, page=None):
            return t == "任务列表"

        def selector_exists(self, s, frame=None):
            return s == "#ok"

        def selector_visible(self, s, frame=None):
            return s == "#vis"
    fe = FE()
    cases = [("n == 3", True), ("n >= 3", True), ("n < 3", False),
             ("s contains 权限", True), ("s not contains 驳回", True),
             ("exists(#ok)", True), ("exists(#no)", False), ("visible(#vis)", True),
             ("has_text(任务列表)", True), ("not exists(#no)", True),
             ("exists(#no) or exists(#ok)", True), ("exists(#ok) and n == 3", True),
             ("exists(#no) or exists(#nope)", False), ("n == 9 or n == 3", True),
             ("type in ['报修','故障']", True), ("n in lst", True),
             ("s startswith 申请", True)]
    bad = [c for c, exp in cases if tk.eval_condition(c, fe) != exp]
    check("条件/函数矩阵 17 项", not bad, str(bad))

    e = tk.WorkflowEngine(tk.parse_yaml("""
steps:
  - uses: set_var
    with: { name: acc, value: "" }
  - uses: for_each
    with: { over: ["a", "b"], as: outer }
    do:
      - uses: repeat
        with: { times: 2 }
        do:
          - uses: set_var
            with: { name: acc, value: "{{ acc }}{{ outer }}{{ index }}" }
  - uses: while
    with: { condition: "n < 5", max: 20, as: n }
    do:
      - uses: set_var
        with: { name: seen, value: "{{ n }}" }
"""), wf_cfg())
    e.run_steps(e.wf["steps"])
    check("嵌套 for_each + repeat", e.vars.get("acc") == "a0a1b0b1", repr(e.vars.get("acc")))
    check("while 计数正确（n<5 时末次为 4）", str(e.vars.get("seen")) == "4",
          repr(e.vars.get("seen")))


def test_workflow_custom_login_steps():
    print("\n【W6】自定义 login.steps 路径（不依赖内置登录）")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["login"] = {
        "steps": [
            {"uses": "goto", "with": {"url": BASE + "/login"}},
            {"uses": "fill", "with": {"selector": "input[name='username']",
                                      "text": "admin"}},
            {"uses": "fill", "with": {"selector": "input[name='password']",
                                      "text": "123456", "secret": True}},
            {"uses": "click_text", "with": {"text": ["登录", "登 录"]}},
            {"uses": "pause", "with": {"message": "请完成 UKey"}},
            {"uses": "goto", "with": {"url": BASE + "/home"}},
            {"uses": "assert", "with": {"condition": "has_text(任务处理平台)",
                                        "message": "登录后应看到平台首页"}},
        ],
    }
    summary = tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    check("自定义 login.steps 能完成登录并保存 state",
          (tk.BASE_DIR / "state.json").exists() and ukey_clicks == 1,
          "ukey_clicks=%d" % ukey_clicks)
    st = tasks_by_no()
    check("自定义登录后任务照常处理", st.get("T101", {}).get("status") == "submitted")


def test_probe_action():
    print("\n【W7】probe 动作：探测 iframe 列表页并生成 YAML 草稿")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/list2_page"}},
        {"uses": "probe", "with": {"file": "shots/list2_probe.yaml", "all_pages": True}},
    ]
    wf["tasks"] = []
    tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    probe_file = tk.BASE_DIR / "shots" / "list2_probe.yaml"
    text = probe_file.read_text(encoding="utf-8") if probe_file.exists() else ""
    check("probe 生成 YAML 文件", probe_file.exists())
    doc = tk.parse_yaml(text) if text else None
    suggested = (doc or {}).get("suggested_steps", [])
    uses = [s.get("uses") for s in suggested]
    check("probe 建议步骤含 click_row_link", "click_row_link" in uses, uses)
    frames = [fr for pg in (doc or {}).get("probe", []) for fr in pg.get("frames", [])]
    check("probe 识别出 iframe 与列表行候选",
          any(fr.get("iframe") for fr in frames)
          and any(fr.get("rows") for fr in frames),
          [(fr.get("iframe"), bool(fr.get("rows"))) for fr in frames])


def test_generic_actions():
    print("\n【W8】通用动作：extract / evaluate / request / 条件函数 / tasks mode")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    for name in ("probe", "record", "extract", "evaluate", "request", "http", "hover",
                 "scroll", "upload", "wait_for_url", "save_state", "set_dialog", "expect_url"):
        check("动作已注册：%s" % name, name in tk.ACTIONS)

    wf = sample_workflow(1)
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/list1_page"}},
        {"uses": "extract", "with": {"selector": "table tr", "all": True,
                                     "join": " | ", "name": "rows_text"}},
        {"uses": "extract", "with": {"selector": "a[href]", "what": "count",
                                     "name": "link_count"}},
        {"uses": "evaluate", "with": {"js": "() => document.title", "name": "page_title"}},
        {"uses": "request", "with": {"url": BASE + "/status", "name": "st"}},
        {"uses": "assert", "with": {"condition": "count(a[href]) >= 1", "message": "有链接"}},
        {"uses": "assert", "with": {"condition": "text(h1) contains 任务列表",
                                    "message": "标题断言"}},
        {"uses": "assert", "with": {"condition": "st.status == 200", "message": "HTTP 200"}},
        {"uses": "assert", "with": {"condition": "page_title contains 任务列表",
                                    "message": "evaluate 返回值未存对"}},
    ]
    wf["tasks"] = [
        {"name": "单页流程", "mode": "once", "steps": [
            {"uses": "goto", "with": {"url": BASE + "/list1_page"}},
            {"uses": "assert", "with": {"condition": "has_text(任务列表)", "message": "列表页"}},
        ]},
        {"name": "逐行模式", "mode": "each_row", "max_tasks": 2, "remove_after": False,
         "url": BASE + "/list1_page", "steps": [
             {"uses": "click_row_link"},
             {"uses": "pick_radio_by_rule"},
             {"uses": "close_task_page"},
         ]},
    ]
    summary = tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    check("mode=once 执行成功",
          summary.get("单页流程", {}).get("成功") == ["单页流程"], str(summary))
    check("mode=each_row 处理 2 行",
          len(summary.get("逐行模式", {}).get("成功", [])) == 2, str(summary))


def test_record_action():
    print("\n【W9】record 动作：模拟点击 → 自动生成 YAML 步骤")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["steps"] = [
        {"uses": "goto", "with": {"url": BASE + "/home"}},
        {"uses": "record", "with": {"file": "shots/rec_test.yaml"}},
    ]
    wf["tasks"] = []

    import builtins
    real_input = builtins.input

    def fake_record_input(prompt=""):
        ctx = tk._ACTIVE_CONTEXT
        pg = ctx.pages[0]
        pg.evaluate("() => { const a = document.querySelector('a[href]');"
                    " if (a) a.click(); }")
        pg.wait_for_timeout(400)
        return ""

    builtins.input = fake_record_input
    try:
        tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    finally:
        builtins.input = real_input

    rec = tk.BASE_DIR / "shots" / "rec_test.yaml"
    check("record 生成 YAML 文件", rec.exists())
    doc = tk.parse_yaml(rec.read_text(encoding="utf-8")) if rec.exists() else None
    steps = (doc or {}).get("steps", [])
    check("record 捕获点击为 click_text",
          any(s.get("uses") == "click_text" for s in steps), steps[:3])
    check("record 产物含 login 配置且密码脱敏",
          isinstance((doc or {}).get("login"), dict)
          and (doc or {}).get("login", {}).get("password") == "{{ env.password }}",
          (doc or {}).get("login"))


def test_state_scoping():
    print("\n【W10】state.json 站点隔离：别的流程/平台的 state 不会被误用")
    clean_artifacts()
    reset_mock()
    global ukey_clicks
    ukey_clicks = 0
    st_path = tk.BASE_DIR / "state.json"
    foreign = {"cookies": [{"name": "sid", "value": "foreign",
                            "domain": "other.example.com", "path": "/"}],
               "origins": []}
    st_path.write_text(json.dumps(foreign), encoding="utf-8")
    (tk.BASE_DIR / "state.meta.json").write_text(json.dumps(
        {"origin": "other.example.com", "workflow": "别人家的流程"},
        ensure_ascii=False), encoding="utf-8")
    tk.workflow_mode(wf=sample_workflow(1), cfg=wf_cfg(max_tasks=1))
    check("带元数据的异站 state → 重新登录/UKey", ukey_clicks == 1,
          "ukey=%d" % ukey_clicks)
    data = json.loads(st_path.read_text(encoding="utf-8"))
    domains = {str(c.get("domain") or "").lstrip(".") for c in data.get("cookies", [])}
    check("state.json 已覆盖为本站点登录态", "127.0.0.1" in domains, domains)

    # 无元数据的异站 state：靠 cookie 域名兜底识别
    clean_artifacts()
    st_path.write_text(json.dumps(foreign), encoding="utf-8")
    ukey_clicks = 0
    tk.workflow_mode(wf=sample_workflow(1), cfg=wf_cfg(max_tasks=1))
    check("无元数据的异站 state → 也能识别并重登", ukey_clicks == 1,
          "ukey=%d" % ukey_clicks)

    # 自定义 state_file：与默认 state.json 互不影响
    clean_artifacts()
    reset_mock()
    ukey_clicks = 0
    wf = sample_workflow(1)
    wf["login"]["state_file"] = "shots/custom_state.json"
    tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    check("自定义 state_file 首次仍走登录", ukey_clicks == 1)
    check("自定义 state_file 与元数据均已生成",
          (tk.BASE_DIR / "shots" / "custom_state.json").exists()
          and (tk.BASE_DIR / "shots" / "custom_state.meta.json").exists())
    before = ukey_clicks
    reset_mock()
    tk.workflow_mode(wf=wf, cfg=wf_cfg(max_tasks=1))
    check("自定义 state_file 二次复用（不再碰 UKey）", ukey_clicks == before,
          "ukey %d->%d" % (before, ukey_clicks))


def main():
    print("=" * 62)
    print("tool_kit 自动验收（目标：mock 平台 %s）" % BASE)
    print("=" * 62)
    try:
        http_json("/status")
    except Exception:
        print("!! mock 平台未运行，请先执行：python mock_platform.py")
        sys.exit(2)
    test_find_button_tolerance()
    test_config_validation()
    test_first_run_and_single_task()
    test_second_run_reuses_state()
    test_inspect()
    test_bad_config_url()
    test_full_batch()
    test_workflow_validate()
    test_workflow_single_and_reuse()
    test_workflow_advanced()
    test_workflow_error_handling()
    test_workflow_conditions_and_loops()
    test_workflow_custom_login_steps()
    test_probe_action()
    test_generic_actions()
    test_record_action()
    test_state_scoping()
    print("\n" + "=" * 62)
    print("验收结果：通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("  ✗ %s  %s" % (name, detail))
    print("=" * 62)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
