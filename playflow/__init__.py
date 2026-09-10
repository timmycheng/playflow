# -*- coding: utf-8 -*-
"""playflow —— YAML 驱动的 Playwright 自动化引擎。

快速开始：
    from playflow import run_workflow, WorkflowEngine

    run_workflow("workflow.yaml")                  # 命令行等价：playflow run workflow.yaml
    run_workflow("workflow.yaml", dry_run=True)    # 只执行读动作，写动作跳过
    run_workflow("workflow.yaml", resume=True)     # 断点续跑

自定义动作：
    from playflow import action

    @action("我的动作")
    def my_action(engine, params, step):
        engine.current.goto(params["url"])
"""
from .engine import (
                     WorkflowEngine,
                     collect_actions,
                     expand_partials,
                     load_workflow,
                     print_summary,
                     run_workflow,
                     validate_workflow,
)
from .errors import AbortError, ConfigError, LoopBreak, LoopContinue, PlayFlowError, StepError
from .registry import ACTIONS, action, action_mode
from .utils import base_dir, disable_console_logging, ensure_console_logging, init_stdio, logger, set_base_dir

__version__ = "0.1.0"

__all__ = [
    "AbortError", "ConfigError", "LoopBreak", "LoopContinue", "PlayFlowError", "StepError",
    "ACTIONS", "action", "action_mode",
    "WorkflowEngine", "run_workflow", "load_workflow", "validate_workflow",
    "collect_actions", "print_summary", "expand_partials",
    "base_dir", "set_base_dir", "init_stdio",
    "ensure_console_logging", "disable_console_logging", "logger",
    "__version__",
]
