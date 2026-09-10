# -*- coding: utf-8 -*-
"""异常层级。"""


class PlayFlowError(Exception):
    """所有 playflow 异常的基类。"""


class ConfigError(PlayFlowError):
    """工作流配置 / 校验错误（用户看的中文提示）。"""


class AbortError(PlayFlowError):
    """致命错误：浏览器、登录或页面打不开，整个流程中止。"""


class StepError(PlayFlowError):
    """单个步骤失败：默认使当前任务失败，不中断整体；可用 continue_on_error 忽略。"""


class LoopBreak(Exception):
    """break 动作的内部信号。"""


class LoopContinue(Exception):
    """continue 动作的内部信号。"""
