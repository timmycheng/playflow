# -*- coding: utf-8 -*-
"""浏览器启动、登录、登录态持久化与人工暂停。"""
import datetime
import json
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from .errors import AbortError, ConfigError
from .template import render
from .utils import anchor, dump_scene, log, pause_for_manual, shot
from .dom import find_button, norm_text

DEFAULT_TIMEOUT = 20000
_ACTIVE_CONTEXT = None   # 最近一次登录建立的上下文，供测试/外部集成观察


def launch(p, channel="chrome", headless=False):
    """启动系统 Chrome（channel="chrome"），不使用 Playwright 自带 Chromium。"""
    try:
        return p.chromium.launch(channel=channel or "chrome", headless=bool(headless))
    except Exception as e:
        raise AbortError(
            "浏览器启动失败（channel=%s）：%s\n"
            "请确认本机已安装 Google Chrome；若仍失败请查看日志 %s。"
            % (channel, str(e).split("\n")[0], "logs/"))


def make_dialog_handler(settings):
    """按 settings.dialog（accept/dismiss，prompt 可配 dialog_text）处理原生弹窗。"""
    def handler(dialog):
        mode = str((settings or {}).get("dialog", "accept")).lower()
        text = (settings or {}).get("dialog_text")
        try:
            if mode in ("dismiss", "reject", "cancel"):
                log("原生弹窗[%s]：%s → 取消" % (dialog.type, dialog.message), echo=True)
                dialog.dismiss()
            else:
                log("原生弹窗[%s]：%s → 接受" % (dialog.type, dialog.message), echo=True)
                dialog.accept(text if (dialog.type == "prompt" and text) else None)
        except Exception:
            try:
                dialog.accept()
            except Exception:
                pass
    return handler


def setup_context(ctx, handler=None, timeout=DEFAULT_TIMEOUT):
    """对上下文中已有及未来的所有页面统一挂默认超时与弹窗处理。"""
    def tune(page):
        try:
            page.set_default_timeout(int(timeout))
        except Exception:
            pass
        try:
            page.on("dialog", handler or (lambda d: d.accept()))
        except Exception:
            pass

    for pg in ctx.pages:
        tune(pg)
    ctx.on("page", tune)


def is_login_page(page) -> bool:
    """是否被重定向到登录页：优先看有无可见密码框，其次看 URL 关键字。"""
    try:
        for fr in page.frames:
            try:
                if fr.locator('input[type="password"]').count() > 0:
                    return True
            except Exception:
                continue
        url = (page.url or "").lower()
        return any(k in url for k in ("login", "signin", "sign_in", "sign-in",
                                      "logon", "passport"))
    except Exception:
        return False


def find_first_visible(page, selectors, timeout=5000, interval=300):
    """跨 frame 找第一个可见且可编辑的输入框；返回 (frame, locator) 或 (None, None)。

    SPA 登录页的表单可能在 load 之后才渲染，因此在内部分轮询等待。
    """
    deadline = time.time() + max(0, timeout) / 1000.0
    while True:
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
        if time.time() >= deadline:
            return None, None
        try:
            page.wait_for_timeout(interval)
        except Exception:
            return None, None


def safe_goto(page, url, what="页面", wait_until="domcontentloaded",
              timeout=25000, settle=True):
    """带友好报错的跳转；返回 Response（可能为 None）。

    goto 失败后 Chrome 会停留在 chrome-error 页面并继续一次内部导航，
    若不重置，紧接着的下一次 goto 会被它打断（interrupted by another navigation）。
    因此失败后先重试一次“被内部导航打断”的情况，再把页面重置到 about:blank。
    """
    last = None
    for attempt in (0, 1):
        try:
            resp = page.goto(str(url), wait_until=wait_until, timeout=int(timeout))
            if settle:
                try:
                    page.wait_for_load_state("load", timeout=10000)
                except Exception:
                    pass
            return resp
        except Exception as e:
            last = e
            if "interrupted by another navigation" in str(e) and attempt == 0:
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                continue
            break
    log(traceback.format_exc())
    for _ in range(3):
        try:
            page.goto("about:blank", wait_until="commit", timeout=3000)
            break
        except Exception:
            try:
                page.wait_for_timeout(200)
            except Exception:
                break
    raise AbortError(
        "无法访问%s：%s\n"
        "可能原因：① 地址写错；② 目标服务未启动；③ 网络不通。原始错误：%s"
        % (what, url, str(last).split("\n")[0]))


# ---------------------------------------------------------------- 登录态文件

def host_of(url) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def state_meta_path(state_path) -> Path:
    p = Path(state_path)
    return p.with_suffix(".meta.json") if p.name.endswith(".json") \
        else Path(str(p) + ".meta.json")


def save_state(ctx, state_path, meta=None):
    """保存登录态；同时写 <state>.meta.json 记录所属站点，防止跨流程误用。"""
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(state_path))
    if not meta:
        return
    try:
        data = dict(meta)
        data.setdefault("saved_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        state_meta_path(state_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log("保存登录态元数据失败：%s" % e)


def state_reusable(state_path, target_url) -> tuple:
    """判断 state 文件能否用于 target_url，返回 (可用?, 原因)。

    ① 元数据站点不一致 → 不可用；② 文件里没有目标站点 cookie/localStorage → 不可用；
    读不出文件内容时退回“可用”，交给后续页面级校验。
    """
    p = Path(state_path)
    if not p.exists():
        return False, "没有 state 文件"
    host = host_of(target_url)
    if not host:
        return True, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return True, ""
    try:
        mpath = state_meta_path(p)
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
        oh = host_of(o.get("origin") or "")
        if oh and (host == oh or host.endswith("." + oh) or oh.endswith("." + host)):
            return True, ""
    return False, "state 文件里没有 %s 的登录态（可能来自其它流程或站点）" % host


def resolve_state_path(spec) -> Path:
    """login.state_file 相对路径锚定到 base_dir，默认 state.json。"""
    return anchor(spec if spec else "state.json")


def _needs_verification(page, login_cfg) -> bool:
    """是否仍停留在 UKey/二次验证页：URL 关键字或 verification_text 命中。"""
    url = (page.url or "").lower()
    if any(k in url for k in ("ukey", "verify", "otp", "mfa", "2fa")):
        return True
    txt = login_cfg.get("verification_text")
    if txt and not is_login_page(page):
        try:
            for fr in page.frames:
                body = fr.evaluate("() => document.body ? document.body.innerText : ''")
                if norm_text(str(txt)) in norm_text(body):
                    return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------- 登录

def login(engine, browser):
    """按 workflow.login 建立上下文并完成登录；未配置 login 时新建干净上下文。

    登录态可复用：state 文件有效则跳过登录；失效或站点不符则重新登录。
    """
    global _ACTIVE_CONTEXT
    login_cfg = render(engine.wf.get("login") or {}, engine.vars)
    handler = make_dialog_handler(engine.settings)
    state_path = resolve_state_path(login_cfg.get("state_file"))
    engine.state_path = state_path

    success_url = login_cfg.get("success_url") or ""
    verify_url = login_cfg.get("verify_url") or success_url
    target = verify_url or login_cfg.get("url") or ""
    reusable, why = (state_reusable(state_path, target) if login_cfg else (False, ""))
    if login_cfg and state_path.exists() and not reusable:
        engine.logf("忽略现有登录态：%s，将重新登录。" % why, echo=True)

    ctx = browser.new_context(storage_state=str(state_path) if reusable else None)
    setup_context(ctx, handler,
                  timeout=int(engine.settings.get("timeout", DEFAULT_TIMEOUT)))
    page = ctx.new_page()
    engine.ctx, engine.current = ctx, page
    _ACTIVE_CONTEXT = ctx

    if not login_cfg:
        return ctx, page

    if reusable and verify_url:
        try:
            page.goto(verify_url, wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("load", timeout=8000)
            except Exception:
                pass
        except Exception as ex:
            engine.logf("复用登录态失败：%s" % ex)
        else:
            if not is_login_page(page) and not _needs_verification(page, login_cfg):
                engine.logf("检测到有效登录态（复用 %s），跳过登录。" % state_path.name, echo=True)
                return ctx, page
            engine.logf("%s 已失效，需要重新登录。" % state_path.name, echo=True)

    perform_login(engine, page, login_cfg)
    try:
        save_state(ctx, state_path, {
            "origin": host_of(verify_url or login_cfg.get("url")),
            "workflow": engine.wf.get("name") or "",
            "success_url": success_url,
        })
        engine.logf("登录态已保存到 %s" % state_path.name, echo=True)
    except Exception as ex:
        engine.logf("保存登录态失败：%s" % ex)
    return ctx, page


def perform_login(engine, page, login_cfg=None):
    """执行登录流程；运行中途被踢回登录页时可再次调用。"""
    login_cfg = login_cfg or render(engine.wf.get("login") or {}, engine.vars)
    if not login_cfg:
        raise ConfigError("登录态失效，但工作流未配置 login，无法自动重新登录")
    if login_cfg.get("steps"):
        engine.run_steps(login_cfg["steps"])
        return

    url = login_cfg.get("url")
    if not url:
        raise ConfigError("login 缺少 url（或改用 login.steps 自定义登录）")
    engine.logf("打开登录页：%s" % url, echo=True)
    safe_goto(page, url, "登录页")

    username, password = login_cfg.get("username"), login_cfg.get("password")
    if username is not None and password is not None:
        u_sel = login_cfg.get("username_selector") or "input[type='text']"
        p_sel = login_cfg.get("password_selector") or "input[type='password']"
        _, ulo = find_first_visible(
            page, [u_sel, "input[name*='user' i]", "input[id*='user' i]"])
        _, plo = find_first_visible(page, [p_sel])
        if not ulo or not plo:
            shot(page, "登录页找不到输入框")
            raise AbortError("登录页找不到账号/密码输入框，请用 probe 查看页面后配置 "
                             "login.username_selector / login.password_selector")
        ulo.fill(str(username))
        plo.fill(str(password))
        kws = login_cfg.get("button_text") or ["登录", "Login"]
        if isinstance(kws, str):
            kws = [kws]
        kws = [str(k) for k in kws]
        hit, deadline = None, time.time() + 10
        while not hit and time.time() < deadline:
            hit = find_button(page, kws)
            if not hit:
                page.wait_for_timeout(300)
        if not hit:
            shot(page, "登录页找不到按钮")
            raise AbortError("登录页找不到登录按钮（关键词 %s），请用 probe 确认按钮文字。"
                             % kws)
        engine.logf("点击登录按钮（命中：%s）" % hit[0], echo=True)
        hit[2].click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        if login_cfg.get("wait_after_login_ms"):
            page.wait_for_timeout(int(login_cfg["wait_after_login_ms"]))
    else:
        print("login 未配置 username/password，请在浏览器中手动输入账密并登录。")

    engine.snap("登录后待验证")
    if login_cfg.get("manual_pause", True):
        pause_for_manual(login_cfg.get("pause_hint")
                         or "请在浏览器外完成验证（如 UKey / 短信验证码）")
    engine.snap("验证后")

    verify_url = login_cfg.get("verify_url") or login_cfg.get("success_url")
    if verify_url:
        engine.logf("访问 %s 校验登录态…" % verify_url, echo=True)
        safe_goto(page, verify_url, "平台首页")
        if is_login_page(page) or _needs_verification(page, login_cfg):
            dump_scene(page, "登录未成功")
            raise AbortError(
                "登录未成功：访问 %s 后仍未进入平台（账密错误或人工验证未完成）。"
                "现场已保存到 shots/。" % verify_url)
