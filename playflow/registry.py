# -*- coding: utf-8 -*-
"""动作注册表：内置动作与自定义动作共用。

每个动作带 mode 标注（"read" / "write"），用于 dry-run：只执行读动作，写动作跳过。
@action 未显式标注时默认 "write"（宁可多跳过，不可误执行）。
"""

ACTIONS = {}


def action(name, mode="write"):
    """把函数注册为名为 name 的工作流动作；mode 决定 dry-run 时是否跳过。"""
    def deco(fn):
        ACTIONS[name] = fn
        fn.action_name = name
        fn.action_mode = mode
        return fn
    return deco


def action_mode(name) -> str:
    """查询动作的读/写模式；未注册或未标注的动作按 "write" 处理。"""
    fn = ACTIONS.get(name)
    return getattr(fn, "action_mode", "write") if fn else "write"
