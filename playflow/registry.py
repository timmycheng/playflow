# -*- coding: utf-8 -*-
"""动作注册表：内置动作与自定义动作共用。"""

ACTIONS = {}


def action(name):
    """把函数注册为名为 name 的工作流动作。"""
    def deco(fn):
        ACTIONS[name] = fn
        fn.action_name = name
        return fn
    return deco
