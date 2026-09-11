# playflow

**YAML 驱动的 Playwright 自动化引擎** —— 把浏览器操作写成工作流文件，面向内网老平台的批量任务自动化。

[![CI](https://github.com/timmycheng/playflow/actions/workflows/ci.yml/badge.svg)](https://github.com/timmycheng/playflow/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/playflow)](https://pypi.org/project/playflow/)
[![Python](https://img.shields.io/pypi/pyversions/playflow)](https://pypi.org/project/playflow/)

```yaml
name: 我的流程
login:
  url: http://10.0.0.1/login
  username: alice
  password: "{{ env.password }}"
  success_url: http://10.0.0.1/home
steps:
  - uses: goto
    with: { url: "http://10.0.0.1/list" }
  - uses: click_text
    with: { text: [签收, 受理], optional: true }
  - uses: pick_radio
    with: { text: 同意 }
  - uses: click_text
    with: { text: [提交, 确定] }
```

```bash
pip install playflow
playflow run workflow.yaml
```

## 核心能力

- **一个执行引擎**：步骤 + 条件 + 循环 + 变量，页面再复杂也只改 YAML、不改代码
- **面向真实老平台**：文字匹配忽略空格、自动遍历 iframe、新标签页/同页跳转兼容、穿透 Shadow DOM
- **无人值守**：断点续跑、失败重试、运行报告、企业微信/钉钉通知、异常 trace
- **创作工具**：probe 探测、record 录制（可回放验证）、heal 改版自检、dry-run 只读演练
- **更多形态**：from_csv/from_xlsx 数据源任务、partials 子流程复用

## 文档导航

- [安装](installation.md)
- [快速开始](quickstart.md)
- [工作流结构 / 断点续跑 / dry-run / heal / 通知 / 数据源 / partials](workflow.md)
- [动作参考](actions.md)
- [命令行](cli.md)
- [Python API](api.md)
- [参与贡献](community.md)
