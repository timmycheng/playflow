# -*- coding: utf-8 -*-
"""heal：检查工作流步骤在页面上的可达性，平台改版后给出修复建议。

用法：playflow heal 流程.yaml [--url 网址] [--fix]
检查在“登录后 + --url 页面”这一个页面状态下进行；流程中途跳转后才出现的元素
需要用 --url 指到对应页面再检查一次。--fix 不改原文件，写出 <名字>.healed.yaml。
"""
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from .dom import find_button, norm_text, radio_label_text
from .probe import collect_probe
from .utils import base_dir, format_text

_MIN_FIX_SCORE = 0.55


def _ratio(a, b) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def collect_candidates(engine) -> list:
    """收集当前页面的全部可交互元素，作为修复候选。"""
    out = []
    for pg in collect_probe(engine.ctx, all_pages=False):
        for fr in pg["frames"]:
            for b in fr["buttons"]:
                out.append({"kind": "button", "text": b["text"], "selector": b["selector"]})
            for i in fr["inputs"]:
                out.append({"kind": "input", "selector": i["selector"], "name": i["name"],
                            "placeholder": i["placeholder"]})
            for c in fr["choices"]:
                out.append({"kind": "choice", "selector": c["selector"], "label": c["label"],
                            "value": c["value"]})
            for s in fr["selects"]:
                out.append({"kind": "select", "selector": s["selector"], "name": s["name"]})
    return out


def _iter_steps(wf):
    """产出 (位置描述, 步骤)，覆盖 steps / login.steps / tasks[].steps 及嵌套。"""
    def walk(steps, where):
        for i, st in enumerate(steps or [], 1):
            if not isinstance(st, dict):
                continue
            tag = "%s 第 %d 步" % (where, i)
            yield tag, st
            for key in ("then", "else", "do", "steps"):
                yield from walk(st.get(key), "%s 的 %s" % (tag, key))

    yield from walk((wf or {}).get("steps"), "steps")
    yield from walk(((wf or {}).get("login") or {}).get("steps"), "login.steps")
    for task in ((wf or {}).get("tasks") or []):
        if isinstance(task, dict):
            yield from walk(task.get("steps"), "tasks[%s].steps" % task.get("name"))


def _radio_matches(engine, text) -> bool:
    """只读检查：页面上是否有归一化文字匹配的单选框（不点击）。"""
    nk = norm_text(text)
    if not nk:
        return True
    for fr in engine.frames_of():
        try:
            radios = fr.locator('input[type="radio"]').all()
        except Exception:
            continue
        for lo in radios:
            if nk in norm_text(radio_label_text(fr, lo)):
                return True
    return False


def _suggest_for_text(keywords, candidates) -> list:
    """按归一化文字相似度给出文字参数候选（按钮文字）。"""
    scored = []
    for c in candidates:
        if c["kind"] != "button" or not c.get("text"):
            continue
        score = max(_ratio(k, c["text"]) for k in keywords)
        scored.append((score, c["text"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"score": round(s, 2), "value": t} for s, t in scored[:3]]


def _suggest_for_selector(selector, candidates) -> list:
    """选择器失效时，按与 id/name/placeholder/文字 的相似度给出替代选择器。"""
    sel = str(selector or "")
    scored = []
    for c in candidates:
        fields = [c.get("selector") or "", c.get("name") or "", c.get("placeholder") or "",
                  c.get("label") or "", c.get("text") or ""]
        score = max((_ratio(sel, f) for f in fields if f), default=0.0)
        for f in fields:
            f_norm = str(f).strip()
            if f_norm and len(f_norm) >= 3 and f_norm.lower() in sel.lower():
                score = min(1.0, score + 0.2)
        if c.get("selector"):
            scored.append((score, c["selector"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    seen, out = set(), []
    for score, sel2 in scored:
        if sel2 not in seen:
            seen.add(sel2)
            out.append({"score": round(score, 2), "value": sel2})
        if len(out) >= 3:
            break
    return out


def check_step(engine, step, candidates) -> dict:
    """检查单个步骤的定位参数；返回 finding：ok / message / suggestions / param。"""
    uses = step.get("uses")
    w = step.get("with") or {}
    finding = {"uses": uses, "ok": True, "message": "", "suggestions": [], "param": None}

    def fail(msg, param, suggestions):
        finding["ok"] = False
        finding["message"] = msg
        finding["param"] = param
        finding["suggestions"] = suggestions

    if uses == "click_text" and w.get("text"):
        kws = [str(k) for k in (w["text"] if isinstance(w["text"], list) else [w["text"]])]
        if find_button(engine.current, kws):
            return finding
        if engine.has_text(" ".join(kws)):
            fail("关键词 %s 在页面上存在但按钮不可见" % kws, "text",
                 _suggest_for_text(kws, candidates))
        else:
            fail("找不到按钮：关键词 %s" % kws, "text", _suggest_for_text(kws, candidates))
        return finding

    if uses == "pick_radio" and not w.get("selector") and w.get("text"):
        if _radio_matches(engine, str(w["text"])):
            return finding
        fail("找不到匹配的单选框：%s" % w["text"], "text",
             _suggest_for_text([str(w["text"])],
                               [c for c in candidates if c["kind"] == "choice"]))
        return finding

    if uses in ("click", "fill", "check", "select_option", "extract", "wait_for",
                "expect_visible", "upload", "hover") and w.get("selector"):
        sel = str(w["selector"])
        if engine.count_elements(sel, w.get("frame")) > 0:
            return finding
        fail("选择器未命中任何元素：%s" % sel, "selector",
             _suggest_for_selector(sel, candidates))
        return finding

    return finding   # 该动作没有可静态检查的定位参数，视为通过


def _apply_finding(step, finding, suggestion, min_score):
    param = finding.get("param")
    if not param or suggestion["score"] < min_score:
        return False
    w = step.setdefault("with", {})
    if param == "text":
        w["text"] = [suggestion["value"]]
    else:
        w[param] = suggestion["value"]
    return True


def run_heal(engine, wf, url=None, fix=False, min_score=_MIN_FIX_SCORE,
             out_path=None) -> list:
    """执行检查并打印报告；fix=True 时把高于阈值的建议写入修复版 YAML。

    返回 findings 列表（每项含 where/uses/ok/message/suggestions）。
    """
    if url:
        from .browser import safe_goto
        safe_goto(engine.current, str(url), "检查页面")
    candidates = collect_candidates(engine)
    engine.logf("heal：当前页面 %s（候选元素 %d 个）" % (engine.current.url, len(candidates)),
                echo=True)
    findings = []
    n_bad = 0
    n_fixed = 0
    for where, step in _iter_steps(wf):
        if not step.get("uses") or step.get("include"):
            continue
        f = check_step(engine, step, candidates)
        f["where"] = where
        findings.append(f)
        if f["ok"]:
            continue
        n_bad += 1
        engine.logf("[X] %s（%s）%s" % (where, f["uses"], f["message"]), echo=True)
        best = f["suggestions"][0] if f["suggestions"] else None
        if not best:
            engine.logf("    页面上没有相近的候选元素。", echo=True)
        elif best["score"] >= min_score:
            engine.logf("    建议 → with.%s = %r（相似度 %.2f）"
                        % (f["param"], format_text(best["value"], 50), best["score"]),
                        echo=True)
            if fix and _apply_finding(step, f, best, min_score):
                n_fixed += 1
        else:
            engine.logf("    最接近的候选 %r 相似度 %.2f（低于阈值 %.2f，不建议自动修复）"
                        % (format_text(best["value"], 50), best["score"], min_score),
                        echo=True)
    engine.logf("heal 完成：%d 个步骤，%d 个未能定位，%d 个已生成修复建议。"
                % (len(findings), n_bad, n_fixed), echo=True)
    if fix and n_fixed:
        target = Path(out_path) if out_path else Path(
            "healed_%s.yaml" % datetime.now().strftime("%H%M%S"))
        if not target.is_absolute():
            target = base_dir() / target
        _write_healed(wf, target)
        engine.logf("修复版已写入：%s（原文件未改动）" % target, echo=True)
    return findings


def _write_healed(wf, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# playflow heal 生成的修复版（%s）\n"
        "# 相似度建议已替换，请人工核对后再使用。\n"
        % datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        + yaml.safe_dump(wf, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")
