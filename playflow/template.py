# -*- coding: utf-8 -*-
"""变量模板：{{ name }} / {{ a.b.0 }}，整串是单个模板时保留原始类型。"""
import re

VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def dig(value, path):
    """按点路径取值：a.b.0.c；路径不存在返回 None。"""
    for part in str(path).split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if value is None:
            return None
    return value


def render(value, variables):
    """递归渲染 dict/list/str 中的 {{ }}；整个字符串就是一个模板时返回原始值。"""
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [render(v, variables) for v in value]
    if not isinstance(value, str):
        return value
    m = VAR_RE.fullmatch(value.strip())
    if m:
        return dig(variables, m.group(1).strip())

    def _sub(mm):
        val = dig(variables, mm.group(1).strip())
        return "" if val is None else str(val)

    return VAR_RE.sub(_sub, value)


def as_bool(value):
    """字符串/数字转布尔；"false"/"0"/"no"/"" 视为 False。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("", "0", "false", "no", "none", "null", "off")
