# -*- coding: utf-8 -*-
"""条件求值：用于步骤的 if、while 循环与 assert 断言。

支持三种写法：
  1. 字符串表达式：`text("...") == "abc"`、`{{ count }} > 3`、`A and (B or C)`
  2. 结构化映射：`{contains: [a, b]}` / `{and: [...]}` / `{exists: "#id"}`
  3. 条件函数：exists / visible / absent / has_text / count / text / attr / value /
     page_count / url / title
"""
from __future__ import annotations

import re

from .errors import ConfigError
from .template import VAR_RE, as_bool, dig, render

_FUNC_NAMES = ("exists", "visible", "absent", "has_text", "count", "text", "attr",
               "value", "page_count", "url", "title")
_FUNC_RE = re.compile(r"^([A-Za-z_]\w*)\s*\((.*)\)$", re.S)
_NUM_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$")
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)*$")

_SYM_OPS = (("==", "==", False), ("!=", "!=", False), (">=", ">=", False),
            ("<=", "<=", False), (">", ">", False), ("<", "<", False))
_WORD_OPS = (
    (re.compile(r"(?<![A-Za-z0-9_])not\s+contains\b"), "contains", True),
    (re.compile(r"(?<![A-Za-z0-9_])not\s+matches\b"), "matches", True),
    (re.compile(r"(?<![A-Za-z0-9_])not\s+in\b"), "in", True),
    (re.compile(r"(?<![A-Za-z0-9_])contains\b"), "contains", False),
    (re.compile(r"(?<![A-Za-z0-9_])matches\b"), "matches", False),
    (re.compile(r"(?<![A-Za-z0-9_])startswith\b"), "startswith", False),
    (re.compile(r"(?<![A-Za-z0-9_])endswith\b"), "endswith", False),
    (re.compile(r"(?<![A-Za-z0-9_])in\b"), "in", False),
)
_MALFORMED_CHARS = "=<>!"


def _find_top(text, token):
    """在括号与引号之外查找 token，返回下标或 -1。"""
    depth, quote, i, n = 0, None, 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(token, i):
            return i
        i += 1
    return -1


def _split_top(text, sep):
    """按分隔符在顶层切分（括号/引号内不切）。"""
    parts, rest = [], text
    while True:
        i = _find_top(rest, sep)
        if i < 0:
            parts.append(rest)
            return parts
        parts.append(rest[:i])
        rest = rest[i + len(sep):]


def _find_cmp(text):
    """在顶层查找比较运算符（两侧可有/无空格），返回 (起, 止, 运算符, 是否取反)。

    词形运算符（contains / in / …）要求两侧不是标识符字符，避免 "login" 里的 in
    被当成运算符；找不到时返回 None。
    """
    depth, quote, i, n = 0, None, 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            i += 1
            continue
        if c in "([{":
            depth += 1
            i += 1
            continue
        if c in ")]}":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            for tok, op, neg in _SYM_OPS:
                if text.startswith(tok, i):
                    return i, i + len(tok), op, neg
            for rx, op, neg in _WORD_OPS:
                m = rx.match(text, i)
                if m:
                    return i, m.end(), op, neg
        i += 1
    return None


def _has_top_level(text, chars) -> bool:
    """顶层（引号/括号外）是否出现指定字符，用于识别写错的表达式。"""
    depth, quote, i, n = 0, None, 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in chars:
            return True
        i += 1
    return False


def _strip_outer(text):
    """去掉最外层成对的括号与 ${{ }} / {{ }} 包裹。"""
    s = text.strip()
    if s.startswith("${{") and s.endswith("}}"):
        s = s[3:-2].strip()
    elif s.startswith("{{") and s.endswith("}}"):
        s = s[2:-2].strip()
    while s.startswith("(") and s.endswith(")"):
        if _find_top(s[1:], ")") == len(s) - 2:
            s = s[1:-1].strip()
        else:
            break
    return s


def eval_condition(cond: object, engine: object) -> bool:
    """对条件求值；cond 为 None 时视为 True。"""
    if cond is None:
        return True
    if isinstance(cond, bool):
        return cond
    if isinstance(cond, (int, float)):
        return cond != 0
    if isinstance(cond, dict):
        return _eval_dict(cond, engine)

    s = _strip_outer(str(cond))
    if s == "":
        return False

    for sep in ("||", " or "):
        parts = _split_top(s, sep)
        if len(parts) > 1:
            return any(eval_condition(part, engine) for part in parts)
    for sep in ("&&", " and "):
        parts = _split_top(s, sep)
        if len(parts) > 1:
            return all(eval_condition(part, engine) for part in parts)
    if s.lower().startswith("not "):
        return not eval_condition(s[4:], engine)

    fm = _FUNC_RE.match(s)
    if fm:
        if fm.group(1) in _FUNC_NAMES:
            return as_bool(_eval_func(fm.group(1), fm.group(2), engine))
        raise ConfigError("无法识别的条件函数：%s（可用：%s）"
                          % (fm.group(1), "、".join(_FUNC_NAMES)))

    cmp = _find_cmp(s)
    if cmp:
        start, end, op, neg = cmp
        left_s, right_s = s[:start].strip(), s[end:].strip()
        if not left_s or not right_s:
            raise ConfigError("条件表达式缺少操作数：%r" % (cond,))
        res = _compare(_resolve(left_s, engine), op, _resolve(right_s, engine))
        return (not res) if neg else res

    if _has_top_level(s, _MALFORMED_CHARS):
        raise ConfigError("无法解析的条件表达式：%r（请检查比较运算符与函数名，"
                          "字符串值建议加引号）" % (cond,))
    return as_bool(_resolve(s, engine))


def _eval_dict(cond, engine):
    if "and" in cond:
        return all(eval_condition(c, engine) for c in cond["and"])
    if "or" in cond:
        return any(eval_condition(c, engine) for c in cond["or"])
    if "not" in cond:
        return not eval_condition(cond["not"], engine)
    for op in ("contains", "equals", "matches", "startswith", "endswith",
               "exists", "visible", "absent", "has_text"):
        if op not in cond:
            continue
        args = cond[op] if isinstance(cond[op], list) else [cond[op]]
        args = [render(a, engine.vars) for a in args]
        if op == "contains":
            return len(args) > 1 and str(args[1]) in str(args[0])
        if op == "equals":
            return len(args) > 1 and args[0] == args[1]
        if op == "matches":
            return len(args) > 1 and re.search(str(args[1]), str(args[0])) is not None
        if op == "startswith":
            return len(args) > 1 and str(args[0]).startswith(str(args[1]))
        if op == "endswith":
            return len(args) > 1 and str(args[0]).endswith(str(args[1]))
        if op == "exists":
            return engine.selector_exists(str(args[0]))
        if op == "visible":
            return engine.selector_visible(str(args[0]))
        if op == "absent":
            return not engine.selector_exists(str(args[0]))
        if op == "has_text":
            return engine.has_text(str(args[0]))
    raise ConfigError("无法识别的 if 条件：%r" % (cond,))


def _resolve(text, engine):
    """把表达式一侧解析成值：引号→字符串、数字/布尔→字面量、函数调用→求值、
    {{ }}→模板、裸变量名→变量值、其余→字符串。"""
    t = str(text).strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    if _NUM_RE.match(t):
        try:
            return int(t)
        except ValueError:
            return float(t)
    fm = _FUNC_RE.match(t)
    if fm:
        if fm.group(1) in _FUNC_NAMES and engine is not None:
            return _eval_func(fm.group(1), fm.group(2), engine)
        if fm.group(1) not in _FUNC_NAMES:
            raise ConfigError("无法识别的条件函数：%s" % fm.group(1))
    if VAR_RE.search(t):
        return render(t, engine.vars)
    if engine is not None and _IDENT_RE.match(t):
        val = dig(engine.vars, t)
        return val if val is not None else t
    return render(t, engine.vars) if engine is not None else t


def _parse_args(raw, engine):
    args = []
    for part in _split_top(raw or "", ","):
        part = part.strip()
        if not part:
            continue
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            args.append(part[1:-1])
        else:
            args.append(_resolve(part, engine))
    return args


def _arg(args, idx, default=""):
    return args[idx] if idx < len(args) else default


def _eval_func(name, raw, engine):
    args = _parse_args(raw, engine)
    if name == "exists":
        return engine.selector_exists(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "visible":
        return engine.selector_visible(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "absent":
        return not engine.selector_exists(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "has_text":
        return engine.has_text(str(_arg(args, 0)))
    if name == "count":
        return engine.count_elements(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "text":
        return engine.get_text(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "attr":
        return engine.get_attr(str(_arg(args, 0)), str(_arg(args, 1)),
                               frame=_arg(args, 2, None) or None)
    if name == "value":
        return engine.get_value(str(_arg(args, 0)), frame=_arg(args, 1, None) or None)
    if name == "page_count":
        return len(engine.ctx.pages)
    if name == "url":
        return engine.current.url
    if name == "title":
        return engine.current.title()
    raise ConfigError("无法识别的条件函数：%s" % name)


def _compare(left, op, right):
    if op in (">", "<", ">=", "<="):
        try:
            left, right = float(left), float(right)
        except (TypeError, ValueError):
            if left is None or right is None:
                return False
            left, right = str(left), str(right)
    if op == "contains":
        return str(right) in str(left)
    if op == "matches":
        return re.search(str(right), str(left)) is not None
    if op == "startswith":
        return str(left).startswith(str(right))
    if op == "endswith":
        return str(left).endswith(str(right))
    if op == "in":
        try:
            return left in right
        except TypeError:
            return str(left) in str(right)
    if op == "==":
        return left == right or str(left) == str(right)
    if op == "!=":
        return not (left == right or str(left) == str(right))
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    return False
