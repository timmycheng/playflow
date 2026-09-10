# -*- coding: utf-8 -*-
"""
tool_kit.py —— 内网平台任务自动化工具（单文件版）

运行环境：内网 Windows 虚拟机（免安装、双击 exe 即可运行）
  - 仅依赖：系统已安装的 Chrome 浏览器（channel="chrome"，不使用 Playwright 自带 Chromium）
  - 外网打包：pyinstaller -F tool_kit.py --collect-all playwright

四个模式：
  1. inspect  页面探测：把页面元素清单打印到控制台，照着输出填 config.json
  2. record   操作录制：playwright codegen（离线可用，打包后失效则降级提示）
  3. debug    选择器调试：交互式验证选择器 / 文字匹配
  4. run      执行自动化任务：读 config.json，自动签收-选radio-下载附件-提交

安全边界：本工具不包含任何在线下载、检查更新、数据上报逻辑；只访问 config.json
（workflow 模式下为 workflow.yaml）中配置的内网地址。
"""
import json
import os
import random
import re
import sys
import time
import traceback
import datetime
from pathlib import Path

# ================================================================ 基础设施

def _app_dir() -> Path:
    """exe 所在目录（打包后）或本文件所在目录（源码运行），配置/产物都放这里"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE_DIR = _app_dir()
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"      # 登录态持久化文件
SHOTS_DIR = BASE_DIR / "shots"            # 截图目录
LOG_DIR = BASE_DIR / "logs"               # 日志目录

LOG_LOCK = None  # 惰性初始化，避免打包环境 import threading 顺序问题
_LOG_FILE = None


def _get_log_file() -> Path:
    global _LOG_FILE
    if _LOG_FILE is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _LOG_FILE = LOG_DIR / ("run_%s.log" % datetime.date.today().strftime("%Y%m%d"))
    return _LOG_FILE


def log(msg: str, echo: bool = False):
    """写日志（UTF-8）；echo=True 时同步打印到控制台"""
    global LOG_LOCK
    if LOG_LOCK is None:
        import threading
        LOG_LOCK = threading.Lock()
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg)
    try:
        with LOG_LOCK:
            with open(_get_log_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    if echo:
        try:
            print(line, flush=True)
        except Exception:
            print(line.encode("gbk", "replace").decode("gbk"), flush=True)


def _init_stdio():
    """打包成 exe 后 Windows 控制台常见 GBK 编码，打印异常中文时兜底不崩溃"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


class ConfigError(Exception):
    """config.json 问题（给用户看的中文提示）"""


class FatalError(Exception):
    """致命失败（登录态失效、页面打不开等，附截图与现场 HTML）"""


class TaskError(Exception):
    """单条任务内的失败（不中断整体队列）"""


# ================================================================ 配置加载

_DEFAULT_CFG = {
    "sso_username": "",
    "sso_password": "",
    "login_username_selector": "",
    "login_password_selector": "",
    "login_button_keywords": ["登录", "登陆", "Login"],
    "channel": "chrome",
    "radio_rules": [],
    "default_radio": "",
    "sign_keywords": ["签收", "受理", "确认"],
    "submit_keywords": ["提交", "确定", "保存"],
    "download_attachments": True,
    "attachment_dir": "附件",
    "max_tasks": 1,
    "headless": False,
    "task_delay_seconds": [1, 3],
}

_REQUIRED = ("base_url", "sso_login_url")


def load_config(path=CONFIG_PATH) -> dict:
    """读取并校验 config.json：缺必填项给中文报错，缺可选项给默认值"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            "未找到配置文件：%s\n"
            "请把 config.json 放在工具（exe）同一目录下，用记事本编辑后重试。" % p)
    try:
        with open(p, "r", encoding="utf-8-sig") as f:   # 容忍记事本存的 BOM
            cfg = json.load(f)
    except Exception as e:
        raise ConfigError("config.json 不是合法的 JSON，无法解析：%s\n"
                          "请用记事本检查逗号、引号是否成对。" % e)
    if not isinstance(cfg, dict):
        raise ConfigError("config.json 顶层应是一个 JSON 对象（{...}）")

    # 必填项
    missing = [k for k in _REQUIRED if not str(cfg.get(k, "")).strip()]
    if missing:
        raise ConfigError(
            "config.json 缺少必填项：%s\n"
            "  base_url      —— 业务平台首页地址，例如 http://192.168.1.10:8080\n"
            "  sso_login_url —— SSO 账密登录页地址" % "、".join(missing))

    # categories 校验
    cats = cfg.get("categories")
    if not isinstance(cats, list) or not cats:
        raise ConfigError(
            "config.json 缺少 categories（任务类别列表），或它不是非空数组。\n"
            "每个类别形如：{\"name\": \"类别一\", \"url\": \"http://.../list\", "
            "\"row_selector\": \"\", \"link_selector\": \"\"}")
    for i, c in enumerate(cats, 1):
        if not isinstance(c, dict) or not str(c.get("name", "")).strip() \
                or not str(c.get("url", "")).strip():
            raise ConfigError(
                "categories 第 %d 项不合法：需要包含非空的 name 和 url 字段。" % i)

    # 可选项 → 默认值，并提示
    for k, v in _DEFAULT_CFG.items():
        if k not in cfg or cfg[k] in ("", None):
            if cfg.get(k) != v:
                log("配置提示：未设置 %s，使用默认值 %r" % (k, v))
            cfg[k] = json.loads(json.dumps(v))  # 深拷贝默认值
    if not isinstance(cfg["radio_rules"], list):
        raise ConfigError("radio_rules 应为数组，形如 "
                          "[{\"if_type_contains\": \"报修\", \"then_pick\": \"同意\"}]")
    if cfg["max_tasks"] < 1:
        log("配置提示：max_tasks<1 无意义，已按 1 处理")
        cfg["max_tasks"] = 1
    if not cfg["default_radio"] and not cfg["radio_rules"]:
        log("配置警告：未配置 radio_rules 且无 default_radio，遇到单选框将跳过选择", echo=True)
    if not (isinstance(cfg["task_delay_seconds"], list)
            and len(cfg["task_delay_seconds"]) == 2):
        cfg["task_delay_seconds"] = [1, 3]
    return cfg


def load_config_optional(path=CONFIG_PATH) -> dict:
    """workflow 模式专用：config.json 整个文件都是可选的。
    不存在 / 无法解析 / 字段缺失都不阻断，只记日志并补默认值，
    让 workflow.yaml 能完全独立运行。"""
    cfg = {}
    p = Path(path)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = data
            else:
                log("config.json 顶层不是 JSON 对象，workflow 模式已忽略该文件")
        except Exception as e:
            log("config.json 无法解析（%s），workflow 模式已忽略该文件" % e)
    for k, v in _DEFAULT_CFG.items():
        if k not in cfg or cfg[k] in ("", None):
            cfg[k] = json.loads(json.dumps(v))
    if not isinstance(cfg.get("radio_rules"), list):
        cfg["radio_rules"] = []
    if not (isinstance(cfg.get("task_delay_seconds"), list)
            and len(cfg["task_delay_seconds"]) == 2):
        cfg["task_delay_seconds"] = [1, 3]
    return cfg


def _merge_login_cfg(cfg: dict, login: dict):
    """把 workflow.login 的字段并入 cfg：登录态失效后 run_workflow_task 会调用
    do_login（只认 cfg 的字段），这里保证 workflow 单独运行也能重新登录。"""
    def pick(*vals):
        for v in vals:
            if v not in (None, ""):
                return v
        return ""

    cfg["sso_login_url"] = pick(login.get("url"), cfg.get("sso_login_url"))
    cfg["base_url"] = pick(login.get("success_url"), login.get("verify_url"),
                           cfg.get("base_url"))
    cfg["sso_username"] = pick(login.get("username"), cfg.get("sso_username"))
    cfg["sso_password"] = pick(login.get("password"), cfg.get("sso_password"))
    cfg["login_username_selector"] = pick(login.get("username_selector"),
                                          cfg.get("login_username_selector"))
    cfg["login_password_selector"] = pick(login.get("password_selector"),
                                          cfg.get("login_password_selector"))
    bt = login.get("button_text")
    if bt:
        cfg["login_button_keywords"] = bt if isinstance(bt, list) else [bt]


def precheck_urls(cfg: dict):
    """用 requests 做一次轻量连通性预检（失败仅告警，不阻断——有些 SSO 会拦 GET）"""
    try:
        import requests
    except ImportError:
        log("未安装 requests，跳过连通性预检")
        return
    urls = [("base_url", cfg["base_url"]), ("sso_login_url", cfg["sso_login_url"])]
    urls += [("categories[%d].url" % i, c["url"]) for i, c in enumerate(cfg["categories"])]
    for name, u in urls:
        try:
            requests.get(u, timeout=3)
        except Exception as e:
            msg = "警告：无法访问 %s（%s）：%s" % (name, u, str(e).split("\n")[0])
            log(msg, echo=True)
            print("       ↑ 若 run 模式报错，请先用浏览器确认该地址是否正确。")


# ================================================================ 截图与现场保存

_SHOT_N = 0


def shot(page, stage: str, echo=False):
    """关键动作前后截图到 shots/，文件名：序号_阶段名_时间戳.png"""
    global _SHOT_N
    try:
        _SHOT_N += 1
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
        path = SHOTS_DIR / ("%03d_%s_%s.png" % (_SHOT_N, stage, ts))
        page.screenshot(path=str(path), full_page=True)
        log("截图：%s" % path.name, echo=echo)
        return path
    except Exception as e:
        log("截图失败(%s)：%s" % (stage, e))
        return None


def dump_scene(page, reason: str):
    """致命失败现场：截图 + 保存页面 HTML，便于离线分析"""
    shot(page, "致命现场")
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
        hp = SHOTS_DIR / ("fatal_scene_%s.html" % ts)
        hp.write_text(page.content(), encoding="utf-8")
        log("已保存现场 HTML：%s" % hp)
    except Exception as e:
        log("保存现场 HTML 失败：%s" % e)
    log("致命失败：%s" % reason, echo=True)


# ================================================================ 文字匹配核心

def _norm_text(s) -> str:
    """归一化空白：去掉所有空格/换行/制表符，兼容“签 收”“提 交”这类带空格的按钮文字"""
    return re.sub(r"\s+", "", s or "")


def _el_brief(loc) -> dict:
    """一次性取元素的 tag / 文字 / 可见性及常用属性（减少逐个属性查询的网络往返）"""
    try:
        return loc.evaluate(
            "el => ({tag: el.tagName.toLowerCase(),"
            " text: (el.innerText || el.textContent || el.value || '').trim(),"
            " visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),"
            " id: el.id || '',"
            " name: el.getAttribute('name') || '',"
            " type: (el.getAttribute('type') || '').toLowerCase(),"
            " placeholder: el.getAttribute('placeholder') || '',"
            " value: (typeof el.value === 'string' ? el.value : '')})")
    except Exception:
        return {}


def _iter_frames(x):
    """兼容传入 Page 或单个 Frame（debug 模式会针对单个 frame 测试）"""
    return list(x.frames) if hasattr(x, "main_frame") else [x]


_BUTTON_SELECTORS = ('button', 'a', '[role="button"]',
                     'input[type="submit"]', 'input[type="button"]')


def find_button(page, keywords):
    """
    按中文关键词列表查找按钮，返回第一个命中的 (关键词, frame, locator, brief)，无则 None。
    - 遍历 page.frames 全量扫描：老内网平台大量使用 iframe；
    - 匹配前对按钮文字与关键词都做空白归一化，兼容“签 收”“提 交”；
    - 优先级：先按关键词顺序，再按 frame 顺序（避免主页面的“确认”抢先于 iframe 里的“签收”）；
    - Playwright 的 locator 选择器默认穿透开放的 Shadow DOM，无需额外展开。
    """
    cache = {}

    def candidates(fr):
        if fr in cache:
            return cache[fr]
        lst = []
        for sel in _BUTTON_SELECTORS:
            try:
                locs = fr.locator(sel).all()
            except Exception:
                continue
            for lo in locs:
                b = _el_brief(lo)
                if b and _norm_text(b.get("text", "")):
                    lst.append((lo, b))
        cache[fr] = lst
        return lst

    for kw in keywords:
        nk = _norm_text(kw)
        if not nk:
            continue
        for fr in _iter_frames(page):
            for lo, b in candidates(fr):
                if nk in _norm_text(b.get("text", "")) and b.get("visible"):
                    return kw, fr, lo, b
    return None


def find_button_all(page, keywords):
    """find_button 的复数版：返回全部命中（debug 模式用），元素结构同上"""
    hits = []
    cache = {}

    def candidates(fr):
        if fr in cache:
            return cache[fr]
        lst = []
        for sel in _BUTTON_SELECTORS:
            try:
                locs = fr.locator(sel).all()
            except Exception:
                continue
            for lo in locs:
                b = _el_brief(lo)
                if b:
                    lst.append((lo, b))
        cache[fr] = lst
        return lst

    for kw in keywords:
        nk = _norm_text(kw)
        if not nk:
            continue
        for fr in _iter_frames(page):
            for lo, b in candidates(fr):
                if nk in _norm_text(b.get("text", "")):
                    hits.append((kw, fr, lo, b))
    return hits


def radio_label_text(fr, radio_loc) -> str:
    """综合 radio 的 value 属性、label[for=id] 文字、父级 label 文字"""
    parts = []
    try:
        parts.append(radio_loc.get_attribute("value") or "")
    except Exception:
        pass
    try:
        rid = (radio_loc.get_attribute("id") or "").replace('"', "")
        if rid:
            lbl = fr.locator('label[for="%s"]' % rid)
            if lbl.count() > 0:
                parts.append(lbl.first.inner_text())
    except Exception:
        pass
    try:
        parts.append(radio_loc.evaluate(
            "el => { const p = el.closest('label'); return p ? p.innerText : ''; }"))
    except Exception:
        pass
    return " ".join(parts)


def pick_radio(page, keyword) -> bool:
    """按关键词勾选单选框：遍历所有 frame 的 input[type=radio]，命中即 check()"""
    nk = _norm_text(keyword)
    if not nk:
        return False
    for fr in page.frames:
        try:
            radios = fr.locator('input[type="radio"]').all()
        except Exception:
            continue
        for lo in radios:
            if nk in _norm_text(radio_label_text(fr, lo)):
                try:
                    lo.check(timeout=5000)
                except Exception:
                    lo.evaluate("el => el.click()")   # 被样式遮挡时兜底
                return True
    return False


_DEFAULT_ROW_SELECTORS = ['tr:has(a[href])', 'li:has(a[href])', 'div:has(>a[href])']


def _scan_first_row(page, cat, index=0):
    """在所有 frame 中找第 index 行任务行；row_selector/link_selector 可由 config 覆盖。
    同一 (frame, 选择器) 组合内按可见行计数，返回其中的第 index 行。"""
    row_sels = ([cat["row_selector"]] if cat.get("row_selector") else []) \
        + _DEFAULT_ROW_SELECTORS
    link_sel = cat.get("link_selector") or ""
    for fr in page.frames:
        for sel in row_sels:
            try:
                rows = fr.locator(sel)
                n = rows.count()
            except Exception:
                continue
            hits = []
            for i in range(min(n, 200)):
                row = rows.nth(i)
                try:
                    if not row.is_visible():
                        continue
                    link = row.locator(link_sel) if link_sel \
                        else row.locator('a[href]').first
                    if link.count() == 0:
                        continue
                    text = row.inner_text()
                except Exception:
                    continue
                if not text.strip():
                    continue
                hits.append({"frame": fr, "row": row, "link": link, "text": text})
                if len(hits) > index:        # 找够就早退，避免大列表全量扫描
                    return hits[index]
            if len(hits) > index:
                return hits[index]
    return None


def find_first_task_row(page, cat, retries=5, index=0):
    """带重试的取行：列表在 iframe 里或由 JS 异步渲染时，外层 load 完内容可能还没就绪"""
    for attempt in range(retries):
        r = _scan_first_row(page, cat, index)
        if r:
            return r
        try:
            # 等网络安静，兼容 AJAX 渲染的列表
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return None


# ================================================================ 页面探测（probe）
# 供 workflow 的 probe 动作、交互式 probe 模式、record 生成的步骤共用：
# 扫描页面元素 → 给出稳健选择器建议 + 可直接粘贴的 YAML 步骤。

def _safe_css_str(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def suggest_selector(b) -> str:
    """按 id > name > placeholder > 文字 的优先级给出选择器建议"""
    tag = b.get("tag") or "*"
    if b.get("id"):
        eid = str(b["id"])
        if re.fullmatch(r"[A-Za-z_][-\w]*", eid):
            return "#" + eid
        return '%s[id="%s"]' % (tag, _safe_css_str(eid))
    if b.get("name"):
        return '%s[name="%s"]' % (tag, _safe_css_str(b["name"]))
    if b.get("placeholder"):
        return '%s[placeholder*="%s"]' % (tag, _safe_css_str(b["placeholder"]))
    text = " ".join((b.get("text") or "").split())
    if text and len(text) <= 30:
        return '%s:has-text("%s")' % (tag, _safe_css_str(text))
    return tag


_INPUT_TYPES = ("", "text", "password", "email", "number", "tel", "search", "url", "date",
                "datetime-local", "month", "week", "time")


def collect_probe(ctx, max_each=60, all_pages=True) -> list:
    """结构化扫描：每页每 frame 的按钮/输入框/单选框/下拉框/列表行候选"""
    pages = list(ctx.pages) if all_pages else ([ctx.pages[-1]] if ctx.pages else [])
    result = []
    for pi, page in enumerate(pages):
        try:
            title = page.title()
        except Exception:
            title = ""
        frs = []
        for fi, fr in enumerate(page.frames):
            data = {"index": fi, "iframe": fr != page.main_frame, "url": fr.url or "",
                    "buttons": [], "inputs": [], "choices": [], "selects": [], "rows": []}
            # 按钮 / 链接
            seen = 0
            for sel in _BUTTON_SELECTORS:
                if seen >= max_each:
                    break
                try:
                    locs = fr.locator(sel).all()
                except Exception:
                    continue
                for lo in locs:
                    b = _el_brief(lo)
                    if not b or not b.get("visible") or not _norm_text(b.get("text", "")):
                        continue
                    data["buttons"].append({
                        "tag": b["tag"], "text": " ".join(b["text"].split())[:60],
                        "selector": suggest_selector(b)})
                    seen += 1
                    if seen >= max_each:
                        break
            # 输入框（含 textarea）
            try:
                locs = fr.locator("input, textarea").all()
            except Exception:
                locs = []
            for lo in locs[:max_each * 2]:
                b = _el_brief(lo)
                if not b or not b.get("visible"):
                    continue
                if b["tag"] == "input" and b.get("type") not in _INPUT_TYPES:
                    continue
                data["inputs"].append({
                    "tag": b["tag"], "type": b.get("type") or "text",
                    "name": b.get("name", ""), "placeholder": b.get("placeholder", ""),
                    "selector": suggest_selector(b)})
            # 单选框 / 复选框
            try:
                locs = fr.locator('input[type="radio"], input[type="checkbox"]').all()
            except Exception:
                locs = []
            for lo in locs[:max_each]:
                b = _el_brief(lo)
                if not b:
                    continue
                try:
                    checked = lo.is_checked()
                except Exception:
                    checked = False
                data["choices"].append({
                    "type": b.get("type") or "radio", "name": b.get("name", ""),
                    "value": b.get("value", ""),
                    "label": " ".join(radio_label_text(fr, lo).split())[:40],
                    "checked": checked, "selector": suggest_selector(b)})
            # 下拉框
            try:
                locs = fr.locator("select").all()
            except Exception:
                locs = []
            for lo in locs[:max_each]:
                b = _el_brief(lo)
                if not b or not b.get("visible"):
                    continue
                try:
                    opts = [" ".join(o.inner_text().split())[:30]
                            for o in lo.locator("option").all()]
                except Exception:
                    opts = []
                data["selects"].append({"name": b.get("name", ""),
                                        "selector": suggest_selector(b),
                                        "options": opts[:20]})
            # 列表行候选
            for sel in _DEFAULT_ROW_SELECTORS:
                try:
                    n = fr.locator(sel).count()
                except Exception:
                    n = 0
                if n:
                    data["rows"].append({"selector": sel, "count": n})
            frs.append(data)
        result.append({"page_index": pi, "url": page.url or "", "title": title,
                       "frames": frs})
    return result


def probe_report_text(probe_data) -> str:
    """把探测结果打印成人类可读清单（选择器建议直接跟在元素后面）"""
    lines = []
    for pg in probe_data:
        lines.append("=" * 66)
        lines.append("[页面%d] %s   标题：%s"
                     % (pg["page_index"], pg["url"], _fmt_text(pg["title"], 40)))
        for fr in pg["frames"]:
            lines.append("-" * 66)
            lines.append("  %sframe#%d  %s"
                         % ("[iframe] " if fr["iframe"] else "", fr["index"], fr["url"]))
            for b in fr["buttons"]:
                lines.append("    [%s] %s    → %s"
                             % (b["tag"], _fmt_text(b["text"]), b["selector"]))
            for i in fr["inputs"]:
                lines.append("    [input:%s] name=%s placeholder=%s    → %s"
                             % (i["type"], i["name"] or "-",
                                _fmt_text(i["placeholder"], 20) or "-", i["selector"]))
            for c in fr["choices"]:
                lines.append("    [%s] name=%s value=%s label=%s%s    → %s"
                             % (c["type"], c["name"], c["value"], c["label"],
                                "（已选）" if c["checked"] else "", c["selector"]))
            for s in fr["selects"]:
                lines.append("    [select] name=%s options=%s    → %s"
                             % (s["name"] or "-", "、".join(s["options"][:6]), s["selector"]))
            for r in fr["rows"]:
                lines.append("    [rows] %s × %d" % (r["selector"], r["count"]))
    lines.append("=" * 66)
    text = "\n".join(lines)
    print(text)
    return text


def probe_suggest_steps(probe_data, max_steps=40) -> list:
    """把探测结果转成可直接粘贴的步骤草案（供人工裁剪）"""
    steps = []
    for pg in probe_data:
        for fr in pg["frames"]:
            frags = []
            for b in fr["buttons"][:8]:
                frags.append({"uses": "click_text", "with": {"text": [b["text"]]}})
            for i in fr["inputs"][:6]:
                frags.append({"uses": "fill", "with": {"selector": i["selector"], "text": ""}})
            for c in fr["choices"][:6]:
                if c["type"] == "radio" and c["label"]:
                    frags.append({"uses": "pick_radio", "with": {"text": c["label"]}})
                else:
                    frags.append({"uses": "check", "with": {"selector": c["selector"]}})
            for s in fr["selects"][:4]:
                frags.append({"uses": "select_option",
                              "with": {"selector": s["selector"], "label": ""}})
            if fr["rows"]:
                frags.append({"uses": "click_row_link"})
            if frags and fr["iframe"]:
                steps.append({"uses": "log",
                              "with": {"message": "--- iframe#%d %s ---"
                                                   % (fr["index"], fr["url"][-60:])}})
            steps += frags
            if len(steps) >= max_steps:
                return steps[:max_steps]
    return steps


def write_probe_file(probe_data, path=None, with_steps=True) -> Path:
    """把探测结果写成 YAML（含 suggested_steps 草稿），返回文件路径"""
    if path is None:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOTS_DIR / ("probe_%s.yaml" % datetime.datetime.now().strftime("%H%M%S"))
    path = Path(path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"probe": probe_data}
    if with_steps:
        payload["suggested_steps"] = probe_suggest_steps(probe_data)
    header = ("# tool_kit 页面探测结果（%s）\n"
              "# probe = 原始清单；suggested_steps = 可直接粘贴到 workflow.yaml 的步骤草稿（请裁剪）\n"
              % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    path.write_text(header + dump_yaml(payload), encoding="utf-8")
    return path


# ================================================================ 操作录制（record）
# 向页面注入事件监听（点击/填写/下拉/勾选/上传/回车），回窗口回车结束，
# 把事件流转换成可直接运行的 YAML 步骤。与 playwright codegen 不同：
# 产物是 workflow.yaml 片段，复用同一套动作与登录态。

_RECORDER_JS = r"""
(() => {
  if (window.__tkRecorder) return;
  const R = { events: [], on: true };
  window.__tkRecorder = R;
  const MAX = 800;
  const KEY = '__tkRecorderEvents';
  const isTop = (() => { try { return window.top === window; } catch (e) { return false; } })();
  R.save = () => {
    if (!isTop) return;
    try { sessionStorage.setItem(KEY, JSON.stringify(R.events)); } catch (e) {}
  };
  if (isTop) {
    try {
      const prev = sessionStorage.getItem(KEY);
      if (prev) {
        const arr = JSON.parse(prev);
        if (Array.isArray(arr)) R.events = arr.slice(-MAX);
      }
    } catch (e) {}
  }
  R.drain = () => { const e = R.events.splice(0, R.events.length); R.save(); return e; };
  const esc = s => String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) {
      if (/^[A-Za-z_][-\w]*$/.test(el.id)) return '#' + el.id;
      return el.tagName.toLowerCase() + '[id="' + esc(el.id) + '"]';
    }
    const name = el.getAttribute && el.getAttribute('name');
    if (name) return el.tagName.toLowerCase() + '[name="' + esc(name) + '"]';
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < 4) {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent; depth++;
    }
    return parts.join(' > ');
  }
  function txt(el) {
    return ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim().slice(0, 60);
  }
  function info(el) {
    const get = n => (el.getAttribute && el.getAttribute(n)) || '';
    const b = {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '', name: get('name'),
      itype: get('type').toLowerCase(), placeholder: get('placeholder'),
      selector: cssPath(el)
    };
    if (b.tag === 'a' || b.tag === 'button' || get('role') === 'button') b.text = txt(el);
    if (b.tag === 'a') { b.href = get('href'); b.target = get('target'); }
    if ('value' in el && typeof el.value === 'string') b.value = el.value;
    return b;
  }
  function push(ev) {
    if (!R.on || R.events.length >= MAX) return;
    ev.ts = Date.now();
    try { R.events.push(ev); R.save(); } catch (e) {}
  }
  document.addEventListener('click', e => {
    const raw = e.target;
    let el = raw && raw.closest
      ? raw.closest('a,button,input,select,textarea,label,[role=button],[onclick]') : raw;
    if (!el || el.nodeType !== 1) return;
    if (el.tagName === 'LABEL') {
      const forId = el.getAttribute('for');
      const t = forId ? document.getElementById(forId) : el.querySelector('input');
      if (t) {
        const bb = info(t); bb.text = txt(el);
        push(Object.assign({ kind: 'click' }, bb));
        return;
      }
    }
    push(Object.assign({ kind: 'click' }, info(el)));
  }, true);
  const lastVals = new Map();
  function flushValue(el) {
    if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA')) return;
    const t = ((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
    if (['checkbox','radio','file','submit','button','reset','image'].indexOf(t) >= 0) return;
    const b = info(el);
    const v = typeof el.value === 'string' ? el.value : '';
    const key = b.selector || b.name || b.tag;
    if (lastVals.get(key) === v) return;
    lastVals.set(key, v);
    push(Object.assign({ kind: 'fill' }, b));
  }
  document.addEventListener('blur', e => flushValue(e.target), true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (!el || el.nodeType !== 1) return;
    const t = ((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
    if (el.tagName === 'SELECT') {
      const b = info(el);
      const opt = el.options[el.selectedIndex];
      b.text = opt ? opt.text.trim() : '';
      push(Object.assign({ kind: 'select' }, b));
    } else if (t === 'file') {
      const b = info(el);
      b.files = Array.from(el.files || []).map(f => f.name);
      push(Object.assign({ kind: 'file' }, b));
    } else if (t === 'checkbox' || t === 'radio') {
      push(Object.assign({ kind: 'check' }, info(el)));
    } else {
      flushValue(el);
    }
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter'
        && e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
      push({ kind: 'press', key: 'Enter', selector: cssPath(e.target) });
    }
  }, true);
})();
"""


def recorder_install(ctx):
    """把录制器注入上下文：已打开的 frame 立即注入，之后的新页面由 init script 覆盖。
    注入前清掉上次录制可能残留的 sessionStorage，避免事件串场。"""
    clear = "() => { try { sessionStorage.removeItem('__tkRecorderEvents'); } catch (e) {} }"
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                fr.evaluate(clear)
            except Exception:
                continue
    try:
        ctx.add_init_script(_RECORDER_JS)
    except Exception:
        pass
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                fr.evaluate(_RECORDER_JS)
            except Exception:
                continue


def recorder_drain(ctx) -> list:
    """收取所有页面所有 frame 的录制事件（按时间排序）"""
    events = []
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                evs = fr.evaluate(
                    "() => (window.__tkRecorder ? window.__tkRecorder.drain() : [])")
            except Exception:
                continue
            if evs:
                events.extend(evs)
    events.sort(key=lambda e: e.get("ts") or 0)
    return events


def recorder_stop(ctx) -> list:
    """停止录制并取回事件（先取事件，再清 sessionStorage 残留）"""
    events = recorder_drain(ctx)
    clear = ("() => { if (window.__tkRecorder) { window.__tkRecorder.on = false; "
             "try { sessionStorage.removeItem('__tkRecorderEvents'); } catch (e) {} } }")
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                fr.evaluate(clear)
            except Exception:
                continue
    return events


def _auto_step_name(step) -> str:
    u, w = step.get("uses"), (step.get("with") or {})
    if u == "click_text":
        t = w.get("text")
        return "点击「%s」" % ((t[0] if isinstance(t, list) and t else t) or "")
    if u == "click":
        return "点击 %s" % w.get("selector", "")
    if u == "fill":
        return "填写 %s" % w.get("selector", "")
    if u == "pick_radio":
        return "选择「%s」" % w.get("text", "")
    if u == "check":
        return "勾选 %s" % w.get("selector", "")
    if u == "select_option":
        return "下拉选择「%s」" % w.get("label", "")
    if u == "upload":
        return "上传文件"
    if u == "press":
        return "按键 %s" % w.get("key", "")
    return u or "步骤"


def events_to_steps(events):
    """录制事件 → YAML 步骤。返回 (steps, need_password)。
    密码框统一替换成 {{ env.password }}，避免把明文写进 YAML。"""
    steps, need_password = [], False
    for ev in events:
        kind = ev.get("kind")
        tag = (ev.get("tag") or "").lower()
        itype = (ev.get("itype") or "").lower()
        sel = ev.get("selector") or ""
        text = " ".join((ev.get("text") or "").split())
        if kind == "click":
            if itype in ("radio", "checkbox"):
                if itype == "radio" and text:
                    steps.append({"uses": "pick_radio", "with": {"text": text}})
                else:
                    w = {"selector": sel}
                    if text:
                        w["text"] = text
                    steps.append({"uses": "check", "with": w})
                continue
            if itype in ("submit", "button", "reset", "image"):
                label = text or (ev.get("value") or "")
                if label:
                    steps.append({"uses": "click_text", "with": {"text": [label]}})
                elif sel:
                    steps.append({"uses": "click", "with": {"selector": sel}})
                continue
            if text:
                w = {"text": [text]}
                if ev.get("target") == "_blank":
                    w["capture_new_page"] = True
                steps.append({"uses": "click_text", "with": w})
            elif sel:
                w = {"selector": sel}
                if ev.get("target") == "_blank":
                    w["capture_new_page"] = True
                steps.append({"uses": "click", "with": w})
        elif kind == "fill":
            if not sel:
                continue
            if itype == "password":
                need_password = True
                step = {"uses": "fill",
                        "with": {"selector": sel, "text": "{{ env.password }}", "secret": True}}
            else:
                step = {"uses": "fill",
                        "with": {"selector": sel, "text": ev.get("value") or ""}}
            prev = steps[-1] if steps else None
            if prev and prev.get("uses") == "fill" and prev["with"].get("selector") == sel:
                steps[-1] = step
            else:
                steps.append(step)
        elif kind == "select":
            steps.append({"uses": "select_option",
                          "with": {"selector": sel, "label": text or ev.get("value") or ""}})
        elif kind == "check":
            prev = steps[-1] if steps else None
            if prev and prev.get("uses") == "pick_radio":
                continue                       # 点击 label 已生成 pick_radio，change 事件不再重复
            if prev and prev.get("uses") == "check" and sel \
                    and (prev.get("with") or {}).get("selector") == sel:
                continue
            if itype == "radio":
                label = ev.get("label") or ""
                if label:
                    steps.append({"uses": "pick_radio", "with": {"text": label}})
                else:
                    steps.append({"uses": "check", "with": {"selector": sel}})
            else:
                steps.append({"uses": "check", "with": {"selector": sel}})
        elif kind == "file":
            files = list(ev.get("files") or []) or ["<请填写文件路径>"]
            steps.append({"uses": "upload", "with": {"selector": sel, "file": files[0]}})
        elif kind == "press":
            steps.append({"uses": "press",
                          "with": {"key": ev.get("key") or "Enter", "selector": sel}})

    # 去重：相邻完全相同的步骤 / 相邻同样的 click_text
    deduped = []
    for st in steps:
        if deduped and deduped[-1] == st:
            continue
        if (deduped and st.get("uses") == "click_text"
                and deduped[-1].get("uses") == "click_text"
                and st["with"].get("text") == deduped[-1]["with"].get("text")):
            continue
        deduped.append(st)
    for st in deduped:
        st.setdefault("name", _auto_step_name(st))
    return deduped, need_password


def write_record_file(steps, wf=None, path=None, need_password=False) -> Path:
    """把录制出的步骤写成可运行的 workflow.yaml（含当前登录配置）"""
    if path is None:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOTS_DIR / ("record_%s.yaml" % datetime.datetime.now().strftime("%H%M%S"))
    path = Path(path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = {"name": "录制流程"}
    login = (wf or {}).get("login")
    if isinstance(login, dict) and login:
        safe_login = dict(login)
        if "password" in safe_login:
            safe_login["password"] = "{{ env.password }}"
        doc["login"] = safe_login
    env = dict((wf or {}).get("env") or {})
    if need_password and not env.get("password"):
        env["password"] = ""
    if env:
        doc["env"] = env
    doc["steps"] = steps
    header = ("# tool_kit 操作录制生成（%s）\n"
              "# 请核对 URL、账号与选择器后使用；suggested 步骤可自由删改。\n"
              "# 运行：tool_kit.exe --workflow %s\n"
              % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), path.name))
    path.write_text(header + dump_yaml(doc), encoding="utf-8")
    return path


# ================================================================ 浏览器与登录

_ACTIVE_CONTEXT = None   # 供外部（如自动化测试）观察当前会话


def launch_browser(p, cfg, force_headed=False):
    """始终驱动系统 Chrome（channel），不使用 Playwright 自带 Chromium"""
    channel = cfg.get("channel") or "chrome"
    headless = bool(cfg.get("headless", False)) and not force_headed
    try:
        return p.chromium.launch(channel=channel, headless=headless)
    except Exception as e:
        raise FatalError(
            "浏览器启动失败（channel=%s）：%s\n"
            "请确认本机已安装 Google Chrome 浏览器；如是其它报错请把 logs/ 下日志发给管理员。"
            % (channel, str(e).split("\n")[0]))


def _default_dialog_handler(d):
    try:
        log("捕获浏览器原生弹窗[%s]：%s → 自动【接受】" % (d.type, d.message), echo=True)
        d.accept()
    except Exception:
        pass


def tune_page(page, dialog_handler=None):
    """新页面的统一初始化：默认超时 + 原生弹窗处理（默认自动接受）"""
    try:
        page.set_default_timeout(20000)
    except Exception:
        pass

    def _on_dialog(d):
        try:
            (dialog_handler or _default_dialog_handler)(d)
        except Exception:
            pass
    page.on("dialog", _on_dialog)


def setup_context(ctx, dialog_handler=None):
    """对上下文中已有及未来的所有页面生效"""
    for pg in ctx.pages:
        tune_page(pg, dialog_handler)

    def _on_page(pg):
        tune_page(pg, dialog_handler)
    ctx.on("page", _on_page)


def is_login_page(page) -> bool:
    """判断当前页面是否被重定向到了登录页：优先看有无可见密码框，其次看 URL 关键字"""
    try:
        for fr in page.frames:
            try:
                if fr.locator('input[type="password"]').count() > 0:
                    return True
            except Exception:
                continue
        u = (page.url or "").lower()
        return any(k in u for k in ("login", "signin", "logon"))
    except Exception:
        return False


def pause_for_ukey(hint=""):
    """UKey 半自动暂停点：UKey 弹窗是浏览器外的独立驱动程序窗口，无法自动化"""
    print()
    print("=" * 62)
    print(">>> 请在 UKey 弹窗中完成验证（该弹窗无法自动化操作）。%s" % hint)
    print(">>> 完成后回到本窗口，按 回车 继续……")
    print("=" * 62)
    try:
        input()
    except EOFError:
        pass


def _find_first_visible(page, selectors):
    """跨 frame 找第一个可见且可填写的输入框"""
    for sel in selectors:
        for fr in page.frames:
            try:
                locs = fr.locator(sel)
                for i in range(min(locs.count(), 5)):
                    lo = locs.nth(i)
                    try:
                        if lo.is_visible() and lo.is_editable():
                            return fr, lo
                    except Exception:
                        continue
            except Exception:
                continue
    return None, None


def safe_goto(page, url, what="页面"):
    """带友好报错的跳转：URL 错误时不裸抛 Playwright 堆栈；返回 Response（可能为 None）"""
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
        try:
            page.wait_for_load_state("load", timeout=10000)  # 等 iframe 就绪
        except Exception:
            pass
        return resp
    except Exception as e:
        log(traceback.format_exc())
        raise FatalError(
            "无法访问%s：%s\n"
            "可能原因：① 地址写错（请用记事本检查 config.json）；"
            "② 目标服务未启动；③ 网络不通。\n"
            "原始错误：%s" % (what, url, str(e).split("\n")[0]))


# ---------------------------------------------------------------- 登录态文件（state.json）

def _host_of(url) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def _state_meta_path(state_path: Path) -> Path:
    p = Path(state_path)
    return p.with_suffix(".meta.json") if p.name.endswith(".json") \
        else Path(str(p) + ".meta.json")


def _save_state(ctx, state_path, meta=None):
    """保存登录态；同时写 <state>.meta.json 记录所属站点，防止跨流程误用"""
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(state_path))
    if not meta:
        return
    try:
        data = dict(meta)
        data["saved_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _state_meta_path(state_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log("保存登录态元数据失败：%s" % e)


def state_reusable(state_path, target_url) -> tuple:
    """判断 state 文件能否用于 target_url，返回 (可用?, 原因)。
    ① 元数据记录的站点不一致 → 不可用（跨流程/跨平台误用的主保护）；
    ② 文件里没有任何目标站点的 cookie/localStorage → 不可用。
    读不出文件内容时退回"可用"，交给后续页面级校验，避免误伤。"""
    p = Path(state_path)
    if not p.exists():
        return False, "没有 state 文件"
    host = _host_of(target_url)
    if not host:
        return True, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return True, ""
    try:
        mpath = _state_meta_path(p)
        if mpath.exists():
            meta = json.loads(mpath.read_text(encoding="utf-8-sig"))
            origin = str(meta.get("origin") or "").lower()
            if origin and not (host == origin or host.endswith("." + origin)
                               or origin.endswith("." + host)):
                return False, "state 文件属于 %s，与当前流程 %s 不是同一站点" % (origin, host)
    except Exception:
        pass
    for c in (data.get("cookies") or []):
        d = str(c.get("domain") or "").lstrip(".").lower()
        if d and (host == d or host.endswith("." + d)):
            return True, ""
    for o in (data.get("origins") or []):
        oh = _host_of(o.get("origin") or "")
        if oh and (host == oh or host.endswith("." + oh) or oh.endswith("." + host)):
            return True, ""
    return False, "state 文件里没有 %s 的登录态（可能来自其它流程或站点）" % host


def resolve_state_path(spec=None) -> Path:
    """workflow.login.state_file：相对路径锚定工具目录，默认 state.json"""
    if not spec:
        return STATE_PATH
    p = Path(str(spec))
    return p if p.is_absolute() else (BASE_DIR / p)


def do_login(ctx, page, cfg, logf=log, state_path=STATE_PATH):
    """完整登录流程：SSO账密 → UKey人工暂停 → 回首页校验 → 持久化 state.json"""
    global _ACTIVE_CONTEXT
    _ACTIVE_CONTEXT = ctx

    logf("打开 SSO 登录页：%s" % cfg["sso_login_url"], echo=True)
    safe_goto(page, cfg["sso_login_url"], "SSO登录页")

    user, pwd = cfg.get("sso_username", ""), cfg.get("sso_password", "")
    if user and pwd:
        uf, ulo = _find_first_visible(
            page, [cfg["login_username_selector"] or "input[type='text']",
                   "input[name*='user' i]", "input[id*='user' i]"])
        pf, plo = _find_first_visible(
            page, [cfg["login_password_selector"] or "input[type='password']"])
        if not ulo or not plo:
            shot(page, "登录页找不到输入框")
            raise FatalError("在登录页上找不到账号/密码输入框，请截图保存（shots/ 目录），"
                             "并在 config.json 中配置 login_username_selector / "
                             "login_password_selector 后重试。")
        ulo.fill(user)
        plo.fill(pwd)
        hit = find_button(page, cfg["login_button_keywords"])
        if not hit:
            shot(page, "登录页找不到登录按钮")
            raise FatalError("在登录页上找不到登录按钮（关键词 %s）。请在 config.json 的 "
                             "login_button_keywords 中补充登录按钮上的文字。"
                             % cfg["login_button_keywords"])
        logf("点击登录按钮（命中关键词：%s）" % hit[0], echo=True)
        hit[2].click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
    else:
        print("config.json 未配置 sso_username/sso_password，请在浏览器中手动输入账密并点击登录。")

    shot(page, "UKey前", echo=True)
    pause_for_ukey()                       # ← 半自动暂停：等人工完成 UKey
    shot(page, "UKey后", echo=True)

    logf("登录环节结束，访问平台首页校验登录态…", echo=True)
    safe_goto(page, cfg["base_url"], "平台首页")
    if is_login_page(page):
        dump_scene(page, "登录未成功：仍被重定向到登录页。请检查账密是否正确、UKey 是否已完成。")
        raise FatalError("登录未成功：访问平台首页仍被重定向到登录页。现场已保存到 shots/ 目录。")

    try:
        _save_state(ctx, Path(state_path), {
            "origin": _host_of(cfg.get("base_url") or cfg.get("sso_login_url")),
            "workflow": "run",
            "success_url": cfg.get("base_url") or "",
        })
        logf("登录态已保存到 %s（下次运行不再触碰 UKey）" % Path(state_path).name, echo=True)
    except Exception as e:
        logf("保存 %s 失败：%s" % (Path(state_path).name, e), echo=True)


def ensure_login(browser, cfg, logf=log, dialog_handler=None, state_path=STATE_PATH):
    """每次启动：优先复用 state.json 并验证；失效/不属于本站点则重新走账密+UKey 流程"""
    global _ACTIVE_CONTEXT
    state_path = Path(state_path)
    target = cfg.get("base_url") or cfg.get("sso_login_url") or ""
    reusable, why = state_reusable(state_path, target)
    if state_path.exists() and not reusable:
        logf("忽略现有登录态：%s。将重新登录。" % why, echo=True)
    ctx = browser.new_context(
        storage_state=str(state_path) if reusable else None)
    setup_context(ctx, dialog_handler)   # 对已有及未来的所有页面挂超时/原生弹窗处理
    page = ctx.new_page()
    tune_page(page, dialog_handler)
    if reusable:
        try:
            page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("load", timeout=8000)
            except Exception:
                pass
        except Exception as e:
            logf(traceback.format_exc())
            raise FatalError("无法访问平台首页 %s：%s\n请检查 config.json 的 base_url 与内网连通性。"
                             % (cfg["base_url"], str(e).split("\n")[0]))
        if not is_login_page(page):
            logf("检测到有效登录态（复用 %s），跳过登录与 UKey。" % state_path.name, echo=True)
            _ACTIVE_CONTEXT = ctx
            return ctx, page
        logf("%s 已过期（被重定向到登录页），需要重新登录。" % state_path.name, echo=True)
        do_login(ctx, page, cfg, logf, state_path=state_path)
        return ctx, page
    do_login(ctx, page, cfg, logf, state_path=state_path)
    return ctx, page


# ================================================================ run 引擎

def open_after_click(ctx, src_page, click_fn, timeout_ms=5000):
    """
    点击后自动判断“新标签页”还是“当前页跳转”，返回 (页面, 是否新开)。
    语义上等价于 context.expect_page()：通过监听 context 的 page 事件捕获新标签页；
    同时能区分“点击本身失败”（向上抛错）与“无新标签页”（同页跳转）两种情况。
    """
    got = []

    def _on_page(pg):
        got.append(pg)
    ctx.on("page", _on_page)
    try:
        click_fn()
    except Exception:
        ctx.remove_listener("page", _on_page)
        raise
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline and not got:
        try:
            src_page.wait_for_timeout(120)   # 等待同时驱动事件分发
        except Exception:
            break
    ctx.remove_listener("page", _on_page)
    if got:
        np = got[0]
        try:
            np.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        return np, True
    try:
        src_page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    return None, False


def _task_no_from_row(text) -> str:
    m = re.search(r"[A-Za-z]{0,6}[-_]?\d{2,}", text or "")
    return m.group(0) if m else ""


def collect_type_text(page, row_text) -> str:
    """任务类型判断素材：列表行文本 + 新页面标题 + 各 frame 正文前 500 字"""
    parts = [row_text or ""]
    try:
        parts.append(page.title() or "")
    except Exception:
        pass
    for fr in page.frames:
        try:
            parts.append(fr.evaluate(
                "() => (document.body ? document.body.innerText : '').slice(0, 500)"))
        except Exception:
            pass
    return "".join(parts)


def decide_radio_keyword(cfg, type_text) -> str:
    """按 radio_rules 的 if_type_contains 逐条匹配，未命中用 default_radio"""
    for rule in cfg.get("radio_rules", []):
        k = str(rule.get("if_type_contains", ""))
        if k and k in type_text:
            return str(rule.get("then_pick", ""))
    return cfg.get("default_radio", "")


def _cfg_path(cfg, key) -> Path:
    """把 config 中的相对路径锚定到 exe（或脚本）所在目录，而不是进程工作目录"""
    p = Path(cfg[key])
    return p if p.is_absolute() else BASE_DIR / p


def sanitize_filename(name: str) -> str:
    """清洗 Windows 非法文件名字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip(" .")
    return name[:120] or "file"


def download_attachment(ctx, page, cfg, cat, task_no, logf=log):
    """下载详情页附件，保存到 附件/类别名/任务号_文件名"""
    if not cfg.get("download_attachments"):
        return None
    keywords = ("附件", "下载", "download")
    for fr in page.frames:
        try:
            anchors = fr.locator('a[href]')
            n = anchors.count()
        except Exception:
            continue
        for i in range(n):
            a = anchors.nth(i)
            try:
                b = _el_brief(a)
                txt = _norm_text(b.get("text", ""))
                href = a.get_attribute("href") or ""
                has_dl_attr = a.get_attribute("download") is not None
            except Exception:
                continue
            if href.startswith(("javascript", "mailto")) or href in ("", "#"):
                continue
            if not (has_dl_attr or any(k in txt for k in keywords)):
                continue
            before_url = page.url
            try:
                with page.expect_download(timeout=20000) as dl_info:
                    a.click()
                dl = dl_info.value
            except Exception as e:
                logf("附件下载触发失败（%s）：%s" % (href, e))
                try:
                    if page.url != before_url:
                        page.go_back(wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
                continue
            fname = sanitize_filename(dl.suggested_filename or ("%s.bin" % task_no))
            dest_dir = _cfg_path(cfg, "attachment_dir") / sanitize_filename(cat["name"])
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / ("%s_%s" % (sanitize_filename(task_no), fname))
            dl.save_as(str(dest))
            logf("附件已保存：%s" % dest, echo=True)
            return str(dest)
    logf("未找到可下载的附件（按配置跳过）")
    return None


def process_single_task(ctx, cfg, cat, list_page, row, logf=log):
    """
    处理一条任务：开新标签(或同页跳转) → 签收 → 选radio → 下载附件 → 提交。
    返回 (是否成功, 当前任务页, 打开过的页面列表, 是否新开标签)
    """
    task_no = row.get("task_no") or _task_no_from_row(row["text"]) or "未识别任务号"
    logf("—— 任务 %s 开始 ——（类别：%s）" % (task_no, cat["name"]), echo=True)
    shot(list_page, "%s_列表行" % task_no)

    opened = []          # 本任务期间打开的页面，最后统一关闭
    cur = list_page
    ok = False
    try:
        try:
            # ① 点击任务链接（自动兼容新标签页 / 当前页跳转）
            np, is_new = open_after_click(ctx, list_page, row["link"].click, 5000)
            if np:
                cur = np
                opened.append(np)
                logf("任务页在新标签页打开：%s" % cur.url)
            else:
                logf("任务页在当前页跳转：%s" % cur.url)
            shot(cur, "%s_打开" % task_no)

            # ② 签收（已签收过的页面找不到签收按钮，直接继续）
            hit = find_button(cur, cfg["sign_keywords"])
            if hit:
                logf("点击签收按钮（命中关键词：%s，文字：%s）" % (hit[0], hit[3].get("text", "")),
                     echo=True)
                np2, is_new2 = open_after_click(ctx, cur, hit[2].click, 3000)
                if np2:
                    cur = np2
                    opened.append(np2)
                shot(cur, "%s_签收后" % task_no)
            else:
                logf("未找到签收按钮（可能已签收过），继续后续步骤")

            # ③ 按任务类型勾选 radio
            type_text = collect_type_text(cur, row["text"])
            pick = decide_radio_keyword(cfg, type_text)
            logf("任务类型素材（前60字）：%s" % type_text[:60].replace("\n", " "))
            if pick:
                if not pick_radio(cur, pick):
                    logf("按规则未匹配到 radio“%s”，尝试 default_radio" % pick)
                    if not pick_radio(cur, cfg["default_radio"]):
                        raise TaskError("找不到匹配的单选框（规则关键词：%s / 默认：%s）"
                                        % (pick, cfg["default_radio"]))
                logf("已勾选单选框：%s" % pick, echo=True)
            else:
                logf("无 radio 规则命中，跳过选择", echo=True)
            shot(cur, "%s_选radio" % task_no)

            # ④ 下载附件
            download_attachment(ctx, cur, cfg, cat, task_no, logf)
            shot(cur, "%s_下载后" % task_no)

            # ⑤ 提交（原生 confirm 由 page.on("dialog") 自动接受）
            hit = find_button(cur, cfg["submit_keywords"])
            if not hit:
                raise TaskError("找不到提交按钮（关键词：%s）" % cfg["submit_keywords"])
            logf("点击提交按钮（命中关键词：%s，文字：%s）" % (hit[0], hit[3].get("text", "")),
                 echo=True)
            hit[2].click()
            try:
                cur.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            shot(cur, "%s_提交后" % task_no)
            ok = True
            logf("任务 %s 处理成功" % task_no, echo=True)
        except TaskError as e:
            logf("任务 %s 失败：%s" % (task_no, e), echo=True)
            logf(traceback.format_exc())
            shot(cur, "%s_异常" % task_no)
        except (FatalError, ConfigError):
            raise
        except Exception as e:
            logf("任务 %s 异常：%s" % (task_no, e), echo=True)
            logf(traceback.format_exc())
            shot(cur, "%s_异常" % task_no)
    finally:
        # 无论成功失败：关闭本任务打开的标签页（列表页除外），避免标签堆积
        _close_pages(opened, list_page)
    return ok


def _close_pages(opened, list_page):
    for pg in opened:
        if pg is list_page:
            continue
        try:
            pg.close()
        except Exception:
            pass


def process_category(ctx, cfg, cat, list_page, summary, logf=log):
    """
    处理一个类别：永远取列表第一行 → 处理 → 关标签 → 重新打开列表 → 循环，直到列表为空。
    """
    stat = summary.setdefault(cat["name"], {"成功": [], "失败": [], "跳过": [], "错误": ""})
    seen = set()   # 已出现过的任务号：防止同一行反复处理造成死循环
    logf("========== 开始类别：%s（%s） ==========" % (cat["name"], cat["url"]), echo=True)

    try:
        resp = safe_goto(list_page, cat["url"], "任务列表页")
        if resp is not None and getattr(resp, "status", 0) >= 400:
            stat["错误"] = "列表页返回 HTTP %d，请用记事本检查该类别 URL 是否写对" % resp.status
            logf("类别 %s：%s（%s）" % (cat["name"], stat["错误"], cat["url"]), echo=True)
            return
    except FatalError as e:
        stat["错误"] = str(e).split("\n")[0]
        logf("类别 %s 的列表页打不开，跳过该类别：%s" % (cat["name"], stat["错误"]), echo=True)
        return

    done = 0
    while done < int(cfg["max_tasks"]):
        # 运行中途被踢回登录页 → 重新登录后恢复队列
        if is_login_page(list_page):
            logf("检测到登录态失效，重新登录（需要人工完成 UKey）…", echo=True)
            try:
                do_login(ctx, list_page, cfg, logf)
            except FatalError as e:
                stat["错误"] = str(e).split("\n")[0]
                raise
            safe_goto(list_page, cat["url"], "任务列表页")

        row = find_first_task_row(list_page, cat)
        if not row:
            logf("类别 %s：列表已空（或未发现任务行），处理结束。" % cat["name"], echo=True)
            break

        task_no = _task_no_from_row(row["text"]) or "row%d" % (len(seen) + 1)
        if task_no in seen:
            stat["跳过"].append(task_no)
            logf("任务 %s 反复出现在列表首位且无法完成，终止该类别以免死循环。" % task_no, echo=True)
            break
        seen.add(task_no)

        ok = process_single_task(ctx, cfg, cat, list_page, {**row, "task_no": task_no}, logf)
        # 回到列表：无论刚才是同页跳转还是新标签页，都重新打开列表地址
        try:
            safe_goto(list_page, cat["url"], "任务列表页")
        except FatalError as e:
            stat["错误"] = str(e).split("\n")[0]
            logf("返回列表页失败：%s" % stat["错误"], echo=True)
            break

        (stat["成功"] if ok else stat["失败"]).append(task_no)
        done += 1
        lo, hi = cfg["task_delay_seconds"]
        delay = random.uniform(float(lo), float(hi))
        logf("随机等待 %.1f 秒（模拟人工节奏）…" % delay)
        try:
            list_page.wait_for_timeout(int(delay * 1000))
        except Exception:
            time.sleep(delay)
    logf("========== 类别 %s 结束：成功 %d，失败 %d，跳过 %d =========="
         % (cat["name"], len(stat["成功"]), len(stat["失败"]), len(stat["跳过"])), echo=True)


def print_summary(summary):
    print()
    print("=" * 62)
    print("处理汇总")
    print("=" * 62)
    all_failed = []
    for name, st in summary.items():
        line = "%s：成功 %d，失败 %d，跳过 %d" % (
            name, len(st["成功"]), len(st["失败"]), len(st["跳过"]))
        if st.get("错误"):
            line += "（类别错误：%s）" % st["错误"]
        print("  " + line)
        if st["失败"]:
            print("    失败任务号：%s" % "、".join(st["失败"]))
            all_failed += st["失败"]
        if st["跳过"]:
            print("    跳过任务号：%s" % "、".join(st["跳过"]))
    if all_failed:
        print("请结合 shots/ 截图与 logs/ 日志排查失败任务。")
    print("日志目录：%s" % LOG_DIR)
    print("截图目录：%s" % SHOTS_DIR)
    print("=" * 62)


def run_mode(cfg=None):
    """run 模式入口：登录 → 逐类别处理 → 汇总。返回 summary 供测试断言。"""
    cfg = cfg or load_config()
    print("连通性预检中…")
    precheck_urls(cfg)
    if cfg.get("headless"):
        print("提示：当前为无头模式（config.json headless=true），浏览器窗口不可见。")

    summary = {}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch_browser(p, cfg)
        try:
            ctx, list_page = ensure_login(browser, cfg, log)
            for cat in cfg["categories"]:
                process_category(ctx, cfg, cat, list_page, summary)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    print_summary(summary)
    return summary


# ================================================================ inspect 模式

def _fmt_text(t, limit=30) -> str:
    t = " ".join((t or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")


def inspect_dump(ctx) -> str:
    """
    打印当前所有页面 / 所有 frame 的元素清单（仅控制台输出，不导出文件）。
    用途：照着输出确认按钮文字、radio、输入框，填写 config.json。
    """
    lines = []
    for pi, page in enumerate(ctx.pages):
        try:
            title = page.title()
        except Exception:
            title = "?"
        lines.append("=" * 66)
        lines.append("[页面%d] %s   标题：%s" % (pi, page.url, _fmt_text(title, 40)))
        for fi, fr in enumerate(page.frames):
            main_tag = "" if fr == page.main_frame else "[iframe] "
            lines.append("-" * 66)
            lines.append("  %sframe#%d  %s" % (main_tag, fi, fr.url))
            # 按钮与链接
            count = 0
            for sel in _BUTTON_SELECTORS:
                try:
                    locs = fr.locator(sel)
                    n = locs.count()
                except Exception:
                    continue
                for i in range(n):
                    lo = locs.nth(i)
                    b = _el_brief(lo)
                    if not b or not b.get("visible") or not (b.get("text") or "").strip():
                        continue
                    lines.append("    [%s] %s" % (b["tag"], _fmt_text(b["text"])))
                    count += 1
                    if count >= 40:
                        lines.append("    …（按钮/链接超过 40 个，已截断）")
                        break
                if count >= 40:
                    break
            # radio
            try:
                radios = fr.locator('input[type="radio"]')
                for i in range(min(radios.count(), 30)):
                    lo = radios.nth(i)
                    name = lo.get_attribute("name") or ""
                    value = lo.get_attribute("value") or ""
                    label = _fmt_text(radio_label_text(fr, lo), 30)
                    checked = "（已选）" if lo.is_checked() else ""
                    lines.append("    [radio] name=%s value=%s label=%s %s"
                                 % (name, value, label, checked))
            except Exception:
                pass
            # 主要输入框
            try:
                inputs = fr.locator(
                    'input[type="text"], input[type="password"], input:not([type])')
                for i in range(min(inputs.count(), 30)):
                    lo = inputs.nth(i)
                    b = _el_brief(lo)
                    if not b or not b.get("visible"):
                        continue
                    lines.append("    [input:%s] name=%s id=%s placeholder=%s"
                                 % (lo.get_attribute("type") or "text",
                                    lo.get_attribute("name") or "-",
                                    lo.get_attribute("id") or "-",
                                    _fmt_text(lo.get_attribute("placeholder") or "-", 20)))
            except Exception:
                pass
    lines.append("=" * 66)
    text = "\n".join(lines)
    print(text)
    return text


# ================================================================ probe 模式（inspect + debug 合并）
# 探测 = 工作流引擎的一个动作（uses: probe）；交互式探测复用同一套登录与扫描逻辑，
# 输出选择器建议和可直接粘贴的 YAML 草稿，不再维护独立代码路径。

PROBE_HELP = """probe 模式命令（合并原 inspect / debug）：
  [回车]          扫描当前全部页面，输出元素清单 + 选择器建议
  <网址>          打开该网址并扫描，如 http://内网IP/list
  t=<关键词>      文字匹配测试（自动容错空格），如 t=签收
  <选择器>        选择器测试，如 input[type=submit]、//button
  w=<文件名>      把最近一次扫描保存为 YAML 草稿（含 suggested_steps）
  #<序号>         切换选择器测试的 frame（序号见 list）
  list            列出当前所有页面与 frame
  q               退出"""


def _debug_test_selector(target, sel):
    """在目标 frame 上执行选择器测试；返回输出行"""
    out = []
    try:
        loc = target.locator(sel)
        n = loc.count()
    except Exception as e:
        return ["  选择器不合法或执行失败：%s" % str(e).split("\n")[0]]
    out.append("  命中 %d 个" % n)
    for i in range(min(n, 20)):
        lo = loc.nth(i)
        b = _el_brief(lo)
        if not b:
            out.append("  [%d] <读取失败>" % i)
            continue
        out.append("  [%d] <%s> %s [%s]" % (
            i, b.get("tag", "?"), _fmt_text(b.get("text", ""), 40),
            "可见" if b.get("visible") else "不可见"))
    if n == 0:
        out.append("  建议：以下是文字包含“%s”的元素（用 t=%s 可按文字匹配）：" % (sel, sel))
        found = 0
        for kw, fr, lo, b in find_button_all(target, [sel]):
            out.append("    <%s> %s" % (b.get("tag", "?"), _fmt_text(b.get("text", ""), 40)))
            found += 1
            if found >= 15:
                break
        if not found:
            out.append("    （没有找到文字相近的元素）")
    return out


def _login_from_cfg(cfg) -> dict:
    return {
        "url": cfg.get("sso_login_url") or "",
        "username": cfg.get("sso_username") or "",
        "password": cfg.get("sso_password") or "",
        "username_selector": cfg.get("login_username_selector") or "",
        "password_selector": cfg.get("login_password_selector") or "",
        "button_text": cfg.get("login_button_keywords") or ["登录"],
        "ukey_wait": True,
        "success_url": cfg.get("base_url") or "",
    }


def _interactive_workflow(path=None) -> dict:
    """交互模式（probe/record）的工作流来源：--workflow > config.json > workflow.yaml"""
    if path:
        wf = load_yaml_file(path) or {}
        if not isinstance(wf, dict):
            raise ConfigError("工作流文件顶层应为映射（含 login/tasks 等字段）")
        return wf
    cfg = load_config_optional()
    wf = {"name": "交互模式", "settings": {"screenshot": False}}
    if str(cfg.get("sso_login_url") or "").strip() and str(cfg.get("base_url") or "").strip():
        wf["login"] = _login_from_cfg(cfg)
        wf["browser"] = {"channel": cfg.get("channel") or "chrome",
                         "headless": bool(cfg.get("headless"))}
        return wf
    default_yaml = BASE_DIR / "workflow.yaml"
    if default_yaml.exists():
        try:
            base = load_yaml_file(default_yaml) or {}
        except ConfigError:
            base = {}
        if isinstance(base, dict):
            if base.get("login"):
                wf["login"] = base["login"]
            if base.get("browser"):
                wf["browser"] = base["browser"]
    return wf


def probe_mode(path=None):
    """交互式探测：登录后 [回车] 扫描 / <网址> / t=文字 / 选择器 / w=保存 / list / #N / q"""
    wf = dict(_interactive_workflow(path))
    wf["browser"] = dict(wf.get("browser") or {})
    wf["browser"]["headless"] = False          # 探测必须可见窗口
    cfg = load_config_optional()
    login = wf.get("login")
    if login:
        if not isinstance(login, dict):
            raise ConfigError("workflow.yaml 的 login 应为映射（含 url/username 等字段）")
        _merge_login_cfg(cfg, login)
    elif not (str(cfg.get("base_url") or "").strip()
              and str(cfg.get("sso_login_url") or "").strip()):
        raise ConfigError(
            "probe 模式需要登录配置：请在 config.json 填 base_url/sso_login_url，"
            "或在 workflow.yaml 中写 login: 段。")

    engine = WorkflowEngine(wf, cfg)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch_browser(p, cfg, force_headed=True)
        try:
            ctx, page = workflow_login(cfg, wf, engine, browser)
            engine.ctx, engine.current = ctx, page
            frame_idx = 0
            last_probe = None
            print()
            print("已进入 probe 模式（原 inspect + debug 合并）。")
            print(PROBE_HELP)
            while True:
                try:
                    line = input("probe> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                low = line.lower()
                if not line:
                    last_probe = collect_probe(ctx, all_pages=True)
                    probe_report_text(last_probe)
                    continue
                if low == "q":
                    break
                if low == "list":
                    for pi, pg in enumerate(ctx.pages):
                        mark = " ←当前" if pg is engine.current else ""
                        print("  页面%d: %s%s" % (pi, pg.url, mark))
                        for fi, fr in enumerate(pg.frames):
                            tag = "" if fr == pg.main_frame else "[iframe] "
                            print("    frame#%d %s%s" % (fi, tag, fr.url))
                    continue
                if line.startswith("#"):
                    try:
                        frame_idx = int(line[1:])
                        print("  已切换到 frame#%d（%s）"
                              % (frame_idx, engine.current.frames[frame_idx].url))
                    except Exception:
                        print("  frame 序号无效，请先执行 list 查看。")
                    continue
                if line.startswith("w="):
                    name = line[2:].strip() or None
                    data = last_probe or collect_probe(ctx, all_pages=True)
                    p2 = write_probe_file(data, name)
                    print("  已保存探测草稿：%s（probe + suggested_steps）" % p2)
                    continue
                if line.startswith("t="):
                    kw = line[2:].strip()
                    print("  文字匹配“%s”结果：" % kw)
                    hits = find_button_all(engine.current, [kw])
                    if not hits:
                        print("  命中 0 个。文字包含该词的元素：")
                        for fr in engine.current.frames:
                            for lo, b in _all_textable(fr):
                                if _norm_text(kw) in _norm_text(b.get("text", "")):
                                    print("    <%s> %s" % (b.get("tag", "?"),
                                                           _fmt_text(b.get("text", ""), 40)))
                        continue
                    for kw2, fr, lo, b in hits:
                        ftag = "" if fr == engine.current.main_frame else "[iframe] "
                        print("  %s<%s> %s [%s]" % (
                            ftag, b.get("tag", "?"), _fmt_text(b.get("text", ""), 40),
                            "可见" if b.get("visible") else "不可见"))
                    continue
                if "://" in line or line.startswith("www."):
                    target_url = line if "://" in line else "http://" + line
                    try:
                        safe_goto(engine.current, target_url, "页面")
                        last_probe = collect_probe(ctx, all_pages=True)
                        probe_report_text(last_probe)
                    except (FatalError, ConfigError) as ex:
                        print("  打开失败：%s" % str(ex).split("\n")[0])
                    continue
                try:
                    target = engine.current.frames[frame_idx]
                except Exception:
                    target = engine.current.main_frame
                for row in _debug_test_selector(target, line):
                    print(row)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _all_textable(fr):
    """收集 frame 内全部可文字匹配元素（debug 的近似建议用）"""
    out = []
    for sel in _BUTTON_SELECTORS:
        try:
            for lo in fr.locator(sel).all():
                b = _el_brief(lo)
                if b:
                    out.append((lo, b))
        except Exception:
            continue
    return out


# ================================================================ record 模式

def record_mode(path=None, out=None, url=None):
    """独立的录制入口：登录（复用 workflow.login 或 config.json）→ 打开 url → 录制。
    产物是 workflow.yaml 片段（而非 Python 代码），可直接回放。"""
    wf = dict(_interactive_workflow(path))
    wf["name"] = wf.get("name") or "录制流程"
    wf["settings"] = dict(wf.get("settings") or {})
    wf["settings"]["screenshot"] = False       # 录制时不刷截图
    wf["browser"] = dict(wf.get("browser") or {})
    wf["browser"]["headless"] = False          # 录制必须可见窗口
    steps = []
    if url:
        steps.append({"uses": "goto", "with": {"url": url}})
    rec = {}
    if out:
        rec["file"] = out
    steps.append({"uses": "record", "with": rec})
    wf["steps"] = steps
    wf.pop("tasks", None)
    return workflow_mode(wf=wf)


# ================================================================ demo（本地彩排）

def demo_mode():
    """菜单 6：用内置示例流程连接本地 mock_platform.py（127.0.0.1:8899）全流程彩排"""
    base = "http://127.0.0.1:8899"
    try:
        from urllib.request import urlopen
        urlopen(base + "/status", timeout=3).read()
    except Exception:
        print("\n未检测到本地 mock 平台。请先在另一个窗口运行：python mock_platform.py\n")
        return
    wf = {
        "name": "本地 mock 彩排",
        "settings": {
            "max_tasks": 1,
            "sign_keywords": ["签收", "受理"],
            "submit_keywords": ["提交", "确定"],
            "default_radio": "同意",
            "radio_rules": [
                {"if_type_contains": "报修", "then_pick": "同意"},
                {"if_type_contains": "故障", "then_pick": "同意"},
                {"if_type_contains": "申请", "then_pick": "驳回"},
            ],
            "task_delay_seconds": [1, 2],
        },
        "browser": {"channel": "chrome", "headless": False},
        "login": {
            "url": base + "/login", "username": "admin", "password": "123456",
            "username_selector": "input[name='username']",
            "password_selector": "input[name='password']",
            "button_text": ["登录", "登 录"],
            "ukey_wait": True, "success_url": base + "/home",
        },
        "steps": [{"uses": "log", "with": {"message": "本地彩排开始（mock 平台）"}}],
        "tasks": [
            {"name": "类别一", "url": base + "/list1_page", "steps": [
                {"uses": "click_row_link"},
                {"uses": "click_text", "with": {"text": ["签收", "受理"], "optional": True}},
                {"uses": "pick_radio_by_rule"},
                {"uses": "download", "with": {"optional": True}},
                {"uses": "click_text", "with": {"text": ["提交", "确定"]}},
                {"uses": "close_task_page"},
            ]},
            {"name": "类别二（iframe）", "url": base + "/list2_page", "steps": [
                {"uses": "click_row_link"},
                {"uses": "click_text", "with": {"text": ["签收", "受理"], "optional": True}},
                {"uses": "pick_radio_by_rule"},
                {"uses": "download", "with": {"optional": True}},
                {"uses": "click_text", "with": {"text": ["提交", "确定"]}},
                {"uses": "close_task_page"},
            ]},
        ],
    }
    print("\n即将连接本地 mock 平台彩排（UKey 环节请人工点一下模拟按钮）。")
    return workflow_mode(wf=wf)


# ================================================================ 内嵌 YAML 解析器
# 纯标准库实现的 YAML 子集解析器（无第三方依赖，内网离线可用）。
# 由 tests/_yaml_fuzz.py 与 PyYAML 做了 8000 例随机对拍 + 边界用例验证。
# 若运行环境恰好装有 PyYAML，parse_yaml() 会优先使用它，行为完全一致。

class YAMLError(Exception):
    pass


# YAML 1.1 风格数字识别：整数为纯十进制；浮点须带小数点、或带符号指数（如 1.0e3 / 1e+3），
# 这样 "1e3"、"007" 之类会像 PyYAML 一样保持字符串，避免把版本号/编号误转成数字。
_INT_RE = re.compile(r"^[-+]?[0-9][0-9_]*$")
_FLOAT_RE = re.compile(
    r"^[-+]?(?:[0-9][0-9_]*\.[0-9_]*(?:[eE][-+]?[0-9]+)?"
    r"|\.[0-9_]+(?:[eE][-+]?[0-9]+)?"
    r"|[0-9][0-9_]*[eE][-+][0-9]+)$")


def _strip_comment(s):
    out, quote, i = [], None, 0
    while i < len(s):
        c = s[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(s):
                out.append(s[i + 1]); i += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c; out.append(c)
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip(" 	")


def _scalar(tok):
    t = tok.strip(" \t")
    if t == "":
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        inner = t[1:-1]
        if t[0] == '"':
            return (inner.replace('\\"', '"').replace("\\n", "\n")
                    .replace("\\t", "\t").replace("\\\\", "\\"))
        return inner.replace("''", "'")
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if _INT_RE.match(t):
        return int(t.replace("_", ""))
    if _FLOAT_RE.match(t):
        return float(t.replace("_", ""))
    return t


def _split_top(s, sep=","):
    parts, buf, depth, quote, i = [], [], 0, None, 0
    while i < len(s):
        c = s[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(s):
                buf.append(s[i + 1]); i += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c; buf.append(c)
        elif c in "[{":
            depth += 1; buf.append(c)
        elif c in "]}":
            depth -= 1; buf.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _find_colon(s):
    depth, quote, i = 0, None, 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < len(s):
                i += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == ":" and depth == 0:
            if i + 1 >= len(s) or s[i + 1] in " \t":
                return i
        i += 1
    return -1


def _is_quoted(s):
    return len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'"


def _flow_or_scalar(s):
    s = s.strip(" 	")
    if s[:1] == "[" and s[-1:] == "]":
        inner = s[1:-1].strip(" 	")
        return [] if not inner else [_flow_or_scalar(p) for p in _split_top(inner)]
    if s[:1] == "{" and s[-1:] == "}":
        inner = s[1:-1].strip(" 	")
        if not inner:
            return {}
        d = {}
        for p in _split_top(inner):
            ci = _find_colon(p)
            if ci < 0:
                raise YAMLError("行内映射缺少冒号：%s" % p)
            k = p[:ci].strip(" 	")
            d[_scalar(k) if _is_quoted(k) else k] = _flow_or_scalar(p[ci + 1:])
        return d
    return _scalar(s)


def _is_seq(text):
    return text == "-" or text.startswith("- ")


class _Parser:
    def __init__(self, text):
        raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.raw = raw
        self.toks = []           # [indent, content, raw_idx]
        for idx, line in enumerate(raw):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            content = _strip_comment(line.strip(" \t"))
            if content == "":
                continue
            self.toks.append([indent, content, idx])
        self._expand_nested_seqs()

    @staticmethod
    def _quoted_closed(s):
        q = s[:1]
        i = 1
        while i < len(s):
            c = s[i]
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                return True
            i += 1
        return False

    @staticmethod
    def _join_quoted(parts):
        out = parts[0]
        for p in parts[1:]:
            if p == "":
                out += "\n"
            elif out.endswith("\n"):
                out += p
            else:
                out += " " + p
        return out

    def _quoted_scalar(self, first, i):
        """跨行的单/双引号标量：续读后续物理行直到引号闭合，避免静默截断"""
        if self._quoted_closed(first):
            return _scalar(first), i + 1
        raw_idx = self.toks[i][2]
        parts, last, j = [first], raw_idx, raw_idx + 1
        while j < len(self.raw):
            parts.append(self.raw[j].strip(" \t"))
            last = j
            if self._quoted_closed(self._join_quoted(parts)):
                break
            j += 1
        k = 0
        while k < len(self.toks) and self.toks[k][2] <= last:
            k += 1
        return _scalar(self._join_quoted(parts)), k

    def _expand_nested_seqs(self):
        """把 '- - x' 这类"序列项内联子序列"展开成两行，便于统一解析：
        (indent, '- - x') -> (indent, '-') + (indent+2, '- x')"""
        changed = True
        while changed:
            changed = False
            out = []
            for ind, text, ridx in self.toks:
                if _is_seq(text):
                    after = text[1:]
                    lead = len(after) - len(after.lstrip(" "))
                    rest = after.strip()
                    if rest.startswith("- ") or rest == "-":
                        item_indent = ind + 1 + lead
                        out.append([ind, "-", ridx])
                        out.append([item_indent, rest, ridx])
                        changed = True
                        continue
                out.append([ind, text, ridx])
            self.toks = out

    # ---------- 块标量 ----------
    def _block_scalar(self, raw_idx, indent, marker):
        style, chomp = marker[0], marker[1:]
        coll, last = [], raw_idx
        j = raw_idx + 1
        while j < len(self.raw):
            line = self.raw[j]
            if line.strip() == "":
                coll.append(None)      # 占位，稍后裁剪
                j += 1
                continue
            ind = len(line) - len(line.lstrip(" "))
            if ind <= indent:
                break
            coll.append((ind, line))
            last = j
            j += 1
        # 去掉尾部空行
        while coll and coll[-1] is None:
            coll.pop()
        base = min(ind for ind, _ in coll) if coll else 0
        body = ["" if c is None else c[1][base:] for c in coll]
        if style == "|":
            s = "\n".join(body)
        else:
            folded, prev_blank = [], False
            for b in body:
                if b.strip() == "":
                    folded.append("\n"); prev_blank = True
                else:
                    folded.append(b.strip() if not folded else " " + b.strip())
                    prev_blank = False
            s = "".join(folded)
        if chomp == "-":
            s = s.rstrip("\n")
        elif chomp == "+":
            s += "\n"
        else:
            s = s.rstrip("\n") + "\n"
        i = 0
        while i < len(self.toks) and self.toks[i][2] <= last:
            i += 1
        return s, i

    def _consume_entry(self, mapping, content, i, indent):
        ci = _find_colon(content)
        if ci < 0:
            raise YAMLError("不是合法的 key: value：%s" % content)
        kb = content[:ci].strip(" 	")
        key = _scalar(kb) if _is_quoted(kb) else kb
        valstr = content[ci + 1:].strip(" 	")
        if valstr in ("|", "|-", "|+", ">", ">-", ">+"):
            val, i = self._block_scalar(self.toks[i][2], indent, valstr)
            mapping[key] = val
            return i
        if valstr == "":
            i += 1
            if i < len(self.toks) and self.toks[i][0] > indent:
                val, i = self.parse_node(i, self.toks[i][0])
            elif i < len(self.toks) and self.toks[i][0] == indent and _is_seq(self.toks[i][1]):
                val, i = self.parse_seq(i, indent)
            else:
                val = None
            mapping[key] = val
            return i
        if valstr[:1] in ("'", '"'):
            val, i = self._quoted_scalar(valstr, i)
            mapping[key] = val
            return i
        mapping[key] = _flow_or_scalar(valstr)
        return i + 1

    def parse_seq(self, i, indent):
        result = []
        while i < len(self.toks):
            tind, text, _ = self.toks[i]
            if tind != indent or not _is_seq(text):
                break
            after = text[1:]
            lead = len(after) - len(after.lstrip(" \t"))
            rest = after.strip(" \t")
            if rest == "":
                i += 1
                if i < len(self.toks) and self.toks[i][0] > indent:
                    val, i = self.parse_node(i, self.toks[i][0])
                else:
                    val = None
                result.append(val)
            elif _find_colon(rest) >= 0 and rest[:1] not in ("[", "{"):
                item_indent = indent + 1 + lead
                m = {}
                i = self._consume_entry(m, rest, i, item_indent)
                while i < len(self.toks) and self.toks[i][0] == item_indent \
                        and not _is_seq(self.toks[i][1]):
                    i = self._consume_entry(m, self.toks[i][1], i, item_indent)
                result.append(m)
            elif rest[:1] in ("'", '"'):
                val, i = self._quoted_scalar(rest, i)
                result.append(val)
            else:
                result.append(_flow_or_scalar(rest))
                i += 1
        return result, i

    def parse_map(self, i, indent):
        result = {}
        while i < len(self.toks):
            tind, text, lineno = self.toks[i]
            if tind < indent:
                break
            if tind > indent or _is_seq(text):
                break
            i = self._consume_entry(result, text, i, indent)
        return result, i

    def parse_node(self, i, indent):
        if i < len(self.toks) and _is_seq(self.toks[i][1]):
            return self.parse_seq(i, indent)
        return self.parse_map(i, indent)

    def run(self):
        if not self.toks:
            return None
        first = self.toks[0]
        if not _is_seq(first[1]) and _find_colon(first[1]) < 0:
            return _flow_or_scalar(first[1])
        val, _ = self.parse_node(0, first[0])
        return val


def _yaml_parse(text):
    return _Parser(text).run()


def parse_yaml(text):
    """解析 YAML 文本。优先 PyYAML；没有则用内置解析器。"""
    try:
        import yaml as _pyyaml            # 环境里若恰好有 PyYAML 就用它
        return _pyyaml.safe_load(text)
    except ImportError:
        return _yaml_parse(text)


def load_yaml_file(path):
    p = Path(path)
    if not p.exists():
        raise ConfigError("未找到流程文件：%s" % p)
    try:
        return parse_yaml(p.read_text(encoding="utf-8-sig"))
    except YAMLError as e:
        raise ConfigError("YAML 语法错误（%s）：%s\n请检查缩进是否统一、冒号后是否有空格。" % (p.name, e))
    except Exception as e:
        raise ConfigError("无法读取流程文件 %s：%s" % (p.name, e))


# ---------------------------------------------------------------- YAML 生成
# 供 probe / record 输出「可直接粘贴执行的步骤」用。生成的文本保证与内置解析器
# 往返兼容（PyYAML 亦能读）。

def _yaml_scalar(v):
    """把 Python 标量转成 YAML 标量文本（该加引号的加引号，防止被误解析）"""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    s = str(v)
    need_quote = (
        s == "" or s != s.strip()
        or any(ch in s for ch in "\n\r\t")
        or s[0] in "-[]{#&*!|>%@`'\""
        or ": " in s or s.endswith(":")
        or " #" in s
        or s.lower() in ("true", "false", "null", "~", "yes", "no", "on", "off")
        or _INT_RE.match(s) is not None or _FLOAT_RE.match(s) is not None)
    if need_quote:
        return '"%s"' % (s.replace("\\", "\\\\").replace('"', '\\"')
                         .replace("\n", "\\n").replace("\t", "\\t"))
    return s


def _dump_node(value, indent):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}\n"
        out = []
        for k, v in value.items():
            key = _yaml_scalar(k)
            if isinstance(v, dict):
                out.append("%s%s: {}\n" % (pad, key) if not v
                           else "%s%s:\n%s" % (pad, key, _dump_node(v, indent + 1)))
            elif isinstance(v, (list, tuple)):
                out.append("%s%s: []\n" % (pad, key) if not v
                           else "%s%s:\n%s" % (pad, key, _dump_node(v, indent + 1)))
            else:
                out.append("%s%s: %s\n" % (pad, key, _yaml_scalar(v)))
        return "".join(out)
    if isinstance(value, (list, tuple)):
        if not value:
            return pad + "[]\n"
        inner = "  " * (indent + 1)
        out = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                body = _dump_node(item, indent + 1)
                if body.startswith(inner):
                    body = body[len(inner):]
                out.append("%s- %s" % (pad, body))
            elif isinstance(item, dict):
                out.append("%s- {}\n" % pad)
            elif isinstance(item, (list, tuple)):
                out.append("%s- []\n" % pad)
            else:
                out.append("%s- %s\n" % (pad, _yaml_scalar(item)))
        return "".join(out)
    return pad + _yaml_scalar(value) + "\n"


def dump_yaml(value):
    """dict/list/标量 → YAML 文本（解析器往返一致，实测见 selftest）"""
    return _dump_node(value, 0)



# ================================================================ 工作流引擎
# 用 YAML 描述步骤（类 GitHub Actions）：uses + with + if + id，支持
# for_each/while/repeat 循环、{{ }} 变量模板、exists/visible 等条件函数。
# 目标：内网页面再复杂，也能靠改 YAML 适配，不必改 Python 代码。

_WF_VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class _LoopBreak(Exception):
    pass


class _LoopContinue(Exception):
    pass


def _dig(val, path):
    """按点路径取值：a.b.0.c"""
    for part in str(path).split("."):
        if isinstance(val, dict):
            val = val.get(part)
        elif isinstance(val, (list, tuple)):
            try:
                val = val[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return val


def render(value, vars_):
    """模板渲染：{{ x }} / {{ a.b }}。整串为单个模板时返回原始类型（数字/布尔）。"""
    if isinstance(value, dict):
        return {k: render(v, vars_) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, vars_) for v in value]
    if not isinstance(value, str):
        return value
    m = _WF_VAR_RE.fullmatch(value.strip())
    if m:
        return _dig(vars_, m.group(1).strip())
    return _WF_VAR_RE.sub(lambda mm: "" if _dig(vars_, mm.group(1).strip()) is None
                          else str(_dig(vars_, mm.group(1).strip())), value)


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s not in ("", "0", "false", "no", "none", "null", "off")


_FUNC_RE = re.compile(
    r"^(exists|visible|absent|has_text|count|text|attr|value|page_count|url|title)"
    r"\s*\((.*)\)$", re.S)


def _resolve(text, vars_, engine=None):
    """把条件中的一侧解析成 Python 值：引号→字符串、bool/null/数字→字面量、
    函数调用（exists/visible/...）→求值、形如 a.b 的裸变量名→变量值、其余→模板渲染。"""
    t = str(text).strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    if _INT_RE.match(t) or _FLOAT_RE.match(t):
        return _lit_or_var(t, vars_)
    fm = _FUNC_RE.match(t)
    if fm and engine is not None:
        return _eval_func(fm.group(1), fm.group(2), engine)
    if _WF_VAR_RE.search(t):
        return render(t, vars_)
    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[\w]+)*", t):
        val = _dig(vars_, t)
        return val if val is not None else t
    return render(t, vars_)


def eval_condition(cond, engine):
    """条件求值：支持字符串比较式与结构化字典，以及 exists()/visible() 等函数。"""
    if cond is None:
        return True
    if isinstance(cond, bool):
        return cond
    if isinstance(cond, (int, float)):
        return cond != 0
    if isinstance(cond, dict):
        if "and" in cond:
            return all(eval_condition(c, engine) for c in cond["and"])
        if "or" in cond:
            return any(eval_condition(c, engine) for c in cond["or"])
        if "not" in cond:
            return not eval_condition(cond["not"], engine)
        for op in ("contains", "equals", "matches", "exists", "visible", "has_text"):
            if op in cond:
                args = cond[op]
                args = args if isinstance(args, list) else [args]
                args = [render(a, engine.vars) for a in args]
                if op == "contains":
                    return str(args[1]) in str(args[0]) if len(args) > 1 else _as_bool(args[0])
                if op == "equals":
                    return len(args) > 1 and args[0] == args[1]
                if op == "matches":
                    return len(args) > 1 and re.search(str(args[1]), str(args[0])) is not None
                if op == "exists":
                    return engine.selector_exists(str(args[0]))
                if op == "visible":
                    return engine.selector_visible(str(args[0]))
                if op == "has_text":
                    return engine.has_text(str(args[0]))
        raise ConfigError("无法识别的 if 条件：%r" % (cond,))

    s = str(cond).strip()
    if s.startswith("${{") and s.endswith("}}"):
        s = s[3:-2].strip()
    elif s.startswith("{{") and s.endswith("}}"):
        s = s[2:-2].strip()

    # 逻辑组合优先级最高：先拆 or/and/not，再解析单侧（否则 exists(a) or exists(b)
    # 会被函数正则的贪婪匹配误当成一次调用）
    for sep in (" or ", " || "):
        if sep in s:
            return any(eval_condition(p, engine) for p in s.split(sep))
    for sep in (" and ", " && "):
        if sep in s:
            return all(eval_condition(p, engine) for p in s.split(sep))
    if s.startswith("not "):
        return not eval_condition(s[4:], engine)

    # 函数式
    fm = _FUNC_RE.match(s)
    if fm:
        return _eval_func(fm.group(1), fm.group(2), engine)

    ops = [(" not contains ", "contains", True), (" contains ", "contains", False),
           (" not matches ", "matches", True), (" matches ", "matches", False),
           (" startswith ", "startswith", False), (" endswith ", "endswith", False),
           (" >= ", ">=", False), (" <= ", "<=", False), (" != ", "!=", False),
           (" == ", "==", False), (" > ", ">", False), (" < ", "<", False),
           (" in ", "in", False)]
    for token, op, neg in ops:
        if token in s:
            left, right = s.split(token, 1)
            lv, rv = _resolve(left, engine.vars, engine), _resolve(right, engine.vars, engine)
            res = _compare(lv, op, rv)
            return (not res) if neg else res
    return _as_bool(_resolve(s, engine.vars, engine))


def _eval_func(name, raw, engine):
    """求值条件函数：exists/visible/absent/has_text/page_count/url/title"""
    raw = (raw or "").strip()
    args = []
    if raw:
        for a in _split_top_args(raw):
            a = a.strip()
            if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'":
                args.append(a[1:-1])
            else:
                args.append(render(a, engine.vars))
    if name == "exists":
        return engine.selector_exists(_tpl_str(args, 0, engine))
    if name == "visible":
        return engine.selector_visible(_tpl_str(args, 0, engine))
    if name == "absent":
        return not engine.selector_exists(_tpl_str(args, 0, engine))
    if name == "has_text":
        return engine.has_text(_tpl_str(args, len(args) - 1, engine))
    if name == "count":
        return engine.count_elements(_tpl_str(args, 0, engine))
    if name == "text":
        return engine.get_text(_tpl_str(args, 0, engine))
    if name == "attr":
        return engine.get_attr(_tpl_str(args, 0, engine), _tpl_str(args, 1, engine))
    if name == "value":
        return engine.get_value(_tpl_str(args, 0, engine))
    if name == "page_count":
        return len(engine.ctx.pages)
    if name == "url":
        return engine.current.url
    if name == "title":
        return engine.current.title()
    raise ConfigError("无法识别的条件函数：%s" % name)


def _tpl_str(args, idx, engine):
    if idx < len(args):
        v = args[idx]
        return v if isinstance(v, str) else str(v)
    return ""


def _split_top_args(raw):
    return [p for p in _split_top(raw) if p.strip() != ""]


def _lit_or_var(text, vars_):
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return render(t, vars_)


def _compare(lv, op, rv):
    if op in (">", "<", ">=", "<="):
        try:
            lv, rv = float(lv), float(rv)
        except (TypeError, ValueError):
            if lv is None or rv is None:
                return False        # 未定义/空值与数字比较一律为假，避免字典序误判
            lv, rv = str(lv), str(rv)
    if op == "contains":
        return str(rv) in str(lv)
    if op == "matches":
        return re.search(str(rv), str(lv)) is not None
    if op == "startswith":
        return str(lv).startswith(str(rv))
    if op == "endswith":
        return str(lv).endswith(str(rv))
    if op == "in":
        try:
            return lv in rv
        except TypeError:
            return str(lv) in str(rv)
    if op == "==":
        return lv == rv or str(lv) == str(rv)
    if op == "!=":
        return not (lv == rv or str(lv) == str(rv))
    if op == ">":
        return lv > rv
    if op == "<":
        return lv < rv
    if op == ">=":
        return lv >= rv
    if op == "<=":
        return lv <= rv
    return False


# ---------------------------------------------------------------- 动作注册表

ACTIONS = {}


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


class WorkflowEngine:
    """执行 YAML 工作流；持有浏览器上下文、当前页面、变量与统计。"""

    def __init__(self, wf, cfg, logf=log):
        self.wf = wf or {}
        self.cfg = cfg or {}
        st = self.wf.get("settings") or {}
        self.settings = {
            "attachment_dir": st.get("attachment_dir", self.cfg.get("attachment_dir", "附件")),
            "task_delay_seconds": st.get("task_delay_seconds",
                                         self.cfg.get("task_delay_seconds", [1, 3])),
            "screenshot": st.get("screenshot", True),
            "max_tasks": st.get("max_tasks", self.cfg.get("max_tasks", 1)),
            "sign_keywords": st.get("sign_keywords", self.cfg.get("sign_keywords")),
            "submit_keywords": st.get("submit_keywords", self.cfg.get("submit_keywords")),
            "radio_rules": st.get("radio_rules", self.cfg.get("radio_rules", [])),
            "default_radio": st.get("default_radio", self.cfg.get("default_radio", "")),
            "download_attachments": st.get("download_attachments",
                                           self.cfg.get("download_attachments", True)),
            "dialog": st.get("dialog", self.cfg.get("dialog", "accept")),
            "dialog_text": st.get("dialog_text", self.cfg.get("dialog_text", "")),
        }
        self.logf = logf
        self.vars = dict(self.wf.get("env") or {})
        self.vars["settings"] = self.settings      # 便于在步骤里 {{ settings.xxx }} 引用
        self.ctx = None
        self.current = None            # 当前页面
        self.list_page = None
        self.row = None                # 当前列表行 {link, text, task_no}
        self.opened_pages = []         # 本任务/本流程打开的页面（用于统一关闭）
        self.state_path = STATE_PATH   # 本流程使用的登录态文件（可由 login.state_file 覆盖）
        self.shot_n = 0
        self.summary = {}

    # ---------- 页面/frame 定位 ----------
    def frames_of(self, page=None):
        page = page or self.current
        try:
            return list(page.frames)
        except Exception:
            return []

    def resolve_frame(self, spec=None, page=None):
        """把 frame 规格解析成 Frame：None/'auto'=全 frame 扫描、'main'、#N、url 子串"""
        page = page or self.current
        frames = self.frames_of(page)
        if not frames:
            raise TaskError("当前没有可用页面")
        if spec is None or spec in ("auto", "all", ""):
            return None                       # 交给调用方全扫描
        if spec == "main":
            return page.main_frame
        s = str(spec)
        if s.startswith("#"):
            s = s[1:]
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(frames):
                return frames[idx]
            raise TaskError("frame 序号 %d 超出范围（共 %d 个）" % (idx, len(frames)))
        for fr in frames:
            if s in (fr.url or ""):
                return fr
        raise TaskError("找不到 URL 包含 %r 的 frame" % spec)

    def locator(self, selector, frame=None, page=None):
        """返回 (frame, locator)。frame 为空时跨所有 frame 找第一个有命中的。"""
        fr = self.resolve_frame(frame, page)
        if fr is not None:
            return fr, fr.locator(selector)
        for f in self.frames_of(page):
            try:
                loc = f.locator(selector)
                if loc.count() > 0:
                    return f, loc
            except Exception:
                continue
        # 都没有命中：返回主 frame 的空 locator（便于上层报错）
        return self.frames_of(page)[0], self.frames_of(page)[0].locator(selector)

    def selector_exists(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc.count() > 0
        except Exception:
            return False

    def selector_visible(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return True
            return False
        except Exception:
            return False

    def count_elements(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc.count()
        except Exception:
            return 0

    def _first_locator(self, selector, frame=None):
        try:
            _, loc = self.locator(selector, frame)
            return loc if loc.count() > 0 else None
        except Exception:
            return None

    def get_text(self, selector, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.inner_text() if loc is not None else ""
        except Exception:
            return ""

    def get_attr(self, selector, attr, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.get_attribute(str(attr)) if loc is not None else None
        except Exception:
            return None

    def get_value(self, selector, frame=None):
        loc = self._first_locator(selector, frame)
        try:
            return loc.first.input_value() if loc is not None else ""
        except Exception:
            return ""

    def has_text(self, text, page=None):
        nk = _norm_text(text)
        for f in self.frames_of(page):
            try:
                body = f.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                continue
            if nk and nk in _norm_text(body):
                return True
        return False

    def snap(self, stage):
        if self.settings.get("screenshot") and self.current is not None:
            shot(self.current, stage)

    # ---------- 单步执行 ----------
    def run_steps(self, steps, vars_extra=None):
        if not steps:
            return
        if vars_extra:
            self.vars.update(vars_extra)
        for step in steps:
            self.run_step(step)

    def run_step(self, step):
        if not isinstance(step, dict):
            raise ConfigError("步骤必须是映射（含 uses 字段），实际为：%r" % (step,))
        name = step.get("name") or step.get("uses") or "?"
        uses = step.get("uses")
        if uses is None:
            raise ConfigError("步骤 %r 缺少 uses 字段" % name)
        if "if" in step and not eval_condition(step["if"], self):
            self.logf("跳过步骤 %s（条件不满足）" % name)
            return
        if uses not in ACTIONS:
            raise ConfigError("未知步骤 uses: %r（可用：%s）"
                              % (uses, "、".join(sorted(ACTIONS))))
        params = render(step.get("with") or {}, self.vars)
        try:
            result = ACTIONS[uses](self, params, step)
        except (_LoopBreak, _LoopContinue):
            raise
        except TaskError:
            if step.get("continue_on_error"):
                self.logf("步骤 %s 出错但已忽略（continue_on_error）" % name)
                return
            raise
        except (FatalError, ConfigError):
            raise                      # 致命错误与配置错误一律向上抛，不被单步容错吞掉
        except Exception as e:
            if step.get("continue_on_error"):
                self.logf("步骤 %s 异常但已忽略：%s" % (name, e))
                return
            self.logf(traceback.format_exc())
            raise TaskError("步骤 %s 执行失败：%s" % (name, e))
        if step.get("id"):
            self.vars.setdefault("steps", {})[step["id"]] = result
            self.vars["steps_" + step["id"]] = result
        if step.get("set") and isinstance(result, dict):
            self.vars.update(result)


# ---------------------------------------------------------------- 基础动作

@action("log")
def _a_log(e, p, s):
    e.logf(str(p.get("message", "")), echo=True)
    return None


@action("set_var")
def _a_set_var(e, p, s):
    name = p.get("name") or p.get("var")
    if not name:
        raise TaskError("set_var 需要 name")
    e.vars[str(name)] = p.get("value")
    e.logf("变量 %s = %r" % (name, p.get("value")))
    return {str(name): p.get("value")}


@action("parse_var")
def _a_parse_var(e, p, s):
    """用正则从文本抽取变量，如任务号：from 为空则用容器文本。"""
    text = p.get("from")
    if text is None:
        sel = p.get("selector")
        if sel:
            _, loc = e.locator(sel, p.get("frame"))
            text = loc.first.inner_text() if loc.count() else ""
        else:
            text = e.vars.get("row_text", "")
    m = re.search(str(p.get("regex", r"")), str(text or ""))
    val = (m.group(int(p.get("group", 0))) if m else p.get("default", ""))
    name = str(p.get("name") or "parsed")
    e.vars[name] = val
    e.logf("parse_var %s = %r" % (name, val))
    return {name: val}


@action("goto")
def _a_goto(e, p, s):
    url = p.get("url")
    if not url:
        raise TaskError("goto 需要 url")
    page = e.current
    if p.get("new_tab"):
        page = e.ctx.new_page()          # setup_context 已对新页面挂超时/弹窗策略
        opened = getattr(e, "opened_pages", None)
        if opened is not None:
            opened.append(page)
        e.current = page
    e.logf("打开：%s" % url, echo=True)
    resp = safe_goto(page, str(url), "页面")
    if p.get("wait_iframe") is not False:
        try:
            page.wait_for_timeout(300)
        except Exception:
            pass
    e.snap("goto")
    return resp


@action("wait_for_url")
def _a_wait_for_url(e, p, s):
    pattern = p.get("pattern") or p.get("url")
    if not pattern:
        raise TaskError("wait_for_url 需要 pattern（如 **/home 或正则）")
    if p.get("regex"):
        pattern = re.compile(str(pattern))
    try:
        e.current.wait_for_url(pattern, timeout=int(p.get("timeout", 20000)))
    except Exception as ex:
        raise TaskError("等待 URL 变化超时：%s（%s）" % (pattern, str(ex).split("\n")[0]))
    e.logf("URL 已变为：%s" % e.current.url)
    return e.current.url


@action("save_state")
def _a_save_state(e, p, s):
    """把当前登录态（cookies）持久化，默认沿用本流程的 state 文件"""
    path = p.get("path") or str(getattr(e, "state_path", STATE_PATH))
    path = Path(path)
    if not path.is_absolute():
        path = BASE_DIR / path
    _save_state(e.ctx, path, {"origin": _host_of(e.current.url), "workflow": "save_state"})
    e.logf("登录态已保存：%s" % path, echo=True)
    return str(path)


@action("set_dialog")
def _a_set_dialog(e, p, s):
    """设置原生弹窗策略：accept（默认）/ dismiss，prompt 可配 text"""
    mode = str(p.get("mode") or p.get("action") or "").lower()
    if mode:
        if mode not in ("accept", "dismiss", "reject", "cancel"):
            raise TaskError("set_dialog.mode 只能是 accept 或 dismiss")
        e.settings["dialog"] = "dismiss" if mode in ("dismiss", "reject", "cancel") else "accept"
    if "text" in p:
        e.settings["dialog_text"] = p.get("text")
    e.logf("弹窗策略：%s%s" % (e.settings.get("dialog", "accept"),
                              ("，输入文本 %r" % e.settings["dialog_text"])
                              if e.settings.get("dialog_text") else ""))
    return e.settings.get("dialog", "accept")


@action("reload")
def _a_reload(e, p, s):
    e.current.reload(wait_until=p.get("wait_until", "domcontentloaded"), timeout=25000)
    e.snap("reload")
    return None


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
    state = p.get("state", "visible")
    timeout = int(p.get("timeout", 20000))
    if not sel:
        raise TaskError("wait_for 需要 selector")
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
    raise TaskError("等待元素超时：%s（%s）" % (sel, str(last).split("\n")[0]))


@action("click")
def _a_click(e, p, s):
    sel = p.get("selector")
    if not sel:
        raise TaskError("click 需要 selector")
    fr, loc = e.locator(sel, p.get("frame"))
    idx = int(p.get("nth", 0) or 0)
    if loc.count() <= idx:
        raise TaskError("click 找不到元素（第 %d 个）：%s" % (idx, sel))
    target = loc.nth(idx)
    e.logf("点击元素：%s" % sel, echo=True)
    if p.get("capture_new_page"):
        return e.click_capture(target, p)
    target.click(timeout=int(p.get("timeout", 20000)))
    e.wait_after_nav(p)
    e.snap("click")
    return None


@action("click_text")
def _a_click_text(e, p, s):
    """按中文文字匹配点击（自动容错空格/换行、遍历 iframe、穿透 Shadow DOM）"""
    kws = p.get("text") or p.get("keywords")
    if kws is None:
        raise TaskError("click_text 需要 text（关键词或关键词列表）")
    if isinstance(kws, str):
        kws = [kws]
    e.logf("文字匹配点击：%s" % kws)
    hit = find_button(e.current, [str(k) for k in kws])
    if not hit:
        if p.get("optional"):
            e.logf("未找到按钮（optional，跳过）：%s" % kws)
            return False
        raise TaskError("找不到按钮：关键词 %s（当前页 %s）" % (kws, e.current.url))
    kw, fr, loc, brief = hit
    e.logf("命中关键词“%s”，按钮文字“%s”" % (kw, brief.get("text", "")), echo=True)
    if p.get("capture_new_page"):
        return e.click_capture(loc, p)
    loc.click(timeout=int(p.get("timeout", 20000)))
    e.wait_after_nav(p)
    e.snap("click_text")
    return brief.get("text", "")


@action("click_row_link")
def _a_click_row_link(e, p, s):
    """点击当前列表行内的任务链接；自动兼容新标签页/当前页跳转"""
    row = e.row
    if not row:
        raise TaskError("click_row_link 只能在任务列表循环中使用")
    e.logf("点击任务链接（行文本：%s）" % (row["text"][:40].replace("\n", " ")), echo=True)
    np, is_new = open_after_click(e.ctx, e.list_page, row["link"].click, 5000)
    if np:
        e.current = np
        e.opened_pages.append(np)
        e.logf("任务页在新标签页打开：%s" % np.url)
    else:
        e.logf("任务页在当前页跳转：%s" % e.current.url)
    e.snap("任务页打开")
    return e.current.url


# ---------------- 表单类 ----------------
@action("fill")
def _a_fill(e, p, s):
    sel = p.get("selector")
    text = p.get("text", p.get("value", ""))
    if not sel:
        # 按 label/placeholder/name 智能找输入框
        for key in ("placeholder", "label", "name"):
            if p.get(key):
                sel = _input_selector_for(p[key], key)
                break
    if not sel:
        raise TaskError("fill 需要 selector，或 placeholder/label/name 之一")
    fr, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise TaskError("fill 找不到输入框：%s" % sel)
    target = loc.nth(int(p.get("nth", 0) or 0))
    if p.get("clear", True):
        target.fill("")
    target.fill(str(text))
    e.logf("填写 %s ← %s" % (sel, "***" if p.get("secret") else text))
    return text


def _input_selector_for(val, kind):
    v = str(val).replace('"', '\\"')
    if kind == "placeholder":
        return 'input[placeholder*="%s"], textarea[placeholder*="%s"]' % (v, v)
    if kind == "name":
        return 'input[name="%s"], textarea[name="%s"]' % (v, v)
    return 'input[id="%s"], textarea[id="%s"]' % (v, v)


@action("check")
def _a_check(e, p, s):
    sel = p.get("selector")
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise TaskError("check 找不到元素：%s" % sel)
    item = loc.nth(int(p.get("nth", 0) or 0))
    try:
        item.check(timeout=int(p.get("timeout", 5000)))
    except Exception:
        item.evaluate("el => el.click()")
    return True


@action("pick_radio")
def _a_pick_radio(e, p, s):
    """按 value/关联label/父级文本选择单选框"""
    text = p.get("text", p.get("value", ""))
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise TaskError("pick_radio 找不到元素：%s" % p["selector"])
        item = loc.nth(int(p.get("nth", 0) or 0))
        try:
            item.check(timeout=5000)
        except Exception:
            item.evaluate("el => el.click()")
        e.logf("已勾选单选框（选择器）：%s" % p["selector"])
        return True
    if not text:
        raise TaskError("pick_radio 需要 text 或 selector")
    if not pick_radio(e.current, str(text)):
        raise TaskError("找不到匹配的单选框：%s" % text)
    e.logf("已勾选单选框：%s" % text, echo=True)
    return True


@action("pick_radio_by_rule")
def _a_pick_radio_by_rule(e, p, s):
    """按 settings.radio_rules 的任务类型→单选框规则自动选择"""
    type_text = p.get("type_text")
    if not type_text:
        type_text = collect_type_text(e.current, e.vars.get("row_text", ""))
    pick = decide_radio_keyword({"radio_rules": e.settings["radio_rules"],
                                 "default_radio": e.settings["default_radio"]}, type_text)
    e.vars["task_type_text"] = type_text
    if not pick:
        e.logf("无 radio 规则命中，跳过选择")
        return None
    if not pick_radio(e.current, pick):
        if not pick_radio(e.current, e.settings["default_radio"]):
            raise TaskError("规则命中“%s”但未找到对应单选框（默认：%s）"
                            % (pick, e.settings["default_radio"]))
    e.logf("按规则勾选单选框：%s" % pick, echo=True)
    return pick


@action("select_option")
def _a_select_option(e, p, s):
    sel = p.get("selector")
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise TaskError("select_option 找不到下拉框：%s" % sel)
    target = loc.first
    if p.get("label") is not None:
        target.select_option(label=str(p["label"]))
    elif p.get("value") is not None:
        target.select_option(value=str(p["value"]))
    else:
        target.select_option(str(p.get("option", "")))
    e.logf("下拉选择：%s" % (p.get("label") or p.get("value") or p.get("option")))
    return True


@action("press")
def _a_press(e, p, s):
    key = p.get("key")
    if not key:
        raise TaskError("press 需要 key，如 Enter/Accept")
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        loc.first.press(str(key))
    else:
        e.current.keyboard.press(str(key))
    return None


@action("hover")
def _a_hover(e, p, s):
    """悬停：selector 精确命中，或用 text 文字匹配（同 click_text 的容错）"""
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise TaskError("hover 找不到元素：%s" % p["selector"])
        loc.nth(int(p.get("nth", 0) or 0)).hover()
    elif p.get("text") is not None:
        kws = p["text"] if isinstance(p["text"], list) else [p["text"]]
        hit = find_button(e.current, [str(k) for k in kws])
        if not hit:
            if p.get("optional"):
                return False
            raise TaskError("hover 找不到文字：%s" % kws)
        hit[2].hover()
    else:
        raise TaskError("hover 需要 selector 或 text")
    return True


@action("scroll")
def _a_scroll(e, p, s):
    """滚动：selector（滚到元素）/ by（像素，如 [0, 800]）/ bottom: true"""
    if p.get("selector"):
        _, loc = e.locator(p["selector"], p.get("frame"))
        if loc.count() == 0:
            raise TaskError("scroll 找不到元素：%s" % p["selector"])
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
    """上传文件：file/files 为路径（相对路径锚定到工具目录）"""
    sel = p.get("selector")
    if not sel:
        raise TaskError("upload 需要 selector（input[type=file]）")
    files = p.get("file", p.get("files"))
    if files is None:
        raise TaskError("upload 需要 file（文件路径，可为数组）")
    if isinstance(files, str):
        files = [files]
    resolved = []
    for f in files:
        fp = Path(str(f))
        resolved.append(str(fp if fp.is_absolute() else (BASE_DIR / fp)))
    _, loc = e.locator(sel, p.get("frame"))
    if loc.count() == 0:
        raise TaskError("upload 找不到文件输入框：%s" % sel)
    loc.first.set_input_files(resolved)
    e.logf("已上传 %d 个文件：%s" % (len(resolved), "、".join(resolved)), echo=True)
    return resolved


@action("extract")
def _a_extract(e, p, s):
    """从页面取值存变量：
    what: text(默认)|html|value|attr|count|href；attr 时用 attr: 属性名；
    all: true 取列表（可用 join 拼接）"""
    sel = p.get("selector")
    name = str(p.get("name") or p.get("var") or "extracted")
    what = str(p.get("what") or ("attr" if p.get("attr") else "text")).lower()
    if not sel and what != "count":
        raise TaskError("extract 需要 selector")

    def read(el):
        if what == "html":
            return el.inner_html()
        if what == "value":
            return el.input_value()
        if what == "attr":
            return el.get_attribute(str(p.get("attr") or p.get("attribute") or ""))
        if what == "href":
            return el.get_attribute("href")
        return el.inner_text()

    try:
        _, loc = e.locator(sel, p.get("frame"))
        n = loc.count()
    except Exception:
        n = 0
    if what == "count":
        val = n
    elif p.get("all"):
        vals = [read(loc.nth(i)) for i in range(n)]
        val = p.get("join", "").join(str(v) for v in vals) if p.get("join") else vals
    elif n == 0:
        val = p.get("default", "")
    else:
        val = read(loc.first)
    e.vars[name] = val
    e.logf("extract %s（%s）= %s" % (name, what, _fmt_text(str(val), 60)))
    return {name: val}


@action("evaluate")
def _a_evaluate(e, p, s):
    """执行页面 JS 并把返回值存变量（js/code/script 三选一）"""
    js = p.get("js") or p.get("code") or p.get("script")
    if js is None:
        raise TaskError("evaluate 需要 js（例如 () => document.title）")
    fr = e.resolve_frame(p.get("frame"))
    target = fr if fr is not None else e.current.main_frame
    val = target.evaluate(str(js))
    name = p.get("name")
    if name:
        e.vars[str(name)] = val
    e.logf("evaluate → %s" % _fmt_text(json.dumps(val, ensure_ascii=False)
                                       if isinstance(val, (dict, list)) else str(val), 80))
    return val


@action("request")
def _a_request(e, p, s):
    """发 HTTP 请求（requests）并把结果存变量：status/text/json/headers。
    需要下载文件时配 output: 路径。"""
    try:
        import requests
    except ImportError:
        raise TaskError("未安装 requests，无法使用 request 动作")
    url = p.get("url")
    if not url:
        raise TaskError("request 需要 url")
    method = str(p.get("method", "GET")).upper()
    try:
        resp = requests.request(
            method, str(url),
            params=p.get("params"), headers=p.get("headers"),
            json=p.get("json"), data=p.get("data"),
            timeout=float(p.get("timeout", 15)),
            verify=bool(p.get("verify", True)))
    except Exception as ex:
        raise TaskError("请求失败 %s %s：%s" % (method, url, str(ex).split("\n")[0]))
    result = {"status": resp.status_code, "ok": bool(resp.ok),
              "text": resp.text, "headers": dict(resp.headers)}
    try:
        result["json"] = resp.json()
    except Exception:
        pass
    if p.get("output"):
        op = Path(str(p["output"]))
        op = op if op.is_absolute() else BASE_DIR / op
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_bytes(resp.content)
        result["file"] = str(op)
    name = str(p.get("name") or "http")
    e.vars[name] = result
    e.logf("%s %s → HTTP %d" % (method, url, resp.status_code), echo=True)
    return result


@action("http")
def _a_http(e, p, s):
    return _a_request(e, p, s)


# ---------------- 下载 / 校验 ----------------
@action("download")
def _a_download(e, p, s):
    """下载附件。text/selector 二选一，缺省自动寻找“附件/下载”链接。"""
    base = Path(p["dir"]) if p.get("dir") else _cfg_path(e.cfg, "attachment_dir")
    if p.get("dir") is None and e.settings.get("attachment_dir"):
        base = Path(e.settings["attachment_dir"])
        if not base.is_absolute():
            base = BASE_DIR / base
    prefix = p.get("filename_prefix", "") or str(e.vars.get("task_no", ""))
    cat = p.get("subdir", "") or str(e.vars.get("category", ""))
    dest_dir = base / (sanitize_filename(cat) if cat else "")
    got = generalized_download(e, dest_dir, prefix,
                               selector=p.get("selector"), text=p.get("text"),
                               frame=p.get("frame"))
    if not got and not p.get("optional"):
        e.logf("未找到可下载的附件（按配置跳过）")
    return got


def generalized_download(e, dest_dir, prefix, selector=None, text=None, frame=None):
    page = e.current
    keywords = ("附件", "下载", "download")
    one = e.resolve_frame(frame)
    frames = [one] if one is not None else e.frames_of(page)
    for fr in frames:
        if selector:
            cands = [(fr.locator(selector), None)]
        else:
            cands = []
            try:
                anchors = fr.locator("a[href]")
                cands = [(anchors, None)]
            except Exception:
                continue
        for loc, _ in cands:
            try:
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                a = loc.nth(i)
                try:
                    brief = _el_brief(a)
                    txt = _norm_text(brief.get("text", ""))
                    href = a.get_attribute("href") or ""
                    has_dl = a.get_attribute("download") is not None
                except Exception:
                    continue
                if href.startswith(("javascript", "mailto")) or href in ("", "#"):
                    continue
                if selector is None and not (has_dl or any(k in txt for k in keywords)):
                    continue
                before = page.url
                try:
                    with page.expect_download(timeout=20000) as dl_info:
                        a.click()
                    dl = dl_info.value
                except Exception as ex:
                    e.logf("附件下载触发失败：%s" % ex)
                    try:
                        if page.url != before:
                            page.go_back(wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    continue
                fname = sanitize_filename(dl.suggested_filename or "file.bin")
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / ("%s_%s" % (sanitize_filename(prefix), fname) if prefix
                                   else fname)
                dl.save_as(str(dest))
                e.logf("附件已保存：%s" % dest, echo=True)
                return str(dest)
    return None


@action("expect_text")
def _a_expect_text(e, p, s):
    text = str(p.get("text", ""))
    if not e.has_text(text):
        raise TaskError("断言失败：页面上未出现文字“%s”" % text)
    e.logf("断言通过：页面包含“%s”" % text)
    return True


@action("expect_visible")
def _a_expect_visible(e, p, s):
    sel = p.get("selector")
    if not e.selector_visible(sel, p.get("frame")):
        raise TaskError("断言失败：元素不可见 %s" % sel)
    e.logf("断言通过：元素可见 %s" % sel)
    return True


@action("assert")
def _a_assert(e, p, s):
    if not eval_condition(p.get("condition", p.get("if")), e):
        raise TaskError("断言失败：%s" % p.get("message", p.get("condition")))
    return True


@action("screenshot")
def _a_screenshot(e, p, s):
    shot(e.current, str(p.get("name", "手动截图")))
    return None


@action("fail")
def _a_fail(e, p, s):
    raise TaskError(str(p.get("message", "流程主动失败")))


@action("pause")
def _a_pause(e, p, s):
    pause_for_ukey(str(p.get("message", "")))
    return None


@action("probe")
def _a_probe(e, p, s):
    """页面探测：扫描按钮/输入框/单选框/下拉框/列表行，打印选择器建议并生成
    可直接粘贴的 YAML 草稿。file 省略=自动写 shots/probe_*.yaml；file: ""=只看控制台。"""
    scope_all = _as_bool(p.get("all_pages", p.get("all", False)))
    data = collect_probe(e.ctx, max_each=int(p.get("max", 60) or 60), all_pages=scope_all)
    probe_report_text(data)
    summary = {"pages": len(data),
               "elements": sum(len(fr["buttons"]) + len(fr["inputs"]) + len(fr["choices"])
                               + len(fr["selects"]) for pg in data for fr in pg["frames"])}
    file = p.get("file")
    with_steps = bool(p.get("steps", True))
    if file is None:
        path = write_probe_file(data, None, with_steps=with_steps)
        summary["file"] = str(path)
        e.logf("探测结果已写入：%s" % path, echo=True)
    elif str(file).strip():
        path = write_probe_file(data, str(file), with_steps=with_steps)
        summary["file"] = str(path)
        e.logf("探测结果已写入：%s" % path, echo=True)
    return summary


@action("record")
def _a_record(e, p, s):
    """录制人工操作并生成 YAML 步骤（产物默认写 shots/record_时间.yaml）"""
    recorder_install(e.ctx)
    e.logf("操作录制已开始，请在浏览器中操作……", echo=True)
    print()
    print("=" * 62)
    print(">>> 操作录制中：请在浏览器里正常操作（点击 / 填写 / 下拉 / 上传 / 回车）")
    print(">>> 完成后回到本窗口按 回车 结束录制")
    print("=" * 62)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    events = recorder_stop(e.ctx)
    steps, need_password = events_to_steps(events)
    e.logf("录制结束：%d 个事件 → %d 个步骤" % (len(events), len(steps)), echo=True)
    path = write_record_file(steps, getattr(e, "wf", None),
                             (p.get("file") or None), need_password=need_password)
    print()
    print("—— 录制生成的步骤（可直接粘贴到 workflow.yaml 的 steps 下）——")
    print(dump_yaml({"steps": steps}).strip())
    e.logf("录制结果已写入：%s" % path, echo=True)
    return {"file": str(path), "steps": steps}


@action("expect_url")
def _a_expect_url(e, p, s):
    pattern = p.get("pattern") or p.get("url")
    if not pattern:
        raise TaskError("expect_url 需要 pattern")
    url = e.current.url or ""
    ok = (re.search(str(pattern), url) is not None if p.get("regex")
          else _glob_match(str(pattern), url))
    if not ok:
        raise TaskError("断言失败：当前 URL %s 不匹配 %s" % (url, pattern))
    e.logf("断言通过：URL 匹配 %s" % pattern)
    return True


def _glob_match(pattern, text):
    """极简 glob（支持 * 与 **）→ 正则，用于 URL/文字匹配"""
    import fnmatch
    return fnmatch.fnmatch(text, pattern)


# ---------------- 页面管理 ----------------
@action("switch_page")
def _a_switch_page(e, p, s):
    pages = e.ctx.pages
    idx = p.get("index")
    if idx is not None:
        e.current = pages[int(idx)]
    elif p.get("url_contains"):
        for pg in pages:
            if str(p["url_contains"]) in (pg.url or ""):
                e.current = pg
                break
        else:
            raise TaskError("找不到 URL 包含 %r 的页面" % p["url_contains"])
    elif p.get("title_contains"):
        for pg in pages:
            if str(p["title_contains"]) in (pg.title() or ""):
                e.current = pg
                break
        else:
            raise TaskError("找不到标题包含 %r 的页面" % p["title_contains"])
    else:
        e.current = pages[-1]
    e.logf("切换到页面：%s" % e.current.url)
    return e.current.url


@action("close_page")
def _a_close_page(e, p, s):
    which = p.get("which", "current")
    if which == "current":
        targets = [e.current] if e.current is not e.list_page else []
    elif which == "others":
        targets = [pg for pg in e.ctx.pages if pg is not e.list_page and pg is not e.current]
    elif which == "task":
        targets = [e.current] if e.current is not e.list_page else []
    else:
        targets = []
    for pg in targets:
        try:
            pg.close()
            e.logf("已关闭页面：%s" % (pg.url or ""))
        except Exception:
            pass
    if e.current is not e.list_page:
        e.current = e.list_page
    return len(targets)


@action("close_task_page")
def _a_close_task_page(e, p, s):
    for pg in list(e.opened_pages):
        try:
            pg.close()
        except Exception:
            pass
    e.opened_pages = []
    e.current = e.list_page
    e.logf("已关闭任务标签页，回到列表")
    return True


# ---------------- 控制流 ----------------
@action("if")
def _a_if(e, p, s):
    if eval_condition(p.get("condition", p.get("if")), e):
        e.run_steps(s.get("then") or [])
        return True
    e.run_steps(s.get("else") or [])
    return False


def _iter_over(e, p):
    over = p.get("over")
    if over is None:
        over = p.get("list")
    if over is None:
        # rows: 选择器 → 按元素文本迭代
        sel = p.get("rows")
        if sel:
            fr, loc = e.locator(sel, p.get("frame"))
            return [loc.nth(i).inner_text() for i in range(min(loc.count(), 200))]
    if isinstance(over, list):
        return over
    if p.get("range"):
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
        except _LoopBreak:
            e.logf("for_each 在第 %d 项 break" % i)
            break
        except _LoopContinue:
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
        except _LoopBreak:
            break
        except _LoopContinue:
            continue
    return times


@action("while")
def _a_while(e, p, s):
    cond = p.get("condition", p.get("while"))
    body = s.get("do") or s.get("steps") or []
    limit = int(p.get("max", 100))
    var = str(p.get("as") or "index")
    # 条件里用到的自定义循环变量先归零，避免首次求值因变量未定义而误判
    if var not in e.vars:
        e.vars[var] = 0
    for ident in re.findall(r"\b([A-Za-z_]\w*)\b", str(cond)):
        if ident.lower() not in ("true", "false", "null", "none", "and", "or", "not",
                                 "exists", "visible", "has_text", "absent",
                                 "contains", "equals", "matches", "startswith", "endswith",
                                 "in", "page_count", "url", "title", "max", "min"):
            e.vars.setdefault(ident, 0)
    i = 0
    while i < limit:
        e.vars[var] = i          # 先更新计数变量，条件求值才能看到当前轮次
        if not eval_condition(cond, e):
            break
        try:
            e.run_steps(body)
        except _LoopBreak:
            i += 1
            break
        except _LoopContinue:
            pass
        i += 1
    if i >= limit:
        e.logf("while 达到上限 %d 次，已停止（可能是死循环）" % limit)
    return i


@action("break")
def _a_break(e, p, s):
    raise _LoopBreak()


@action("continue")
def _a_continue(e, p, s):
    raise _LoopContinue()


@action("run_steps")
def _a_run_steps(e, p, s):
    """内联执行子步骤（也可用 uses: run_steps 引用片段）"""
    e.run_steps(s.get("do") or s.get("steps") or [])
    return None


# ---------------------------------------------------------------- 引擎装配与运行


def _engine_click_capture(self, target, p):
    """点击并捕获可能的新标签页；无新页则视为当前页跳转"""
    np, is_new = open_after_click(self.ctx, self.current, target.click, int(p.get("capture_timeout", 5000)))
    if np:
        self.opened_pages.append(np)
        self.current = np
        self.logf("捕获到新标签页：%s" % np.url)
    else:
        self.logf("无新标签页，按当前页跳转处理：%s" % self.current.url)
    self.wait_after_nav(p)
    self.snap("click_capture")
    return self.current.url


def _engine_wait_after_nav(self, p):
    if p.get("no_wait"):
        return
    try:
        self.current.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass


WorkflowEngine.click_capture = _engine_click_capture
WorkflowEngine.wait_after_nav = _engine_wait_after_nav


def _handle_dialog(d, engine):
    """按 settings.dialog 处理原生弹窗（默认 accept；可 dismiss；prompt 可配文本）"""
    mode = str((engine.settings or {}).get("dialog", "accept")).lower()
    text = (engine.settings or {}).get("dialog_text")
    try:
        if mode in ("dismiss", "reject", "cancel"):
            log("捕获浏览器原生弹窗[%s]：%s → 按配置【取消】" % (d.type, d.message), echo=True)
            d.dismiss()
        else:
            log("捕获浏览器原生弹窗[%s]：%s → 自动【接受】" % (d.type, d.message), echo=True)
            d.accept(text if (d.type == "prompt" and text) else None)
    except Exception:
        try:
            d.accept()
        except Exception:
            pass


def workflow_login(cfg, wf, engine, browser):
    """按 workflow.login 配置登录（未配置则回落到 config.json 的内置登录）。
    state 文件默认 state.json，可用 login.state_file 指定（不同流程互不干扰）；
    复用前会校验该 state 是否属于当前站点，避免拿别的流程的登录态硬套。"""
    global _ACTIVE_CONTEXT
    login = wf.get("login")
    handler = lambda d: _handle_dialog(d, engine)
    if not login:
        ctx, page = ensure_login(browser, cfg, engine.logf, dialog_handler=handler)
        return ctx, page
    state_path = resolve_state_path(login.get("state_file"))
    engine.state_path = state_path
    success_url = login.get("success_url") or cfg.get("base_url")
    verify_target = success_url or login.get("url") or cfg.get("sso_login_url") or ""
    reusable, why = state_reusable(state_path, verify_target)
    if state_path.exists() and not reusable:
        engine.logf("忽略现有登录态：%s。将重新登录。" % why, echo=True)
    ctx = browser.new_context(
        storage_state=str(state_path) if reusable else None)
    setup_context(ctx, handler)
    page = ctx.new_page()
    tune_page(page, handler)
    engine.ctx, engine.current = ctx, page
    _ACTIVE_CONTEXT = ctx          # UKey 暂停点等外部集成需要访问当前上下文

    if reusable and success_url:
        try:
            page.goto(success_url, wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("load", timeout=8000)
            except Exception:
                pass
        except Exception as ex:
            engine.logf("复用登录态失败：%s" % ex)
        else:
            if not is_login_page(page) and not _on_verify_page(page, login):
                engine.logf("检测到有效登录态（复用 %s），跳过登录与 UKey。"
                            % state_path.name, echo=True)
                return ctx, page
            engine.logf("%s 已过期，需要重新登录。" % state_path.name, echo=True)

    if login.get("steps"):
        engine.run_steps(login["steps"])
    else:
        url = login.get("url") or cfg.get("sso_login_url")
        if not url:
            raise ConfigError(
                "workflow.yaml 的 login 未配置 url，且 config.json 也没有 sso_login_url。\n"
                "请在 login: 下补充登录页地址，例如 url: http://sso.example.com/login")
        safe_goto(page, url, "SSO登录页")
        if login.get("username") is not None and login.get("password") is not None:
            u_sel = login.get("username_selector") or "input[type='text']"
            p_sel = login.get("password_selector") or "input[type='password']"
            uf, ulo = _find_first_visible(page, [u_sel, "input[name*='user' i]"])
            pf, plo = _find_first_visible(page, [p_sel])
            if not ulo or not plo:
                raise FatalError("登录页找不到账号/密码输入框，请用 inspect 查看后配置选择器")
            ulo.fill(str(login["username"]))
            plo.fill(str(login["password"]))
            kws = login.get("button_text") or cfg.get("login_button_keywords") or ["登录"]
            hit = find_button(page, [str(k) for k in (kws if isinstance(kws, list) else [kws])])
            if not hit:
                raise FatalError("登录页找不到登录按钮（关键词 %s）" % kws)
            engine.logf("点击登录按钮（命中：%s）" % hit[0], echo=True)
            hit[2].click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            if login.get("wait_after_login_ms"):
                page.wait_for_timeout(int(login["wait_after_login_ms"]))
        engine.snap("UKey前")
        if login.get("ukey_wait", True):
            pause_for_ukey()      # ← 人工完成 UKey 弹窗后回车；真实环境必须走这里
        engine.snap("UKey后")
        if login.get("verify_url") or success_url:
            verify = login.get("verify_url") or success_url
            safe_goto(page, verify, "平台首页")
            if is_login_page(page) or _on_verify_page(page, login):
                dump_scene(page, "登录未成功")
                raise FatalError(
                    "登录未成功：访问 %s 后仍未进入平台（可能 UKey 未完成或账密错误）。"
                    "现场已保存到 shots/ 目录。" % verify)

    try:
        _save_state(ctx, state_path, {
            "origin": _host_of(verify_target),
            "workflow": wf.get("name") or "",
            "success_url": success_url or "",
        })
        engine.logf("登录态已保存到 %s" % state_path.name, echo=True)
    except Exception as ex:
        engine.logf("保存 %s 失败：%s" % (state_path.name, ex))
    return ctx, page


def _on_verify_page(page, login):
    """是否仍停留在 UKey/二次验证页：URL 命中 ukey/verify/otp 等关键字，
    或页面上出现配置的 verification_text"""
    u = (page.url or "").lower()
    if any(k in u for k in ("ukey", "verify", "otp", "mfa", "2fa")):
        return True
    txt = login.get("verification_text")
    if txt and not is_login_page(page):
        try:
            for fr in page.frames:
                if _norm_text(str(txt)) in _norm_text(
                        fr.evaluate("() => document.body ? document.body.innerText : ''")):
                    return True
        except Exception:
            pass
    return False


def workflow_mode(path=None, wf=None, cfg=None):
    """workflow 模式入口：读 workflow.yaml → 登录 → 逐类别跑任务 → 汇总。
    config.json 是可选的：完全没有也能运行，只在 workflow.yaml 缺省时兜底。
    返回 summary，便于自动测试断言。"""
    cfg = load_config_optional() if cfg is None else dict(cfg)
    wf = wf if wf is not None else load_yaml_file(path or (BASE_DIR / "workflow.yaml"))
    if not isinstance(wf, dict):
        raise ConfigError("workflow.yaml 顶层应是一个映射（含 name/tasks 等字段）")

    login = wf.get("login")
    if login:
        if not isinstance(login, dict):
            raise ConfigError("workflow.yaml 的 login 应为映射（含 url/username 等字段）")
        _merge_login_cfg(cfg, login)
    elif not (str(cfg.get("base_url") or "").strip()
              and str(cfg.get("sso_login_url") or "").strip()):
        raise ConfigError(
            "workflow.yaml 未配置 login，且 config.json 缺少 base_url/sso_login_url。\n"
            "请任选其一：\n"
            "  ① 在 workflow.yaml 中补上 login:（url、success_url、username 等）；\n"
            "  ② 在同目录放置填写完整的 config.json。")

    engine = WorkflowEngine(wf, cfg)
    engine.vars.setdefault("base_url", cfg.get("base_url", ""))
    engine.vars.setdefault("storage_state", str(STATE_PATH))

    br = wf.get("browser") or {}
    cfg = dict(cfg)
    if br.get("channel"):
        cfg["channel"] = br["channel"]
    if "headless" in br:
        cfg["headless"] = bool(br["headless"])

    print("工作流：%s" % wf.get("name", "(未命名)"))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch_browser(p, cfg, force_headed=bool(br.get("headed_force")))
        try:
            ctx, page = workflow_login(cfg, wf, engine, browser)
            engine.ctx, engine.current = ctx, page
            engine.vars["storage_state"] = str(getattr(engine, "state_path", STATE_PATH))
            engine.opened_pages = []
            if wf.get("steps"):
                engine.logf("执行主流程 steps（%d 步）" % len(wf["steps"]), echo=True)
                engine.run_steps(wf["steps"])
            for task in (wf.get("tasks") or []):
                run_workflow_task(engine, cfg, task)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    print_summary(engine.summary)
    return engine.summary


def _run_one_task(engine, steps, label, stat, snap_stage=None):
    """执行一个任务单元的 steps：失败记入 stat，不让整类崩溃。返回是否成功。"""
    engine.opened_pages = []
    engine.current = engine.list_page
    ok = False
    try:
        if snap_stage:
            engine.snap(snap_stage)
        engine.run_steps(steps)
        ok = True
        engine.logf("任务 %s 处理成功" % label, echo=True)
    except ConfigError:
        raise                     # 配置错误立即中止，不当作单任务失败吞掉
    except TaskError as ex:
        engine.logf("任务 %s 失败：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    except FatalError:
        raise
    except Exception as ex:
        engine.logf("任务 %s 异常：%s" % (label, ex), echo=True)
        engine.logf(traceback.format_exc())
        engine.snap("%s_异常" % label)
    finally:
        for pg in list(engine.opened_pages):
            try:
                pg.close()
            except Exception:
                pass
        engine.opened_pages = []
        engine.current = engine.list_page
    (stat["成功"] if ok else stat["失败"]).append(label)
    return ok


def run_workflow_task(engine, cfg, task):
    """按 workflow 的 tasks 定义跑任务。mode 三种：
      once       只执行一次 steps（不找列表行；适合纯导航/接口/单页流程）
      first_row  队列循环：取列表第一行 → steps → 回列表，直到列表空（默认）
      each_row   表格循环：remove_after: true（默认）时始终取第一行（行会消失的列表）；
                 false 时按行号依次取第 0、1、2… 行（行不消失的表格）。"""
    name = str(task.get("name") or "未命名类别")
    stat = engine.summary.setdefault(name, {"成功": [], "失败": [], "跳过": [], "错误": ""})
    steps = task.get("steps") or []
    mode = str(task.get("mode") or "first_row").lower()
    max_tasks = int(task.get("max_tasks", engine.settings["max_tasks"]))
    remove_after = _as_bool(task.get("remove_after", True))
    engine.vars["category"] = name
    engine.vars["category_url"] = task.get("url", "")
    engine.logf("========== 工作流类别：%s（%s，mode=%s） =========="
                % (name, task.get("url", ""), mode), echo=True)

    list_page = engine.current
    engine.list_page = list_page
    engine.vars["list_url"] = task.get("url", "")
    if task.get("url"):
        resp = safe_goto(list_page, task["url"], "任务列表页")
        if resp is not None and getattr(resp, "status", 0) >= 400:
            stat["错误"] = "列表页返回 HTTP %d" % resp.status
            engine.logf("%s：%s" % (name, stat["错误"]), echo=True)
            return

    if mode == "once":
        engine.row = None
        engine.vars["task_no"] = ""
        engine.vars["row_text"] = ""
        engine.vars["row_index"] = 0
        _run_one_task(engine, steps, name, stat)
        engine.logf("========== 类别 %s 结束：成功 %d，失败 %d =========="
                    % (name, len(stat["成功"]), len(stat["失败"])), echo=True)
        return

    seen = set()
    done = 0
    while done < max_tasks:
        if is_login_page(list_page):
            engine.logf("登录态失效，重新登录（需人工完成 UKey）…", echo=True)
            do_login(engine.ctx, list_page, cfg, engine.logf,
                     state_path=getattr(engine, "state_path", STATE_PATH))
            if task.get("url"):
                safe_goto(list_page, task["url"], "任务列表页")
        row_index = 0 if remove_after else done
        row_info = find_first_task_row(list_page, {
            "row_selector": task.get("row_selector", ""),
            "link_selector": task.get("link_selector", "")}, index=row_index)
        if not row_info:
            engine.logf("类别 %s：列表已空，处理结束。" % name, echo=True)
            break
        task_no = _task_no_from_row(row_info["text"]) or "row%d" % (done + 1)
        if remove_after and task_no in seen:
            stat["跳过"].append(task_no)
            engine.logf("任务 %s 反复无法完成，终止该类别以免死循环。" % task_no, echo=True)
            break
        seen.add(task_no)

        engine.vars.update({
            "task_no": task_no, "row_text": row_info["text"],
            "index": done, "row_index": row_index,
            "category": name, "list_url": task.get("url", ""),
        })
        engine.row = dict(row_info)
        engine.row["task_no"] = task_no
        _run_one_task(engine, steps, task_no, stat, snap_stage="%s_列表行" % task_no)
        done += 1

        if task.get("url"):
            try:
                safe_goto(list_page, task["url"], "任务列表页")
            except FatalError as ex:
                stat["错误"] = str(ex).split("\n")[0]
                engine.logf("返回列表页失败：%s" % stat["错误"], echo=True)
                break
        lo, hi = engine.settings["task_delay_seconds"]
        delay = random.uniform(float(lo), float(hi))
        engine.logf("随机等待 %.1f 秒（模拟人工节奏）…" % delay)
        try:
            list_page.wait_for_timeout(int(delay * 1000))
        except Exception:
            time.sleep(delay)
    engine.logf("========== 类别 %s 结束：成功 %d，失败 %d，跳过 %d =========="
                % (name, len(stat["成功"]), len(stat["失败"]), len(stat["跳过"])), echo=True)

# ================================================================ 菜单

MENU = """
==============================================
   内网平台任务自动化工具  tool_kit
==============================================
  1. probe     页面探测 / 选择器调试（输出 YAML 草稿，原 inspect+debug）
  2. record    操作录制（在浏览器里操作，自动生成 YAML 步骤）
  3. workflow  执行 YAML 工作流（读 workflow.yaml，推荐；config.json 可省略）
  4. validate  校验 YAML（语法 / 动作名 / 常见参数）
  5. run       固定流程（兼容旧 config.json）
  6. demo      用本地 mock 平台彩排（需先运行 mock_platform.py）
  0. 退出
----------------------------------------------
命令行等价用法：
  tool_kit --workflow 流程.yaml            执行
  tool_kit --workflow 流程.yaml --validate 只校验
  tool_kit --probe [--workflow 流程.yaml]  交互探测
  tool_kit --record out.yaml [--url 网址]  录制生成 YAML
"""


def _arg_value(*names):
    """取 --xxx 后面跟的值（也支持 --xxx=value 形式）；未出现返回 None"""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                return argv[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def _arg_present(*names):
    return any(a in names or any(a.startswith(n + "=") for n in names for a in sys.argv[1:])
               for a in sys.argv[1:])


# 常见动作必填参数（with 里至少给一个），校验时给出提醒但不阻断
_ACTION_PARAM_HINTS = {
    "goto": ["url"],
    "wait_for_url": ["pattern", "url"],
    "click": ["selector"],
    "fill": ["selector", "placeholder", "label", "name"],
    "check": ["selector"],
    "pick_radio": ["text", "selector", "value"],
    "select_option": ["selector"],
    "press": ["key"],
    "wait_for": ["selector"],
    "upload": ["selector", "file"],
    "extract": ["selector"],
    "evaluate": ["js", "code", "script"],
    "request": ["url"],
    "http": ["url"],
    "expect_text": ["text"],
    "expect_visible": ["selector"],
    "expect_url": ["pattern", "url"],
    "assert": ["condition", "if"],
    "set_var": ["name", "var"],
    "for_each": ["over", "list", "rows", "range"],
    "while": ["condition", "while"],
    "repeat": ["times"],
}


def main():
    _init_stdio()
    log("tool_kit 启动（模式菜单）", echo=False)
    wf_path = _workflow_arg()
    if _has_flag("-h", "--help", "/?"):
        print(MENU)
        return 0
    if _has_flag("--validate", "--check"):
        try:
            ok = validate_workflow_cli(wf_path)
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
            return 2
        return 0 if ok else 2
    if _arg_present("--record"):
        # 命令行录制：tool_kit.exe --record out.yaml [--url 网址] [--workflow 参考.yaml]
        try:
            record_mode(wf_path, _arg_value("--record"), _arg_value("--url", "-u"))
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
            return 2
        except FatalError as e:
            print("\n【致命错误】%s\n" % e)
            return 1
        return 0
    if _has_flag("--probe"):
        try:
            probe_mode(wf_path)
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
            return 2
        except FatalError as e:
            print("\n【致命错误】%s\n" % e)
            return 1
        return 0
    if wf_path:
        # 命令行直接跑工作流：tool_kit.exe --workflow my.yaml
        try:
            workflow_mode(wf_path)
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
            return 2
        except FatalError as e:
            print("\n【致命错误】%s\n" % e)
            return 1
        return 0
    print(MENU)
    while True:
        try:
            choice = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        try:
            if choice == "1":
                probe_mode(wf_path)
            elif choice == "2":
                record_mode(wf_path)
            elif choice == "3":
                wf = BASE_DIR / "workflow.yaml"
                if not wf.exists():
                    print("\n未找到 %s。请先用 tool_kit --validate 检查路径，或把工作流文件命名为 workflow.yaml 放在 exe 同目录。\n" % wf)
                else:
                    workflow_mode(wf)
            elif choice == "4":
                validate_workflow_cli()
            elif choice == "5":
                run_mode()
            elif choice == "6":
                demo_mode()
            elif choice == "0":
                break
            elif choice == "":
                continue
            else:
                print("无效选择：%r，请输入 0-6。" % choice)
                continue
        except ConfigError as e:
            print("\n【配置错误】%s\n" % e)
        except FatalError as e:
            print("\n【致命错误】%s\n" % e)
        except KeyboardInterrupt:
            print("\n已取消当前操作。")
        except Exception as e:
            log("未预期异常：%s\n%s" % (e, traceback.format_exc()))
            print("\n【未预期错误】%s" % str(e).split("\n")[0])
            print("详细堆栈已写入日志：%s" % _get_log_file())
        print(MENU)
    print("再见。")


def _workflow_arg():
    """解析命令行 --workflow/-w 参数"""
    for i, a in enumerate(sys.argv[1:], 1):
        if a in ("--workflow", "-w", "--file", "-f") and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--workflow="):
            return a.split("=", 1)[1]
    return None


def _has_flag(*names):
    return any(a in names for a in sys.argv[1:])


def validate_workflow_cli(path=None):
    """校验 workflow.yaml：语法 + 动作名 + 结构，给中文提示"""
    path = path or _workflow_arg() or (BASE_DIR / "workflow.yaml")
    p = Path(path)
    print("校验工作流文件：%s" % p)
    wf = load_yaml_file(p)
    if not isinstance(wf, dict):
        raise ConfigError("顶层应为映射（含 name / login / tasks / steps）")

    errs, warns = [], []
    if not wf.get("login"):
        warns.append("未配置 login，将回落到 config.json 的内置登录流程（需要 sso_login_url）")
    if not wf.get("steps") and not wf.get("tasks"):
        errs.append("既没有 steps 也没有 tasks，工作流不会执行任何东西")

    def walk(steps, where):
        if steps is None:
            return
        if not isinstance(steps, list):
            errs.append("%s 应为步骤列表" % where)
            return
        for i, st in enumerate(steps, 1):
            tag = "%s 第 %d 步" % (where, i)
            if not isinstance(st, dict):
                errs.append("%s 不是映射" % tag)
                continue
            uses = st.get("uses")
            if not uses:
                errs.append("%s 缺少 uses" % tag)
                continue
            if uses not in ACTIONS:
                errs.append("%s 未知动作 uses=%r（可用：%s）"
                            % (tag, uses, "、".join(sorted(ACTIONS))))
            else:
                hints = _ACTION_PARAM_HINTS.get(uses)
                w = st.get("with") or {}
                if hints and isinstance(w, dict) \
                        and not any(k in w for k in hints):
                    warns.append("%s（%s）缺少参数：需要 %s 之一"
                                 % (tag, uses, " / ".join(hints)))
            for key in ("then", "else", "do", "steps"):
                if key in st:
                    if isinstance(st[key], list):
                        walk(st[key], "%s 的 %s" % (tag, key))
                    elif st[key] is None:
                        pass
                    else:
                        errs.append("%s 的 %s 应为步骤列表" % (tag, key))
    walk(wf.get("steps"), "steps")
    for i, t in enumerate(wf.get("tasks") or [], 1):
        if not isinstance(t, dict) or not t.get("name"):
            errs.append("tasks 第 %d 项需要 name" % i)
            continue
        mode = str(t.get("mode") or "first_row").lower()
        if mode not in ("once", "first_row", "each_row"):
            errs.append("tasks[%s].mode 只能是 once / first_row / each_row（当前 %r）"
                        % (t.get("name"), mode))
        elif mode != "once" and not t.get("url"):
            warns.append("tasks[%s]（%s）未配置 url，将在当前页面查找列表行"
                         % (t.get("name"), mode))
        walk(t.get("steps"), "tasks[%s].steps" % t.get("name"))
    if wf.get("login", {}).get("steps"):
        walk(wf["login"]["steps"], "login.steps")

    if errs:
        print("\n【发现 %d 个错误】" % len(errs))
        for e in errs:
            print("  [X] %s" % e)
    for w in warns:
        print("  ! 提示：%s" % w)
    if not errs:
        acts = sorted({st.get("uses") for st in _collect_actions(wf) if st.get("uses")})
        print("\n[OK] 语法与动作名校验通过。")
        print("  动作：%s" % "、".join(acts))
        print("  任务类别：%s" % "、".join(str(t.get("name")) for t in (wf.get("tasks") or [])))
    return not errs


def _collect_actions(wf):
    out = []

    def walk(steps):
        for st in (steps or []):
            if isinstance(st, dict):
                out.append(st)
                for k in ("then", "else", "do", "steps"):
                    walk(st.get(k))
    walk(wf.get("steps"))
    walk((wf.get("login") or {}).get("steps"))
    for t in (wf.get("tasks") or []):
        walk((t or {}).get("steps"))
    return out


if __name__ == "__main__":
    sys.exit(main() or 0)
