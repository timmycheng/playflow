# -*- coding: utf-8 -*-
"""运行报告：把每次执行的汇总落盘为 JSON + HTML（reports/ 目录）。"""
import datetime
import json

from .utils import base_dir, log


def build_report(engine, status, duration, started_at=None) -> dict:
    """把引擎汇总整理成结构化报告 dict。status: completed / aborted。"""
    tasks = {}
    for name, st in (engine.summary or {}).items():
        tasks[str(name)] = {
            "成功": list(st.get("成功") or []),
            "失败": list(st.get("失败") or []),
            "跳过": list(st.get("跳过") or []),
            "错误": st.get("错误") or "",
        }
    counts = engine.total_counts()
    return {
        "workflow": engine.wf.get("name") or "(未命名)",
        "status": status,
        "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S") if started_at else "",
        "duration_seconds": round(float(duration or 0), 1),
        "dry_run": engine.dry_run,
        "counts": counts,
        "tasks": tasks,
    }


def _escape(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def render_html(report) -> str:
    """极简单文件 HTML 报告（内联样式，离线可看）。"""
    counts = report.get("counts") or {}
    rows = []
    for name, st in (report.get("tasks") or {}).items():
        err = _escape(st.get("错误") or "")
        rows.append(
            "<tr><td>%s</td><td class='ok'>%d</td><td class='%s'>%d</td>"
            "<td>%d</td><td class='err'>%s</td><td>%s</td></tr>"
            % (_escape(name), len(st.get("成功") or []),
               "err" if st.get("失败") else "ok", len(st.get("失败") or []),
               len(st.get("跳过") or []), err,
               _escape("、".join(str(x) for x in st.get("失败") or []))))
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>playflow 运行报告 - %s</title>
<style>
body{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:24px;color:#222}
h1{font-size:20px} table{border-collapse:collapse;width:100%%;margin-top:12px}
th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:14px}
th{background:#f5f5f5}.ok{color:#1a7f37}.err{color:#c62828}
.meta{color:#666;font-size:13px}.dry{background:#fff3cd;padding:2px 8px;border-radius:4px}
</style></head><body>
<h1>%s %s</h1>
<p class="meta">状态：%s · 开始：%s · 耗时：%.1f 秒 ·
成功 %d / 失败 %d / 跳过 %d%s</p>
<table><tr><th>任务</th><th>成功</th><th>失败</th><th>跳过</th><th>任务错误</th><th>失败明细</th></tr>
%s</table>
</body></html>""" % (
        _escape(report.get("workflow")), _escape(report.get("workflow")),
        "<span class='dry'>dry-run</span>" if report.get("dry_run") else "",
        "运行中止" if report.get("status") == "aborted" else "完成",
        _escape(report.get("started_at")), report.get("duration_seconds") or 0,
        counts.get("成功", 0), counts.get("失败", 0), counts.get("跳过", 0),
        " · <span class='err'>存在失败，可运行 playflow run --retry-failed 补跑</span>"
        if counts.get("失败") else "",
        "\n".join(rows))


def write_reports(engine, status, duration, started_at=None):
    """写入 reports/run_<时间>.json 与 .html，返回 (json_path, html_path)。"""
    report = build_report(engine, status, duration, started_at)
    d = base_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    jp = d / ("run_%s.json" % ts)
    hp = d / ("run_%s.html" % ts)
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    hp.write_text(render_html(report), encoding="utf-8")
    log("运行报告已写入：%s" % jp.parent, echo=True)
    return jp, hp
