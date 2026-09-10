# -*- coding: utf-8 -*-
"""
mock_platform.py —— 本地模拟内网业务平台（外网全流程彩排专用）

用 Python 标准库 http.server 实现的单文件模拟平台，端口 8899。
用途：在外网开发机上完整彩排 tool_kit.py 的全部流程，再去内网实战。

模拟内容与真实平台的对应关系：
  1. /login           SSO 账密登录页（admin / 123456），提交后种 session cookie
  2. /ukey            模拟"UKey 验证"环节。真实环境是浏览器外的独立驱动弹窗、无法自动化，
                      这里用页面按钮"点击模拟UKey验证"代替人工点击，用于验证半自动暂停逻辑
  3. /home            任务平台首页，两个 tab 分别指向两个任务列表页
  4. /list1_page      类别一任务列表（直接渲染，5 条任务）
     /list2_page      类别二任务列表（iframe 嵌套实现，用于验证工具的 iframe 遍历能力，5 条任务）
  5. /task/{任务号}    任务详情页：含"签收"按钮（点击后变已签收）、两个 radio（同意/驳回，
                       label 关联）、附件下载链接、"提 交"按钮（故意带空格，验证文字匹配容错），
                       提交时弹原生 confirm 对话框
  6. 已提交的任务自动从列表消失；/reset 可重置任务数据；/status 可查看 JSON 状态

账号：admin / 123456
启动：python tests/mock_platform.py   （Ctrl+C 停止）
"""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

HOST, PORT = "127.0.0.1", 8899
USERS = {"admin": "123456"}          # 模拟 SSO 账号

_LOCK = threading.Lock()
_SESSIONS = {}                        # sid -> {"user": str, "ukey": bool}
_TASKS = {}                           # 任务号 -> {no/type/title/status/decision}


def _default_tasks():
    """预置两个类别各 5 条任务：T1xx 属类别一，T2xx 属类别二"""
    data = [
        ("T101", "设备报修", "打印机卡纸无法打印"),
        ("T102", "网络故障", "办公区网络突然断开"),
        ("T103", "设备报修", "投影仪遥控器失灵"),
        ("T104", "数据申请", "申请导出本月考勤数据"),
        ("T105", "系统权限申请", "申请财务系统查看权限"),
        ("T201", "权限申请", "申请OA流程审批权限"),
        ("T202", "数据申请", "申请人事月度报表数据"),
        ("T203", "系统权限申请", "申请VPN权限开通"),
        ("T204", "网络故障", "会议室WiFi无法连接"),
        ("T205", "设备报修", "工位显示器花屏闪烁"),
    ]
    return {no: {"no": no, "type": t, "title": ti,
                 "status": "pending", "decision": ""} for no, t, ti in data}


TASKS = _default_tasks()


def reset_tasks():
    global TASKS
    with _LOCK:
        TASKS = _default_tasks()


# ---------------------------------------------------------------- 页面模板
PAGE_CSS = """
body { font-family: "Microsoft YaHei", sans-serif; margin: 24px; background:#f5f6f8; }
h1 { color:#1a4d8f; } h2 { color:#333; }
table { border-collapse: collapse; background:#fff; }
th, td { border: 1px solid #999; padding: 6px 14px; }
th { background:#dce6f1; }
a { color:#1155cc; }
.btn { display:inline-block; padding:8px 22px; margin:6px 4px; background:#1a73e8;
       color:#fff !important; border:none; border-radius:4px; font-size:15px; cursor:pointer; }
.btn.green { background:#188038; }
.panel { background:#fff; border:1px solid #ddd; padding:16px 24px; margin:12px 0; }
.tip { color:#886; font-size:13px; }
.badge { color:#188038; font-weight:bold; }
"""


def html_page(title, body):
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>%s</title><style>%s</style></head><body>%s</body></html>"
            % (title, PAGE_CSS, body))


STATUS_TEXT = {"pending": "待处理", "signed": "已签收", "submitted": "已提交"}


def login_html(err=""):
    err_html = "<p style='color:#c00'>登录失败：%s</p>" % err if err else ""
    body = """
<h1>内网统一登录（SSO）</h1>
<div class="panel">
%s
<form method="post" action="/login">
  <p>用户名：<input type="text" name="username" id="username" placeholder="请输入用户名"></p>
  <p>密&nbsp;&nbsp;码：<input type="password" name="password" id="password" placeholder="请输入密码"></p>
  <p><button type="submit" class="btn">登 录</button></p>
</form>
<p class="tip">模拟账号：admin / 123456</p>
</div>""" % err_html
    return html_page("SSO登录", body)


def ukey_html():
    body = """
<h1>UKey 身份验证</h1>
<div class="panel">
<p>请插入 UKey 并完成验证。</p>
<p><b>【模拟说明】</b>真实环境中，这一步是浏览器外的独立 UKey 驱动弹窗，无法被自动化工具操作，
需要人工处理；本模拟页用下面的按钮代替人工动作。</p>
<a class="btn green" href="/ukey_verify">点击模拟UKey验证</a>
</div>"""
    return html_page("UKey验证", body)


def home_html():
    body = """
<h1>任务处理平台</h1>
<div class="panel">
<p>请选择任务类别：</p>
<a class="btn" href="/list1_page">类别一（设备维修类）</a>
<a class="btn" href="/list2_page">类别二（权限申请类）</a>
</div>
<p class="tip">模拟辅助：<a href="/status">查看任务状态JSON</a> ｜
<a href="/reset">重置任务数据</a></p>"""
    return html_page("任务处理平台", body)


def list_rows_html(cat_no):
    """生成列表行：已提交的任务从列表消失"""
    rows = []
    for no, t in TASKS.items():
        if not no.startswith("T%d" % cat_no):
            continue
        if t["status"] == "submitted":
            continue
        rows.append(
            "<tr><td>%s</td><td>%s</td>"
            "<td><a href='/task/%s' target='_blank'>%s</a></td><td>%s</td></tr>"
            % (no, t["type"], no, t["title"], STATUS_TEXT[t["status"]]))
    if not rows:
        rows.append("<tr><td colspan='4'>（列表已空，全部处理完毕）</td></tr>")
    return "".join(rows)


LIST_TABLE = """
<h1>任务列表（类别 %d）</h1>
<table>
<tr><th>任务号</th><th>任务类型</th><th>任务标题</th><th>状态</th></tr>
%s
</table>"""


def list1_html():
    return html_page("类别一任务列表", LIST_TABLE % (1, list_rows_html(1)))


def list2_inner_html():
    """被 iframe 嵌套加载的列表页正文"""
    return html_page("类别二任务列表", LIST_TABLE % (2, list_rows_html(2)))


def list2_page_html():
    """类别二列表：故意用 iframe 嵌套，验证工具的全 frame 遍历能力"""
    body = """
<h1>任务列表（类别二 · iframe版）</h1>
<p class="tip">本页列表位于 iframe 内，用于验证自动化工具的 frame 遍历能力。</p>
<iframe src="/list2" width="98%%" height="480" style="background:#fff;border:1px solid #999"></iframe>
"""
    return html_page("类别二任务列表", body)


def detail_html(t):
    """任务详情页：签收 + radio（label 关联）+ 附件下载 + 带 confirm 的"提 交"按钮"""
    if t["status"] == "pending":
        sign_html = ("""<form method="post" action="/sign/%s" style="display:inline">
<button type="submit" class="btn green">签收</button></form>""" % t["no"])
    else:
        sign_html = "<span class='badge'>已签收 ✔</span>"

    body = """
<h1>任务详情：%s</h1>
<div class="panel">
<p><b>任务号：</b>%s</p>
<p><b>任务类型：</b>%s</p>
<p><b>任务标题：</b>%s</p>
<p><b>当前状态：</b>%s</p>
<p>签收操作：%s</p>
</div>
<div class="panel">
<h2>处理意见</h2>
<form method="post" action="/submit/%s">
  <p>
    <input type="radio" name="decision" id="d_agree" value="agree">
    <label for="d_agree">同意</label>
    &nbsp;&nbsp;
    <input type="radio" name="decision" id="d_reject" value="reject">
    <label for="d_reject">驳回</label>
  </p>
  <p>附件：<a href="/attachment/%s" download>下载附件</a></p>
  <p><button type="submit" class="btn"
     onclick="return confirm('确认提交该任务？提交后不可修改。')">提 交</button></p>
</form>
</div>""" % (t["title"], t["no"], t["type"], t["title"],
             STATUS_TEXT[t["status"]], sign_html, t["no"], t["no"])
    return html_page("任务详情_%s" % t["no"], body)


def success_html(t):
    body = """
<h1>提交成功</h1>
<div class="panel"><p>任务 <b>%s</b>（%s）已提交，处理意见：<b>%s</b>。</p>
<p>本窗口可以关闭。<a href="/home" target="_blank">返回平台首页</a></p></div>
""" % (t["no"], t["type"], t["decision"])
    return html_page("提交成功", body)


# ---------------------------------------------------------------- HTTP 处理
class MockHandler(BaseHTTPRequestHandler):

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):
        pass  # 静默访问日志，避免干扰观察工具输出

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _html(self, body, code=200, headers=None):
        self._send(code, body, "text/html; charset=utf-8", headers)

    def _redirect(self, loc):
        self._send(302, "", "text/html; charset=utf-8", {"Location": loc})

    def _session(self):
        """从 Cookie 解析当前会话；无效返回 None"""
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid":
                return _SESSIONS.get(v)
        return None

    def _body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _task(self, tid):
        return TASKS.get(tid)

    # ---------- GET 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        sess = self._session()

        if path in ("/", "/home"):
            if not sess:
                return self._redirect("/login")
            if not sess.get("ukey"):
                return self._redirect("/ukey")
            return self._html(home_html())

        if path == "/login":
            err = parse_qs(urlparse(self.path).query).get("err", [""])[0]
            return self._html(login_html(err))

        if path == "/ukey":
            if not sess:
                return self._redirect("/login")
            return self._html(ukey_html())

        if path == "/ukey_verify":
            if not sess:
                return self._redirect("/login")
            with _LOCK:
                sess["ukey"] = True
            return self._redirect("/home")

        if path == "/list1_page":
            if not sess or not sess.get("ukey"):
                return self._redirect("/login")
            return self._html(list1_html())

        if path in ("/list2_page", "/list2"):
            if not sess or not sess.get("ukey"):
                return self._redirect("/login")
            body = list2_page_html() if path == "/list2_page" else list2_inner_html()
            return self._html(body)

        if path == "/list1":
            if not sess or not sess.get("ukey"):
                return self._redirect("/login")
            return self._html(list1_html())

        if path.startswith("/task/"):
            if not sess or not sess.get("ukey"):
                return self._redirect("/login")
            t = self._task(path.split("/")[-1])
            if not t:
                return self._html(html_page("错误", "<h1>任务不存在</h1>"), 404)
            return self._html(detail_html(t))

        if path.startswith("/attachment/"):
            if not sess:
                return self._redirect("/login")
            t = self._task(path.split("/")[-1])
            if not t:
                return self._send(404, b"no task", "text/plain")
            fname = "报告_%s_%s.txt" % (t["type"], t["no"])
            content = ("这是任务 %s 的模拟附件\n任务类型：%s\n任务标题：%s\n"
                       "—— mock_platform 自动生成\n" % (t["no"], t["type"], t["title"]))
            self._send(200, content, "application/octet-stream", {
                "Content-Disposition":
                    "attachment; filename=\"%s_report_%s.txt\"; filename*=UTF-8''%s"
                    % (t["no"], t["no"], quote(fname)),
            })
            return

        if path == "/status":
            with _LOCK:
                data = {"tasks": list(TASKS.values()), "sessions": len(_SESSIONS)}
            return self._send(200, json.dumps(data, ensure_ascii=False, indent=2),
                              "application/json; charset=utf-8")

        if path == "/reset":
            reset_tasks()
            return self._html(html_page("已重置",
                              "<h1>任务数据已重置</h1><p><a href='/home'>返回首页</a></p>"))

        return self._html(html_page("404", "<h1>页面不存在：%s</h1>" % path), 404)

    # ---------- POST 路由 ----------
    def do_POST(self):
        path = urlparse(self.path).path
        sess = self._session()

        if path == "/login":
            form = self._body()
            u, p = form.get("username", ""), form.get("password", "")
            if USERS.get(u) == p:
                sid = secrets.token_hex(8)
                with _LOCK:
                    _SESSIONS[sid] = {"user": u, "ukey": False}
                # 登录成功：种 session cookie，跳转 UKey 验证页
                return self._send(302, "", "text/html; charset=utf-8",
                                  {"Location": "/ukey",
                                   "Set-Cookie": "sid=%s; Path=/" % sid})
            return self._redirect("/login?err=" + quote("用户名或密码错误"))

        if path.startswith("/sign/"):
            if not sess:
                return self._redirect("/login")
            t = self._task(path.split("/")[-1])
            if t and t["status"] == "pending":
                with _LOCK:
                    t["status"] = "signed"
            return self._redirect("/task/%s" % t["no"]) if t else self._redirect("/home")

        if path.startswith("/submit/"):
            if not sess:
                return self._redirect("/login")
            t = self._task(path.split("/")[-1])
            if not t:
                return self._redirect("/home")
            form = self._body()
            with _LOCK:
                t["decision"] = {"agree": "同意", "reject": "驳回"}.get(
                    form.get("decision", ""), "未选择")
                t["status"] = "submitted"      # 提交后从列表消失
            return self._html(success_html(t))

        return self._html(html_page("404", "<h1>页面不存在：%s</h1>" % path), 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), MockHandler)
    server.allow_reuse_address = True
    print("=" * 56)
    print("模拟内网平台已启动： http://%s:%d" % (HOST, PORT))
    print("模拟账号：admin / 123456")
    print("重置任务： http://%s:%d/reset" % (HOST, PORT))
    print("按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n模拟平台已停止")


if __name__ == "__main__":
    main()
