# -*- coding: utf-8 -*-
"""页面元素辅助：文字容错匹配、列表行扫描、单选框、附件下载。"""
import re
from pathlib import Path

from .utils import log, sanitize_filename

BUTTON_SELECTORS = ('button', 'a', '[role="button"]',
                    'input[type="submit"]', 'input[type="button"]')
DEFAULT_ROW_SELECTORS = ['tr:has(a[href])', 'li:has(a[href])', 'div:has(> a[href])']
DEFAULT_DOWNLOAD_KEYWORDS = ("附件", "下载", "download")
INPUT_TYPES = ("", "text", "password", "email", "number", "tel", "search", "url",
               "date", "datetime-local", "month", "week", "time")


def norm_text(text) -> str:
    """去掉所有空白，兼容“签 收”“提 交”这类带空格的按钮文字。"""
    return re.sub(r"\s+", "", str(text or ""))


def el_brief(loc) -> dict:
    """一次性取元素的 tag/文字/可见性/常用属性，减少逐个属性的往返。"""
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


def _frame_clickables(fr):
    """收集 frame 内所有可点击元素（按钮/链接/提交框）。"""
    out = []
    for sel in BUTTON_SELECTORS:
        try:
            locs = fr.locator(sel).all()
        except Exception:
            continue
        for lo in locs:
            b = el_brief(lo)
            if b and norm_text(b.get("text", "")):
                out.append((lo, b))
    return out


def find_button(page, keywords):
    """按关键词顺序、frame 顺序找第一个可见按钮，返回 (关键词, frame, locator, brief)。

    匹配前对按钮文字与关键词都做空白归一化；Playwright 选择器默认穿透开放 Shadow DOM。
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    kws = [k for k in (keywords or []) if norm_text(k)]
    if not kws:
        return None
    cache = {}
    for kw in kws:
        nk = norm_text(kw)
        for fr in page.frames:
            if fr not in cache:
                cache[fr] = _frame_clickables(fr)
            for lo, b in cache[fr]:
                if b.get("visible") and nk in norm_text(b.get("text", "")):
                    return kw, fr, lo, b
    return None


def find_button_all(page, keywords):
    """find_button 的复数版：返回全部命中。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    kws = [k for k in (keywords or []) if norm_text(k)]
    hits, cache = [], {}
    for kw in kws:
        nk = norm_text(kw)
        for fr in page.frames:
            if fr not in cache:
                cache[fr] = _frame_clickables(fr)
            for lo, b in cache[fr]:
                if nk in norm_text(b.get("text", "")):
                    hits.append((kw, fr, lo, b))
    return hits


def radio_label_text(fr, radio_loc) -> str:
    """综合 radio 的 value 属性、label[for=id] 文字、父级 label 文字。"""
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
    """按关键词勾选单选框：遍历所有 frame 的 input[type=radio]，命中即 check。"""
    nk = norm_text(keyword)
    if not nk:
        return False
    for fr in page.frames:
        try:
            radios = fr.locator('input[type="radio"]').all()
        except Exception:
            continue
        for lo in radios:
            if nk not in norm_text(radio_label_text(fr, lo)):
                continue
            try:
                lo.check(timeout=5000)
            except Exception:
                try:
                    lo.evaluate("el => el.click()")
                except Exception:
                    continue
            return True
    return False


def _safe_css_str(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def suggest_selector(b) -> str:
    """按 id > name > placeholder > 文字 的优先级给出选择器建议。"""
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


def input_selector(value, kind="placeholder") -> str:
    """按 placeholder/name/id 生成输入框选择器。"""
    v = _safe_css_str(value)
    if kind == "name":
        return 'input[name="%s"], textarea[name="%s"]' % (v, v)
    if kind == "id":
        return '[id="%s"]' % v
    return ('input[placeholder*="%s"], textarea[placeholder*="%s"]' % (v, v))


def _scan_row(page, row_selector, link_selector, index):
    """在所有 frame 中找第 index 行可点击的任务行（行内第一个 <a>）。"""
    row_sels = ([row_selector] if row_selector else []) + DEFAULT_ROW_SELECTORS
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
                    link = row.locator(link_selector) if link_selector \
                        else row.locator("a[href]").first
                    if link.count() == 0:
                        continue
                    text = row.inner_text()
                except Exception:
                    continue
                if not text.strip():
                    continue
                hits.append({"frame": fr, "row": row, "link": link, "text": text})
                if len(hits) > index:
                    return hits[index]
            if len(hits) > index:
                return hits[index]
    return None


def find_row(page, row_selector="", link_selector="", index=0, retries=5):
    """带重试的取行：列表在 iframe 内或由 JS 异步渲染时，内容可能还没就绪。"""
    for _ in range(max(1, int(retries))):
        info = _scan_row(page, row_selector, link_selector, index)
        if info:
            return info
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return None


def download(page, frames, dest_dir, prefix="", selector=None, text=None,
             timeout=20000, logf=log):
    """下载附件：优先 selector，其次自动寻找“附件/下载”链接或带 download 属性的链接。

    返回保存路径；没有可下载项时返回 None。
    """
    keywords = text if text else DEFAULT_DOWNLOAD_KEYWORDS
    if isinstance(keywords, str):
        keywords = [keywords]
    dest_dir = Path(dest_dir)
    for fr in frames:
        try:
            loc = fr.locator(selector if selector else "a[href]")
        except Exception:
            continue
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(count):
            a = loc.nth(i)
            try:
                brief = el_brief(a)
                txt = norm_text(brief.get("text", ""))
                href = a.get_attribute("href") or ""
                has_dl = a.get_attribute("download") is not None
            except Exception:
                continue
            if href.startswith(("javascript", "mailto")) or href in ("", "#"):
                continue
            if selector is None and not (has_dl or any(k in txt for k in keywords)):
                continue
            before_url = page.url
            try:
                with page.expect_download(timeout=timeout) as dl_info:
                    a.click()
                dl = dl_info.value
            except Exception as ex:
                logf("附件下载触发失败（%s）：%s" % (href or selector, str(ex).split("\n")[0]))
                try:
                    if page.url != before_url:
                        page.go_back(wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
                continue
            fname = sanitize_filename(dl.suggested_filename or "file.bin")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / ("%s_%s" % (sanitize_filename(prefix), fname)
                               if prefix else fname)
            dl.save_as(str(dest))
            logf("附件已保存：%s" % dest, echo=True)
            return str(dest)
    return None
