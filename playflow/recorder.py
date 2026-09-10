# -*- coding: utf-8 -*-
"""操作录制：向页面注入事件监听，把人工操作转换成可直接运行的 YAML 步骤。"""
import datetime
from pathlib import Path

import yaml

from .utils import anchor, base_dir

_RECORDER_JS = r"""
(() => {
  if (window.__pfRecorder) return;
  const R = { events: [], on: true };
  window.__pfRecorder = R;
  const MAX = 800;
  const KEY = '__pfRecorderEvents';
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

_CLEAR_JS = "() => { try { sessionStorage.removeItem('__pfRecorderEvents'); } catch (e) {} }"
_STOP_JS = ("() => { if (window.__pfRecorder) { window.__pfRecorder.on = false; "
            "try { sessionStorage.removeItem('__pfRecorderEvents'); } catch (e) {} } }")


def install(ctx):
    """注入录制器：已有 frame 立即注入，之后的新页面由 init script 覆盖。"""
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                fr.evaluate(_CLEAR_JS)
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


def drain(ctx) -> list:
    """收取所有页面所有 frame 的录制事件（按时间排序）。"""
    events = []
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                evs = fr.evaluate(
                    "() => (window.__pfRecorder ? window.__pfRecorder.drain() : [])")
            except Exception:
                continue
            if evs:
                events.extend(evs)
    events.sort(key=lambda e: e.get("ts") or 0)
    return events


def stop(ctx) -> list:
    """停止录制并取回事件。"""
    events = drain(ctx)
    for pg in ctx.pages:
        for fr in pg.frames:
            try:
                fr.evaluate(_STOP_JS)
            except Exception:
                continue
    return events


def _auto_step_name(step) -> str:
    uses, w = step.get("uses"), (step.get("with") or {})
    if uses == "click_text":
        t = w.get("text")
        return "点击「%s」" % ((t[0] if isinstance(t, list) and t else t) or "")
    if uses == "click":
        return "点击 %s" % w.get("selector", "")
    if uses == "fill":
        return "填写 %s" % w.get("selector", "")
    if uses == "pick_radio":
        return "选择「%s」" % w.get("text", "")
    if uses == "check":
        return "勾选 %s" % w.get("selector", "")
    if uses == "select_option":
        return "下拉选择「%s」" % w.get("label", "")
    if uses == "upload":
        return "上传文件"
    if uses == "press":
        return "按键 %s" % w.get("key", "")
    return uses or "步骤"


def events_to_steps(events):
    """录制事件 → 步骤列表，返回 (steps, need_password)。

    密码框统一替换成 {{ env.password }}，不落明文。
    """
    steps, need_password = [], False
    for ev in events:
        kind = ev.get("kind")
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
                step = {"uses": "fill", "with": {"selector": sel, "text": ev.get("value") or ""}}
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
                continue
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
            steps.append({"uses": "press", "with": {"key": ev.get("key") or "Enter",
                                                    "selector": sel}})

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


def write_record_file(steps, workflow=None, path=None, need_password=False,
                      verify_results=None) -> Path:
    """把录制出的步骤写成可直接运行的工作流 YAML。

    verify_results 与 steps 等长（或 None）：每项 (True, "") / (False, 失败原因)，
    失败步骤上方会生成 “# ⚠ 回放验证失败” 注释。
    """
    if path is None:
        d = base_dir() / "shots"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("record_%s.yaml" % datetime.datetime.now().strftime("%H%M%S"))
    path = anchor(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = {"name": "录制流程"}
    login = (workflow or {}).get("login")
    if isinstance(login, dict) and login:
        safe_login = dict(login)
        if "password" in safe_login:
            safe_login["password"] = "{{ env.password }}"
        doc["login"] = safe_login
    env = dict((workflow or {}).get("env") or {})
    if need_password and not env.get("password"):
        env["password"] = ""
    if env:
        doc["env"] = env
    header = ("# playflow 操作录制生成（%s）\n"
              "# 请核对 URL、账号与选择器后使用。\n"
              % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if not verify_results:
        doc["steps"] = steps
        path.write_text(header + yaml.safe_dump(
            doc, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8")
        return path

    # 带验证结果：逐个步骤手写缩进，以便在失败步骤上方插注释
    doc.pop("steps", None)
    chunks = [header + yaml.safe_dump(
        doc, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n"),
        "steps:" if steps else "steps: []"]
    for i, st in enumerate(steps):
        if i < len(verify_results) and verify_results[i] and not verify_results[i][0]:
            chunks.append("  # ⚠ 回放验证失败：%s" % (verify_results[i][1] or "未知原因"))
        step_yaml = yaml.safe_dump([st], allow_unicode=True, sort_keys=False,
                                   default_flow_style=False).rstrip("\n")
        chunks.append("\n".join("  " + ln if ln.strip() else ln
                                for ln in step_yaml.split("\n")))
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return path
