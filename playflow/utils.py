# -*- coding: utf-8 -*-
"""运行期基础设施：日志、截图、路径解析、人工暂停。"""
import datetime
import logging
import re
import sys
import threading
import time
from pathlib import Path

_BASE_DIR = Path.cwd()
_LOG_DIRNAME = "logs"
_SHOTS_DIRNAME = "shots"
_LOG_FILE = None
_LOG_LOCK = None
_SHOT_N = 0

# 库形态：所有控制台输出走 "playflow" logger；未配置时自动挂默认 handler。
logger = logging.getLogger("playflow")
logger.addHandler(logging.NullHandler())
_CONSOLE_HANDLER = None
_CONSOLE_DISABLED = False


class _MsFormatter(logging.Formatter):
    """控制台格式与旧版 print 保持一致：[HH:MM:SS.mmm] 消息。"""

    def formatTime(self, record, datefmt=None):
        return time.strftime("%H:%M:%S", time.localtime(record.created)) \
            + ".%03d" % int(record.msecs)


def ensure_console_logging():
    """给 "playflow" logger 挂默认控制台 handler（幂等）。

    用户已自行配置 handler 时不再附加，输出完全交给用户的 logging 配置。
    """
    global _CONSOLE_HANDLER
    if _CONSOLE_DISABLED or _CONSOLE_HANDLER is not None:
        return
    for h in logger.handlers:
        if not isinstance(h, logging.NullHandler) and not getattr(h, "_playflow_default", False):
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_MsFormatter("[%(asctime)s] %(message)s"))
    handler._playflow_default = True
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    _CONSOLE_HANDLER = handler


def disable_console_logging():
    """移除默认控制台 handler；用户自己配置 logging 或需要静默时使用。"""
    global _CONSOLE_HANDLER, _CONSOLE_DISABLED
    _CONSOLE_DISABLED = True
    if _CONSOLE_HANDLER is not None:
        logger.removeHandler(_CONSOLE_HANDLER)
        _CONSOLE_HANDLER = None


def init_stdio():
    """Windows 控制台兜底：打印异常字符不崩溃；并确保控制台输出可用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ensure_console_logging()


def set_base_dir(path):
    """设置相对路径（日志/截图/附件/登录态）的锚定目录，并重置日志缓存。"""
    global _BASE_DIR, _LOG_FILE
    _BASE_DIR = Path(path)
    _LOG_FILE = None


def base_dir() -> Path:
    return _BASE_DIR


def anchor(path) -> Path:
    """相对路径锚定到 base_dir；绝对路径原样返回。"""
    p = Path(path)
    return p if p.is_absolute() else _BASE_DIR / p


def _log_file() -> Path:
    global _LOG_FILE
    if _LOG_FILE is None:
        d = _BASE_DIR / _LOG_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        _LOG_FILE = d / ("run_%s.log" % datetime.date.today().strftime("%Y%m%d"))
    return _LOG_FILE


def reset_log():
    """测试用：丢弃日志文件缓存。"""
    global _LOG_FILE
    _LOG_FILE = None


def log(msg, echo=False):
    """写 UTF-8 日志；echo=True 时同步输出到控制台（经 "playflow" logger）。"""
    global _LOG_LOCK
    if _LOG_LOCK is None:
        _LOG_LOCK = threading.Lock()
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], msg)
    try:
        with _LOG_LOCK:
            with open(_log_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    if echo:
        try:
            # 时间戳由 handler 的 formatter 统一加，避免与文件行前缀重复
            logger.info("%s", msg)
        except Exception:
            pass


def shot(page, stage, echo=False):
    """关键动作截图到 shots/，文件名：序号_阶段_时间戳.png。"""
    global _SHOT_N
    try:
        _SHOT_N += 1
        d = _BASE_DIR / _SHOTS_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
        path = d / ("%03d_%s_%s.png" % (_SHOT_N, stage, ts))
        page.screenshot(path=str(path), full_page=True)
        log("截图：%s" % path.name, echo=echo)
        return path
    except Exception as e:
        log("截图失败(%s)：%s" % (stage, e))
        return None


def reset_shots():
    """测试用：重置截图序号。"""
    global _SHOT_N
    _SHOT_N = 0


def dump_scene(page, reason=""):
    """致命失败现场：截图 + 保存页面 HTML。"""
    if page is not None:
        shot(page, "致命现场")
        try:
            d = _BASE_DIR / _SHOTS_DIRNAME
            d.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
            hp = d / ("fatal_scene_%s.html" % ts)
            hp.write_text(page.content(), encoding="utf-8")
            log("已保存现场 HTML：%s" % hp)
        except Exception as e:
            log("保存现场 HTML 失败：%s" % e)
    log("致命失败：%s" % reason, echo=True)


def pause_for_manual(hint=""):
    """人工介入暂停点（如浏览器外的 UKey/短信验证）：回车继续。"""
    print()
    print("=" * 62)
    if hint:
        print(">>> %s" % hint)
    print(">>> 完成后回到本窗口，按回车继续……")
    print("=" * 62)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def sanitize_filename(name) -> str:
    """清洗 Windows 非法文件名字符。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip(" .")
    return name[:120] or "file"


def format_text(text, limit=30) -> str:
    """压缩空白并按长度截断，用于日志/控制台展示。"""
    t = " ".join(str(text or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")
