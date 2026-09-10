# -*- coding: utf-8 -*-
"""页面探测：扫描元素、给出选择器建议、生成可直接粘贴的 YAML 步骤草稿。"""
import datetime
from pathlib import Path

import yaml

from .dom import (
    BUTTON_SELECTORS,
    DEFAULT_ROW_SELECTORS,
    INPUT_TYPES,
    el_brief,
    find_button_all,
    norm_text,
    radio_label_text,
    suggest_selector,
)
from .utils import anchor, base_dir, format_text


def collect_probe(target, max_each=60, all_pages=True) -> list:
    """结构化扫描每个页面/每个 frame 的按钮/输入框/单选框/下拉框/列表行候选。"""
    pages = list(target.pages) if hasattr(target, "pages") else [target]
    if not all_pages and pages:
        pages = [pages[-1]]
    result = []
    for pi, page in enumerate(pages):
        try:
            title = page.title()
        except Exception:
            title = ""
        frames = []
        for fi, fr in enumerate(page.frames):
            data = {"index": fi, "iframe": fr != page.main_frame, "url": fr.url or "",
                    "buttons": [], "inputs": [], "choices": [], "selects": [], "rows": []}
            seen = 0
            for sel in BUTTON_SELECTORS:
                if seen >= max_each:
                    break
                try:
                    locs = fr.locator(sel).all()
                except Exception:
                    continue
                for lo in locs:
                    b = el_brief(lo)
                    if not b or not b.get("visible") or not norm_text(b.get("text", "")):
                        continue
                    data["buttons"].append({
                        "tag": b["tag"], "text": " ".join(b["text"].split())[:60],
                        "selector": suggest_selector(b)})
                    seen += 1
                    if seen >= max_each:
                        break
            try:
                locs = fr.locator("input, textarea").all()
            except Exception:
                locs = []
            for lo in locs[:max_each * 2]:
                b = el_brief(lo)
                if not b or not b.get("visible"):
                    continue
                if b["tag"] == "input" and b.get("type") not in INPUT_TYPES:
                    continue
                data["inputs"].append({
                    "tag": b["tag"], "type": b.get("type") or "text",
                    "name": b.get("name", ""), "placeholder": b.get("placeholder", ""),
                    "selector": suggest_selector(b)})
            try:
                locs = fr.locator('input[type="radio"], input[type="checkbox"]').all()
            except Exception:
                locs = []
            for lo in locs[:max_each]:
                b = el_brief(lo)
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
            try:
                locs = fr.locator("select").all()
            except Exception:
                locs = []
            for lo in locs[:max_each]:
                b = el_brief(lo)
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
            for sel in DEFAULT_ROW_SELECTORS:
                try:
                    n = fr.locator(sel).count()
                except Exception:
                    n = 0
                if n:
                    data["rows"].append({"selector": sel, "count": n})
            frames.append(data)
        result.append({"page_index": pi, "url": page.url or "", "title": title,
                       "frames": frames})
    return result


def report_probe(probe_data) -> str:
    """把探测结果打印成人类可读清单（选择器建议直接跟在元素后面）。"""
    lines = []
    for pg in probe_data:
        lines.append("=" * 66)
        lines.append("[页面%d] %s   标题：%s"
                     % (pg["page_index"], pg["url"], format_text(pg["title"], 40)))
        for fr in pg["frames"]:
            lines.append("-" * 66)
            lines.append("  %sframe#%d  %s"
                         % ("[iframe] " if fr["iframe"] else "", fr["index"], fr["url"]))
            for b in fr["buttons"]:
                lines.append("    [%s] %s    → %s"
                             % (b["tag"], format_text(b["text"]), b["selector"]))
            for i in fr["inputs"]:
                lines.append("    [input:%s] name=%s placeholder=%s    → %s"
                             % (i["type"], i["name"] or "-",
                                format_text(i["placeholder"], 20) or "-", i["selector"]))
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


def suggest_steps(probe_data, max_steps=40) -> list:
    """把探测结果转成可直接粘贴的步骤草稿（供人工裁剪）。"""
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
    """把探测结果写成 YAML（probe 清单 + suggested_steps 草稿），返回文件路径。"""
    if path is None:
        d = base_dir() / "shots"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("probe_%s.yaml" % datetime.datetime.now().strftime("%H%M%S"))
    path = anchor(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"probe": probe_data}
    if with_steps:
        payload["suggested_steps"] = suggest_steps(probe_data)
    header = ("# playflow 页面探测结果（%s）\n"
              "# probe = 原始清单；suggested_steps = 可直接粘贴到工作流的步骤草稿（请裁剪）\n"
              % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    path.write_text(header + yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")
    return path


PROBE_HELP = """probe 交互命令：
  [回车]        扫描当前全部页面，输出元素清单 + 选择器建议
  <网址>        打开该网址并扫描
  t=<关键词>    文字匹配测试（自动容错空格），如 t=签收
  <选择器>      选择器测试，如 button[type=submit]
  w=<文件名>    把最近一次扫描保存为 YAML 草稿（含 suggested_steps）
  #<序号>       切换选择器测试的 frame（序号见 list）
  list          列出当前所有页面与 frame
  q             退出"""


def interactive_probe(engine) -> None:
    """登录后在浏览器里人工浏览、按页扫描的交互式探测循环。"""
    frame_idx = 0
    last = None
    print()
    print("已进入 probe 交互模式。")
    print(PROBE_HELP)
    while True:
        try:
            line = input("probe> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        low = line.lower()
        if not line:
            last = collect_probe(engine.ctx, all_pages=True)
            report_probe(last)
            continue
        if low in ("q", "quit", "exit"):
            break
        if low == "list":
            for pi, pg in enumerate(engine.ctx.pages):
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
            data = last or collect_probe(engine.ctx, all_pages=True)
            path = write_probe_file(data, name)
            print("  已保存探测草稿：%s" % path)
            continue
        if line.startswith("t="):
            kw = line[2:].strip()
            print("  文字匹配“%s”结果：" % kw)
            hits = find_button_all(engine.current, [kw])
            if not hits:
                print("  命中 0 个。")
                continue
            for _, fr, _lo, b in hits:
                ftag = "" if fr == engine.current.main_frame else "[iframe] "
                print("  %s<%s> %s [%s]" % (
                    ftag, b.get("tag", "?"), format_text(b.get("text", ""), 40),
                    "可见" if b.get("visible") else "不可见"))
            continue
        if "://" in line or line.startswith("www."):
            from .browser import safe_goto
            url = line if "://" in line else "http://" + line
            try:
                safe_goto(engine.current, url, "页面")
                last = collect_probe(engine.ctx, all_pages=True)
                report_probe(last)
            except Exception as ex:
                print("  打开失败：%s" % str(ex).split("\n")[0])
            continue
        try:
            target = engine.current.frames[frame_idx]
        except Exception:
            target = engine.current.main_frame
        loc = target.locator(line)
        try:
            n = loc.count()
        except Exception as ex:
            print("  选择器不合法或执行失败：%s" % str(ex).split("\n")[0])
            continue
        print("  命中 %d 个" % n)
        for i in range(min(n, 20)):
            b = el_brief(loc.nth(i))
            print("  [%d] <%s> %s [%s]" % (
                i, b.get("tag", "?"), format_text(b.get("text", ""), 40),
                "可见" if b.get("visible") else "不可见"))
