# 社区

## 参与贡献

欢迎 Issue 与 PR！请先阅读 [CONTRIBUTING.md](https://github.com/timmycheng/playflow/blob/main/CONTRIBUTING.md)。

开发环境：

```bash
pip install -e .[dev]
pytest                        # 单元测试（无需浏览器）
python tests/selftest.py      # 端到端验收（自动拉起 mock 平台，需 Chrome）
ruff check .
```

约定要点：

- 库内输出走 `logging`，不要新增裸 `print`
- 新动作需标注读/写模式（dry-run 依据），并补 README 动作参考
- 涉及引擎语义的改动请补 selftest 用例并在 PR 中说明验证方式

## 行为准则

参与本项目（Issue / PR / Discussions）请遵守
[行为准则](https://github.com/timmycheng/playflow/blob/main/CODE_OF_CONDUCT.md)。

## 安全问题

请勿公开 Issue 报告安全问题，参见
[SECURITY.md](https://github.com/timmycheng/playflow/blob/main/SECURITY.md)。

## 提问渠道

- 使用疑问、写工作流的思路：[Discussions](https://github.com/timmycheng/playflow/discussions)
- Bug 与功能建议：[Issues](https://github.com/timmycheng/playflow/issues)
