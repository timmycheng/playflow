# 安全策略 / Security Policy

## 支持的版本

| 版本 | 支持情况 |
| --- | --- |
| 0.1.x | ✅ 支持 |

## 报告漏洞

playflow 会在**你本机**驱动浏览器并处理工作流文件中的凭据（账号密码、登录态 `state.json`）。如发现以下类型的问题，请勿公开 Issue，而是通过 GitHub 的 **Private vulnerability reporting**（仓库 Security 页签 → Report a vulnerability）私下报告：

- 凭据泄漏（密码、token、`state.json`、cookie 落到日志/截图/报告等不该出现的位置）
- 工作流 YAML 解析或路径解析中的注入/越界问题
- 可能导致远程代码执行的输入处理缺陷

请在报告中包含：影响描述、复现步骤（最小工作流示例）、playflow 版本与环境。我们会在确认后尽快修复并发布新版本；修复发布前请勿公开披露细节。

## 安全设计要点（供使用者参考）

- 登录态与凭据默认只写在本机文件（`state.json`、`.pypirc` 不相关），不上传任何远端
- 日志对密码做了脱敏（`fill` 的 `secret: true`、录制时自动替换为 `{{ env.password }}`）
- `record --verify` 与真实平台交互前有确认提示；`--dry-run` 可做只读演练
