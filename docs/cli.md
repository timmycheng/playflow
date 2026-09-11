# 命令行

```bash
playflow run workflow.yaml [--headed|--headless] [--channel chrome]
playflow run workflow.yaml --dry-run         # 只执行读动作，写动作跳过
playflow run workflow.yaml --resume          # 断点续跑
playflow run workflow.yaml --retry-failed    # 只补跑上次失败的任务
playflow run workflow.yaml --validate        # 只校验
playflow validate workflow.yaml
playflow probe [--workflow workflow.yaml] [--url 网址]
playflow record out.yaml [--workflow workflow.yaml] [--url 网址] [--verify]
playflow heal workflow.yaml [--url 网址] [--fix] [--headed] [--min-score 0.55]
python -m playflow                           # 等价；不带子命令进交互菜单
```

## run 选项

| 选项 | 说明 |
|---|---|
| `--headed` / `--headless` | 显示/隐藏浏览器窗口 |
| `--channel` | 浏览器渠道，默认 chrome |
| `--validate` | 只校验不执行 |
| `--dry-run` | 只执行读动作，写动作跳过 |
| `--resume` | 依据 progress 文件跳过已完成任务 |
| `--retry-failed` | 依据 failed 清单只补跑失败任务 |

## probe 交互命令

```
[回车]        扫描当前全部页面，输出元素清单 + 选择器建议
<网址>        打开该网址并扫描
t=<关键词>    文字匹配测试（容错空格），如 t=签收
<选择器>      选择器测试，如 button[type=submit]
w=<文件名>    把最近一次扫描保存为 YAML 草稿
#<序号>       切换选择器测试的 frame
list          列出当前所有页面与 frame
q             退出
```

## record

`playflow record out.yaml` 在浏览器里录制人工操作并生成 YAML 步骤（密码自动脱敏）。
加 `--verify` 会在录制完成后逐步回放验证（会再次真实执行操作，建议只在测试环境使用），
失败步骤在生成的 YAML 中以注释标出。

## heal

`playflow heal 流程.yaml` 检查步骤定位参数在页面上的可达性，给出相似度评分的修复建议；
`--fix` 生成 `<名字>.healed.yaml`（不改原文件）。只检查“登录后 + `--url` 指定页面”
这一个页面状态。
