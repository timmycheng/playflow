# -*- coding: utf-8 -*-
"""运行结果通知：企业微信 / 钉钉群机器人或通用 JSON webhook。

工作流配置示例：

    notify:
      webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
      # webhook 也可以是列表，一次推多个群
      only_on_failure: false   # true 时只有失败/中止才推送

需要 requests：pip install playflow[notify]
"""
from .utils import log


def build_payload(url, title, text) -> dict:
    """按 webhook 地址识别平台并构造消息体（markdown）。"""
    u = str(url).lower()
    if "qyapi.weixin.qq.com" in u:
        return {"msgtype": "markdown", "markdown": {"content": text}}
    if "oapi.dingtalk.com" in u:
        return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    return {"title": title, "text": text}


def build_summary_text(engine, status, duration, report_path=None) -> str:
    """把运行汇总拼成 markdown 文本。"""
    counts = engine.total_counts()
    lines = ["**playflow 运行%s** —— %s" % (
        "中止" if status == "aborted" else "结束",
        engine.wf.get("name") or "(未命名)")]
    if engine.dry_run:
        lines.append("> dry-run 模式：写动作未执行")
    lines.append("成功 %d · 失败 %d · 跳过 %d · 耗时 %.1f 秒"
                 % (counts["成功"], counts["失败"], counts["跳过"], duration))
    for name, st in (engine.summary or {}).items():
        if st.get("失败") or st.get("错误"):
            detail = "、".join(str(x) for x in (st.get("失败") or []))
            line = "- %s：失败 %d" % (name, len(st.get("失败") or []))
            if st.get("错误"):
                line += "（%s）" % st["错误"]
            if detail:
                line += "：%s" % detail[:200]
            lines.append(line)
    if report_path:
        lines.append("报告：%s" % report_path)
    return "\n".join(lines)


def send_notify(cfg, title, text, logf=log) -> list:
    """把消息推送到 cfg.webhook（字符串或列表），返回发送结果列表。"""
    urls = cfg.get("webhook") or cfg.get("webhooks")
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u) for u in (urls or []) if u]
    if not urls:
        return []
    try:
        import requests
    except ImportError:
        log("未安装 requests，无法发送通知（pip install playflow[notify]）")
        return []
    results = []
    for u in urls:
        payload = build_payload(u, title, text)
        try:
            resp = requests.post(u, json=payload, timeout=float(cfg.get("timeout", 10)))
            ok = resp.status_code < 300
            body = ""
            try:
                body = str(resp.json().get("errmsg") or resp.json().get("errcode") or "")
            except Exception:
                body = resp.text[:100] if not ok else ""
            results.append({"url": u, "ok": ok, "status": resp.status_code, "detail": body})
            if ok:
                log("通知已发送：%s" % u)
            else:
                log("通知发送异常 HTTP %d：%s %s" % (resp.status_code, u, body))
        except Exception as ex:
            results.append({"url": u, "ok": False, "error": str(ex)})
            log("通知发送失败：%s（%s）" % (u, str(ex).split("\n")[0]))
    return results


def notify_run_result(cfg, engine, status, duration, report_paths=None):
    """按 only_on_failure 决定是否推送；report_paths 为 (json, html)。"""
    from .template import as_bool
    if as_bool(cfg.get("only_on_failure", False)):
        counts = engine.total_counts()
        if status == "completed" and counts["失败"] == 0:
            log("运行全部成功，only_on_failure=true，跳过通知。")
            return []
    title = "playflow 运行%s：%s" % ("中止" if status == "aborted" else "结束",
                                     engine.wf.get("name") or "(未命名)")
    report_path = str(report_paths[1]) if report_paths else None
    text = build_summary_text(engine, status, duration, report_path)
    return send_notify(cfg, title, text)
