# -*- coding: utf-8 -*-
"""playflow —— YAML 驱动的 Playwright 自动化引擎。

快速开始：
    from playflow import run_workflow, WorkflowEngine

    run_workflow("workflow.yaml")                  # 命令行等价：playflow run workflow.yaml

自定义动作：
    from playflow import action

    @action("我的动作")
    def my_action(engine, params, step):
        engine.current.goto(params["url"])
"""
from .errors import AbortError, ConfigError, LoopBreak, LoopContinue, PlayFlowError, StepError
from .registry import ACTIONS, action
from .engine import (WorkflowEngine, collect_actions, load_workflow, print_summary,
                     run_workflow, validate_workflow)
from .utils import base_dir, init_stdio, set_base_dir

__version__ = "0.1.0"

__all__ = [
    "AbortError", "ConfigError", "LoopBreak", "LoopContinue", "PlayFlowError", "StepError",
    "ACTIONS", "action",
    "WorkflowEngine", "run_workflow", "load_workflow", "validate_workflow",
    "collect_actions", "print_summary",
    "base_dir", "set_base_dir", "init_stdio",
    "__version__",
]
