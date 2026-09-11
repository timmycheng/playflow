<!-- 请先确认以下检查项 -->

## 改动说明

<!-- 简述改了什么、为什么改 -->

## 类型

- [ ] 🐞 Bug 修复
- [ ] ✨ 新功能 / 新动作
- [ ] 📖 文档
- [ ] 🔧 工程化 / 重构（不改变行为）

## 自查清单

- [ ] `ruff check .` 通过
- [ ] `pytest` 全绿（新增纯逻辑 → 补 tests/unit 用例）
- [ ] `python tests/selftest.py` 全绿（改动引擎语义时必须跑，并补用例）
- [ ] README / CHANGELOG 已同步更新
- [ ] 库内输出未引入新的裸 `print`（走 `logging` / `log(echo=True)`）
