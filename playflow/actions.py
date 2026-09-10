# -*- coding: utf-8 -*-
"""内置动作库：每个动作用 @action("名字") 注册，签名 (engine, params, step)。"""
import json
import re
import time
from pathlib import Path

from .browser import host_of, pause_for_manual, safe_goto, save_state
from .conditions import eval_condition
from .dom import (download as dom_download, find_button, input_selector, pick_radio)
from .errors import LoopBreak, LoopContinue, StepError
from .probe import collect_probe, report_probe, write_probe_file
from .recorder import events_to_steps, install as recorder_install
from .recorder import stop as recorder_stop, write_record_file
from .registry import ACTIONS, action
from .template import as_bool
from .utils import anchor, base_dir, format_text, sanitize_filename, shot


def ensure_registered():
    """内置动作在导入本模块时完成注册；引擎显式调用以表明依赖。"""
    return len(ACTIONS)


# ---------------------------------------------------------------- 变量 / 基础

@action("log")
def _a_log(e, p, s):
    e.logf(str(p.get("message", "")), echo=True)
    return None


@action("set_var")
def _a_set_var(e, p, s):
    name = p.get("name") or p.get("var")
    if not name:
        raise StepError("set_var 需要 name")
    e.vars[str(name)] = p.get("value")
    e.logf("变量 %s = %r" % (name, p.get("value")))
    return {str(name): p.get("value")}


@action("parse_var")
def _a_parse_var(e, p, s):
    """用正则从文本抽取变量；from 省略时取 selector 元素文本或 row_text。"""
    text = p.get("from")
    if text is None:
        sel = p.get("selector")
        if sel:
            _, loc = e.locator(sel, p.get("frame"))
            text = loc.first.inner_text() if loc.count() else ""
        else:
            text = e.vars.get("row_text", "")
    match = re.search(str(p.get("regex", "")), str(text or ""))
    val = match.group(int(p.get("group", 0))) if match else p.get("default", "")
    name = str(p.get("name") or "parsed")
    e.vars[name] = val
    e.logf("parse_var %s = %r" % (name, val))
    return {name: val}


@action("write_file")
def _a_write_file(e, p, s):
    """把文本或对象写入文件（相对路径锚定到 base_dir）；append: true 追加。"""
    path = p.get("path") or p.get("file")
    if not path:
        raise StepError("write_file 需要 path（或 file）")
    content = p.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    path = anchor(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = str(p.get("encoding") or "utf-8")
    if as_bool(p.get("append", False)):
        with open(path, "a", encoding=encoding) as f:
            f.write(content)
    else:
        path.write_text(content, encoding=encoding)
    e.logf("已写入文件：%s（%d 字符）" % (path, len(content)), echo=True)
    return str(path)


# ---------------------------------------------------------------- 导航

@action("goto")
def _a_goto(e, p, s):
    url = p.get("url")
    if not url:
        raise StepError("goto 需要 url")
    page = e.current
    if p.get("new_tab"):
        page = e.ctx.new_page()
        e.opened_pages.append(page)
        e.current = page
    e.logf("打开：%s" % url, echo=True)
    resp = safe_goto(page, str(url), "页面",
                     wait_until=p.get("wait_until", "domcontentloaded"),
                     timeout=int(p.get("timeout", 25000)),
                     settle=as_bool(p.get("settle", True)))
    e.snap("goto")
    return resp


@action("wait_for_url")
def _a_wait_for_url(e, p, s):
    pattern = p.get("pattern") or p.get("url")
    if not pattern:
        raise StepError("wait_for_url 需要 pattern（如 **/home 或正则）")
    if p.get("regex"):
        pattern = re.compile(str(pattern))
    try:
        e.current.wait_for_url(pattern, timeout=e.timeout(p))
    except Exception as ex:
        raise StepError("等待 URL 变化超时：%s（%s）" % (pattern, str(ex).split("\n")[0]))
    e.logf("URL 已变为：%s" % e.current.url)
    return e.current.url


@action("reload")
def _a_reload(e, p, s):
    e.current.reload(wait_until=p.get("wait_until", "domcontentloaded"),
                     timeout=int(p.get("timeout", 25000)))
    e.snap("reload")
    return None


@action("save_state")
def _a_save_state(e, p, s):
    """把当前登录态持久化，默认沿用本流程的 state 文件。"""
    path = anchor(p.get("path") or e.state_path or "state.json")
    save_state(e.ctx, path, {"origin": host_of(e.current.url),
                             "workflow": (e.wf.get("name") or "")})
    e.logf("登录态已保存：%s" % path, echo=True)
    return str(path)


@action("set_dialog")
def _a_set_dialog(e, p, s):
    """设置原生弹窗策略：accept（默认）/ dismiss；prompt 可配 text。"""
    mode = str(p.get("mode") or p.get("action") or "").lower()
    if mode:
        if mode not in ("accept", "dismiss", "reject", "cancel"):
            raise StepError("set_dialog.mode 只能是 accept 或 dismiss")
        e.settings["dialog"] = \
            "dismiss" if mode in ("dismiss", "reject", "cancel") else "accept"
    if "text" in p:
        e.settings["dialog_text"] = p.get("text")
    e.logf("弹窗策略：%s" % e.settings.get("dialog", "accept"))
    return e.settings.get("dialog", "accept")


@action("wait")
def _a_wait(e, p, s):
    ms = p.get("ms")
    if ms is None:
        ms = float(p.get("seconds", p.get("s", 1)) or 0) * 1000
    e.current.wait_for_timeout(int(ms))
    return None


@action("wait_for")
def _a_wait_for(e, p, s):
    sel = p.get("selector")
    if not sel:
        raise StepError("wait_for 需要 selector")
    state = p.get("state", "visible")
    timeout = e.timeout(p)
    fr = e.resolve_frame(p.get("frame"))
    targets = [fr] if fr is not None else e.frames_of()
    last = None
    for f in targets:
        try:
            f.wait_for_selector(sel, state=state, timeout=timeout)
            e.logf("wait_for 命中：%s" % sel)
            return True
        except Exception as ex:
            last = ex
    raise StepError("等待元素超时：%s（%s）" % (sel, str(last).split("\n")[0]))


# ---------------------------------------------------------------- 点击 / 表单

@action("click")
def _a_click(e, p, s):
    sel = p.get("selector")
    if not sel:
        raise StepError("click 需要 selector")
    _, loc = e.locator(sel, p.get("frame"))
    nth = int(p.get("nth", 0) or 0)
    if loc.count() <= nth:
        raise StepError("click 找不到元素（第 %d 个）：%s" % (nth, sel))
    target = loc.nth(nth)
    e.logf("点击元素：%s" % sel, echo=True)
    if p.get("capture_new_page"):
        return e.click_capture(target, p)
    target.click(timeout=e.timeout(p))
    e.wait_after_nav(p)
    e.snap("click")
    return None


@action("click_text")
def _a_click_text(e, p, s):
    """按文字匹配点击：容错空格/换行、遍历 iframe、穿透开放 Shadow DOM。"""
    kws = p.get("text") or p.get("keywords")
    if kws is None:
        raise StepError("click_text 需要 text（关键词或关键词列表）")
    if isinstance(kws, str):
        kws = [kws]
    e.logf("文字匹配点击：%s" % kws)
    hit = find_button(e.current, [str(k) for k in kws])
    if not hit:
        if p.get("optional"):
            e.logf("未找到按钮（optional，跳过）：%s" % kws)
            return False
        raise StepError("找不到按钮：关键词 %s（当前页 %s）" % (kws, e.current.url))
    kw, _fr, loc, brief = hit
    e.logf("命中关键词“%s”，按钮文字“%s”" % (kw, brief.get("text", "")), echo=True)
    if p.get("capture_new_page"):
        return e.click_capture(loc, p)
    loc.click(timeout=e.timeout(p))
    e.wait_after_nav(p)
    e.snap("click_text")
    return brief.get("text", "")


@action("click_row_link")
def _a_click_row_link(e, p, s):
    """点击当前列表行内的任务链接；自动兼容新标签页/当前页跳转。"""
    row = e.row
    if not row:
        raise StepError("click_row_link 只能在 tasks 列表循环中使用")
    e.logf("点击任务链接（行文本：%s）" % format_text(row["text"], 40), echo=True)
    return e.click_capture(row["link"], p, source_page=e.list_page)


@action("fill")
def _a_fill(e, p, s):
    sel = p.get("selector")
    if not sel:
        for key in ("placeholder", "name", "id"):
            if p.get(key):
                sel = input_selector(p[key], key)
                break
    if not sel:
        raise StepError("fill 需要 selector（或 placeholder / name / id）")
    text = p.get("text", p.get("value", ""))
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise StepError("fill 找不到输入框：%s" % sel)
    target = loc.nth(int(p.get("nth", 0) or 0))
    if as_bool(p.get("clear", True)):
        target.fill("")
    target.fill(str(text))
    e.logf("填写 %s ← %s" % (sel, "***" if p.get("secret") else text))
    return text


@action("check")
def _a_check(e, p, s):
    sel = p.get("selector")
    if not sel:
        raise StepError("check 需要 selector")
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise StepError("check 找不到元素：%s" % sel)
    item = loc.nth(int(p.get("nth", 0) or 0))
    try:
        item.check(timeout=e.timeout(p, 5000))
    except Exception:
        item.evaluate("el => el.click()")
    return True


@action("pick_radio")
def _a_pick_radio(e, p, s):
    """按 value/关联 label/父级文本选择单选框。"""
    text = p.get("text", p.get("value", ""))
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise StepError("pick_radio 找不到元素：%s" % p["selector"])
        item = loc.nth(int(p.get("nth", 0) or 0))
        try:
            item.check(timeout=e.timeout(p, 5000))
        except Exception:
            item.evaluate("el => el.click()")
        e.logf("已勾选单选框（选择器）：%s" % p["selector"])
        return True
    if not text:
        raise StepError("pick_radio 需要 text 或 selector")
    if not pick_radio(e.current, str(text)):
        raise StepError("找不到匹配的单选框：%s" % text)
    e.logf("已勾选单选框：%s" % text, echo=True)
    return True


@action("select_option")
def _a_select_option(e, p, s):
    sel = p.get("selector")
    if not sel:
        raise StepError("select_option 需要 selector")
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise StepError("select_option 找不到下拉框：%s" % sel)
    target = loc.first
    if p.get("label") is not None:
        target.select_option(label=str(p["label"]))
    elif p.get("value") is not None:
        target.select_option(value=str(p["value"]))
    elif p.get("index") is not None:
        target.select_option(index=int(p["index"]))
    else:
        target.select_option(str(p.get("option", "")))
    e.logf("下拉选择：%s" % (p.get("label") or p.get("value") or p.get("option")))
    return True


@action("press")
def _a_press(e, p, s):
    key = p.get("key")
    if not key:
        raise StepError("press 需要 key，如 Enter / Escape")
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        loc.first.press(str(key))
    else:
        e.current.keyboard.press(str(key))
    return None


@action("hover")
def _a_hover(e, p, s):
    """悬停：selector 精确命中，或用 text 文字匹配。"""
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise StepError("hover 找不到元素：%s" % p["selector"])
        loc.nth(int(p.get("nth", 0) or 0)).hover()
    elif p.get("text") is not None:
        kws = p["text"] if isinstance(p["text"], list) else [p["text"]]
        hit = find_button(e.current, [str(k) for k in kws])
        if not hit:
            if p.get("optional"):
                return False
            raise StepError("hover 找不到文字：%s" % kws)
        hit[2].hover()
    else:
        raise StepError("hover 需要 selector 或 text")
    return True


@action("scroll")
def _a_scroll(e, p, s):
    """滚动：selector（滚到元素）/ by（像素 [x, y]）/ bottom: true。"""
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise StepError("scroll 找不到元素：%s" % p["selector"])
        loc.first.scroll_into_view_if_needed()
    elif p.get("bottom"):
        e.current.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    else:
        by = p.get("by") or [0, 600]
        x, y = (by if isinstance(by, list) else [0, by])
        e.current.mouse.wheel(float(x), float(y))
    try:
        e.current.wait_for_timeout(int(p.get("ms", 300)))
    except Exception:
        pass
    return True


@action("upload")
def _a_upload(e, p, s):
    """上传文件：file/files 为路径（相对路径锚定到 base_dir）。"""
    sel = p.get("selector")
    if not sel:
        raise StepError("upload 需要 selector（input[type=file]）")
    files = p.get("file", p.get("files"))
    if files is None:
        raise StepError("upload 需要 file（文件路径，可为数组）")
    if isinstance(files, str):
        files = [files]
    resolved = [str(anchor(f)) for f in files]
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise StepError("upload 找不到文件输入框：%s" % sel)
    loc.first.set_input_files(resolved)
    e.logf("已上传 %d 个文件：%s" % (len(resolved), "、".join(resolved)), echo=True)
    return resolved


# ---------------------------------------------------------------- 取值 / 请求

@action("extract")
def _a_extract(e, p, s):
    """从页面取值存变量。

    what: text(默认)|text_content|html|value|attr|count|href；attr 时用 attr: 属性名；
    all: true 取列表（可用 join 拼接）；nth 指定第几个命中（0 起始）；
    until: 可选条件，不满足则按 interval 轮询重试（配合 timeout），如 until: "value != ''"。
    """
    sel = p.get("selector")
    name = str(p.get("name") or p.get("var") or "extracted")
    what = str(p.get("what") or ("attr" if p.get("attr") else "text")).lower()
    nth = p.get("nth")
    if not sel and what != "count":
        raise StepError("extract 需要 selector")

    def read(el):
        if what == "html":
            return el.inner_html()
        if what == "value":
            return el.input_value()
        if what == "text_content":
            return el.text_content()
        if what == "attr":
            return el.get_attribute(str(p.get("attr") or p.get("attribute") or ""))
        if what == "href":
            return el.get_attribute("href")
        try:
            text = el.inner_text()
        except Exception:
            text = ""
        if not str(text or "").strip():
            # 隐藏元素的 innerText 可能为空，退回原始 textContent
            try:
                text = el.text_content() or ""
            except Exception:
                pass
        return text

    try:
        _, loc = e.locator(sel, p.get("frame"))
    except Exception:
        loc = None

    def count():
        try:
            return loc.count() if loc is not None else 0
        except Exception:
            return 0

    def pick(cnt):
        if what == "count":
            return cnt
        if p.get("all"):
            vals = [read(loc.nth(i)) for i in range(cnt)]
            return p.get("join", "").join(str(v) for v in vals) if p.get("join") else vals
        if nth is not None:
            idx = int(nth)
            return read(loc.nth(idx)) if 0 <= idx < cnt else p.get("default", "")
        if cnt == 0:
            return p.get("default", "")
        return read(loc.first)

    until = p.get("until")
    deadline = time.time() + e.timeout(p, 10000) / 1000.0 if until is not None else None
    n, val = 0, None
    while True:
        n = count()
        val = pick(n)
        e.vars[name] = val
        if until is None or eval_condition(until, e):
            break
        if time.time() >= deadline:
            e.logf("extract %s：until 条件未满足已超时（当前值 %r）" % (name, val))
            break
        try:
            e.current.wait_for_timeout(int(p.get("interval", 500)))
        except Exception:
            time.sleep(0.5)
    e.logf("extract %s（%s，命中 %d）= %s" % (name, what, n, format_text(val, 60)))
    return {name: val}


@action("evaluate")
def _a_evaluate(e, p, s):
    """执行页面 JS 并把返回值存变量（js/code/script 三选一）。"""
    js = p.get("js") or p.get("code") or p.get("script")
    if js is None:
        raise StepError("evaluate 需要 js（例如 () => document.title）")
    fr = e.resolve_frame(p.get("frame"))
    target = fr if fr is not None else e.current.main_frame
    val = target.evaluate(str(js))
    if p.get("name"):
        e.vars[str(p["name"])] = val
    shown = json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
    e.logf("evaluate → %s" % format_text(shown, 80))
    return val


@action("request")
def _a_request(e, p, s):
    """发 HTTP 请求并把结果存变量：status/text/json/headers；output 可保存响应体。"""
    try:
        import requests
    except ImportError:
        raise StepError("未安装 requests，无法使用 request 动作（pip install requests）")
    url = p.get("url")
    if not url:
        raise StepError("request 需要 url")
    method = str(p.get("method", "GET")).upper()
    try:
        resp = requests.request(
            method, str(url),
            params=p.get("params"), headers=p.get("headers"),
            json=p.get("json"), data=p.get("data"),
            timeout=float(p.get("timeout", 15)),
            verify=as_bool(p.get("verify", True)))
    except Exception as ex:
        raise StepError("请求失败 %s %s：%s" % (method, url, str(ex).split("\n")[0]))
    result = {"status": resp.status_code, "ok": bool(resp.ok),
              "text": resp.text, "headers": dict(resp.headers)}
    try:
        result["json"] = resp.json()
    except Exception:
        pass
    if p.get("output"):
        op = anchor(p["output"])
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_bytes(resp.content)
        result["file"] = str(op)
    name = str(p.get("name") or "http")
    e.vars[name] = result
    e.logf("%s %s → HTTP %d" % (method, url, resp.status_code), echo=True)
    return result


# ---------------------------------------------------------------- 下载 / 校验

@action("download")
def _a_download(e, p, s):
    """下载附件：selector 或 text 二选一，缺省自动寻找“附件/下载”链接。"""
    dest = Path(p["dir"]) if p.get("dir") else Path(e.settings.get("attachment_dir") or "附件")
    if not dest.is_absolute():
        dest = base_dir() / dest
    subdir = p.get("subdir") or e.vars.get("category") or ""
    if subdir:
        dest = dest / sanitize_filename(subdir)
    prefix = p.get("filename_prefix", "") or str(e.vars.get("label")
                                                  or e.vars.get("task_no") or "")
    frames = e.frames_of()
    got = dom_download(e.current, frames, dest, prefix,
                       selector=p.get("selector"), text=p.get("text"),
                       timeout=e.timeout(p, 20000), logf=e.logf)
    if not got:
        if p.get("optional"):
            e.logf("未找到可下载的附件（optional，跳过）")
        else:
            e.logf("未找到可下载的附件")
    return got


@action("expect_text")
def _a_expect_text(e, p, s):
    text = str(p.get("text", ""))
    if not e.has_text(text):
        raise StepError("断言失败：页面上未出现文字“%s”" % text)
    e.logf("断言通过：页面包含“%s”" % text)
    return True


@action("expect_visible")
def _a_expect_visible(e, p, s):
    sel = p.get("selector")
    if not e.selector_visible(sel, p.get("frame")):
        raise StepError("断言失败：元素不可见 %s" % sel)
    e.logf("断言通过：元素可见 %s" % sel)
    return True


@action("expect_url")
def _a_expect_url(e, p, s):
    pattern = p.get("pattern") or p.get("url")
    if not pattern:
        raise StepError("expect_url 需要 pattern")
    url = e.current.url or ""
    if p.get("regex"):
        ok = re.search(str(pattern), url) is not None
    else:
        import fnmatch
        ok = fnmatch.fnmatch(url, str(pattern))
    if not ok:
        raise StepError("断言失败：当前 URL %s 不匹配 %s" % (url, pattern))
    e.logf("断言通过：URL 匹配 %s" % pattern)
    return True


@action("assert")
def _a_assert(e, p, s):
    cond = p.get("condition", p.get("if"))
    if cond is None:
        raise StepError("assert 需要 condition")
    if not eval_condition(cond, e):
        raise StepError("断言失败：%s" % p.get("message", cond))
    return True


@action("screenshot")
def _a_screenshot(e, p, s):
    shot(e.current, str(p.get("name", "手动截图")))
    return None


@action("fail")
def _a_fail(e, p, s):
    raise StepError(str(p.get("message", "流程主动失败")))


@action("pause")
def _a_pause(e, p, s):
    pause_for_manual(str(p.get("message", "")))
    return None


# ---------------------------------------------------------------- 页面管理

@action("switch_page")
def _a_switch_page(e, p, s):
    pages = e.ctx.pages
    if p.get("index") is not None:
        e.current = pages[int(p["index"])]
    elif p.get("url_contains"):
        for pg in pages:
            if str(p["url_contains"]) in (pg.url or ""):
                e.current = pg
                break
        else:
            raise StepError("找不到 URL 包含 %r 的页面" % p["url_contains"])
    elif p.get("title_contains"):
        for pg in pages:
            if str(p["title_contains"]) in (pg.title() or ""):
                e.current = pg
                break
        else:
            raise StepError("找不到标题包含 %r 的页面" % p["title_contains"])
    else:
        e.current = pages[-1]
    e.logf("切换到页面：%s" % e.current.url)
    return e.current.url


@action("close_page")
def _a_close_page(e, p, s):
    which = str(p.get("which") or "current")
    if which == "others":
        targets = [pg for pg in e.ctx.pages
                   if pg is not e.list_page and pg is not e.current]
    elif which == "all_task":
        targets = [pg for pg in e.ctx.pages if pg is not e.list_page]
    else:
        targets = [e.current] if e.current is not e.list_page else []
    for pg in targets:
        try:
            pg.close()
        except Exception:
            pass
    if e.list_page is not None:
        e.current = e.list_page
    elif e.ctx.pages:
        e.current = e.ctx.pages[-1]
    return len(targets)


@action("close_task_page")
def _a_close_task_page(e, p, s):
    for pg in list(e.opened_pages):
        try:
            pg.close()
        except Exception:
            pass
    e.opened_pages = []
    if e.list_page is not None:
        e.current = e.list_page
    e.logf("已关闭任务标签页，回到列表")
    return True


# ---------------------------------------------------------------- 探测 / 录制

@action("probe")
def _a_probe(e, p, s):
    """扫描页面并打印元素清单 + 选择器建议；file 省略=写 shots/probe_*.yaml。"""
    data = collect_probe(e.ctx, max_each=int(p.get("max", 60) or 60),
                         all_pages=as_bool(p.get("all_pages", True)))
    report_probe(data)
    summary = {"pages": len(data),
               "elements": sum(len(fr["buttons"]) + len(fr["inputs"]) + len(fr["choices"])
                               + len(fr["selects"]) for pg in data for fr in pg["frames"])}
    file = p.get("file")
    if file is None or str(file).strip():
        path = write_probe_file(data, str(file) if str(file or "").strip() else None,
                                with_steps=as_bool(p.get("steps", True)))
        summary["file"] = str(path)
        e.logf("探测结果已写入：%s" % path, echo=True)
    return summary


@action("record")
def _a_record(e, p, s):
    """录制人工操作并生成 YAML 步骤（默认写 shots/record_*.yaml）。"""
    recorder_install(e.ctx)
    e.logf("操作录制已开始，请在浏览器中操作……", echo=True)
    print()
    print("=" * 62)
    print(">>> 操作录制中：请在浏览器里正常操作（点击 / 填写 / 下拉 / 上传 / 回车）")
    print(">>> 完成后回到本窗口按回车结束录制")
    print("=" * 62)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    events = recorder_stop(e.ctx)
    steps, need_password = events_to_steps(events)
    e.logf("录制结束：%d 个事件 → %d 个步骤" % (len(events), len(steps)), echo=True)
    path = write_record_file(steps, e.wf, p.get("file") or None,
                             need_password=need_password)
    print()
    print("—— 录制生成的步骤（可直接粘贴到工作流的 steps 下）——")
    import yaml
    print(yaml.safe_dump({"steps": steps}, allow_unicode=True,
                         sort_keys=False, default_flow_style=False).strip())
    e.logf("录制结果已写入：%s" % path, echo=True)
    return {"file": str(path), "steps": steps}


# ---------------------------------------------------------------- 控制流

@action("if")
def _a_if(e, p, s):
    cond = p.get("condition")
    if cond is None:
        raise StepError("if 动作需要 with.condition（步骤级条件直接用 if: 字段）")
    if eval_condition(cond, e):
        e.run_steps(s.get("then") or [])
        return True
    e.run_steps(s.get("else") or [])
    return False


def _iter_over(e, p):
    over = p.get("over")
    if over is None:
        over = p.get("list")
    if over is None and p.get("rows"):
        _, loc = e.locator(p["rows"], p.get("frame"))
        return [loc.nth(i).inner_text() for i in range(min(loc.count(), 200))]
    if isinstance(over, list):
        return over
    if p.get("range") is not None:
        r = p["range"]
        start, stop = (0, int(r)) if not isinstance(r, list) else (int(r[0]), int(r[1]))
        return list(range(start, stop))
    if isinstance(over, str):
        return [x.strip() for x in over.split(",") if x.strip()]
    return []


@action("for_each")
def _a_for_each(e, p, s):
    items = _iter_over(e, p)
    body = s.get("do") or s.get("steps") or []
    as_name = str(p.get("as") or "item")
    e.logf("循环处理 %d 项（as=%s）" % (len(items), as_name))
    for i, item in enumerate(items):
        saved = {k: e.vars.get(k) for k in ("item", "index", as_name)}
        had = {k: (k in e.vars) for k in saved}
        e.vars["item"] = item
        e.vars["index"] = i
        e.vars[as_name] = item
        try:
            e.run_steps(body)
        except LoopBreak:
            e.logf("for_each 在第 %d 项 break" % i)
            break
        except LoopContinue:
            continue
        finally:
            for k, v in saved.items():
                if had[k]:
                    e.vars[k] = v
                else:
                    e.vars.pop(k, None)
    return len(items)


@action("repeat")
def _a_repeat(e, p, s):
    times = int(p.get("times", 1))
    body = s.get("do") or s.get("steps") or []
    for i in range(times):
        e.vars["index"] = i
        try:
            e.run_steps(body)
        except LoopBreak:
            break
        except LoopContinue:
            continue
    return times


@action("while")
def _a_while(e, p, s):
    cond = p.get("condition")
    body = s.get("do") or s.get("steps") or []
    limit = int(p.get("max", 100))
    var = str(p.get("as") or "index")
    for k, v in (p.get("init") or {}).items():
        e.vars[str(k)] = v
    if var not in e.vars:
        e.vars[var] = 0
    i = 0
    while i < limit:
        e.vars[var] = i
        if cond is not None and not eval_condition(cond, e):
            break
        try:
            e.run_steps(body)
        except LoopBreak:
            break
        except LoopContinue:
            pass
        i += 1
    if i >= limit:
        e.logf("while 达到上限 %d 次，已停止（可能是死循环）" % limit)
    return i


@action("break")
def _a_break(e, p, s):
    raise LoopBreak()


@action("continue")
def _a_continue(e, p, s):
    raise LoopContinue()


@action("run_steps")
def _a_run_steps(e, p, s):
    """内联执行子步骤。"""
    e.run_steps(s.get("do") or s.get("steps") or [])
    return None
