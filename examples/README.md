# 示例集 / Examples

所有示例都与仓库自带的 mock 平台（`tests/mock_platform.py`，127.0.0.1:8899，账号 admin/123456）配套。

| 文件 | 演示内容 |
|---|---|
| `demo.yaml` | 最小可用流程：登录 → UKey 人工暂停 → iframe 待办列表（first_row 队列）→ 文字点击/单选/附件/提交 |
| `advanced.yaml` | 高级语法：条件分支、for_each 循环、`request` 接口调用、断言、`once` 模式任务 |
| `deepseek_usage.yaml` | 真实站点示例：登录 DeepSeek 开放平台，抓取当月用量/费用/余额并落盘 JSON |

## 运行

```bash
python tests/mock_platform.py      # 先启动 mock 平台（demo/advanced 需要）
playflow run examples/demo.yaml
playflow run examples/advanced.yaml
playflow run examples/deepseek_usage.yaml    # 需要真实账号（env 中填写）
```

## 新特性速查片段

断点续跑 / 失败重试（命令行开关，无需改 YAML）：

```bash
playflow run demo.yaml --resume          # 从进度文件断点继续
playflow run demo.yaml --retry-failed    # 只补跑 failed 清单
```

数据源任务（`from_csv`）：

```yaml
tasks:
  - name: 批量补录
    from_csv: 待办.csv
    label_column: 单号
    steps:
      - uses: fill
        with: { selector: "#city", text: "{{ row.城市 }}" }
```

子流程复用（`partials` + `include`）：

```yaml
partials:
  归档:
    - uses: download
      with: { optional: true }
    - uses: close_task_page
tasks:
  - name: 待办
    url: http://10.0.0.1/list
    steps:
      - uses: click_row_link
      - include: 归档
```

dry-run / heal / 通知等能力的用法见 README 对应章节。
