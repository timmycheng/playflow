# 快速开始

## 本地演练（不需要内网）

仓库自带一个模拟内网平台（SSO 登录 + UKey 模拟验证 + iframe 待办列表 + 附件下载 + confirm 弹窗）：

```bash
git clone https://github.com/timmycheng/playflow.git
cd playflow
pip install playflow
python tests/mock_platform.py          # 启动模拟平台（127.0.0.1:8899）
playflow run examples/demo.yaml        # 账号 admin/123456
```

UKey 环节会暂停并在终端提示，在模拟页面上点击「点击模拟UKey验证」按钮后回终端按回车。
跑完可在 `附件/`、`logs/`、`shots/` 查看归档、日志与截图；把 `examples/demo.yaml` 的
`max_tasks` 调大可批量处理全部 10 条。

## 真实站点示例

- `examples/advanced.yaml` —— 条件、循环、接口调用、断言等高级语法
- `examples/deepseek_usage.yaml` —— 登录 DeepSeek 开放平台，抓取当月用量/费用/余额并落盘 JSON

## 写第一个工作流

一个最小可用的工作流长这样：

```yaml
name: 我的流程
login:
  url: http://10.0.0.1/login
  username: alice
  password: "{{ env.password }}"
  success_url: http://10.0.0.1/home
env:
  password: secret
tasks:
  - name: 待办列表
    url: http://10.0.0.1/list
    steps:
      - uses: click_row_link
      - uses: click_text
        with: { text: [提交, 确定] }
      - uses: close_task_page
```

创作流程推荐：先用 `playflow probe --url 页面地址` 探测页面拿到选择器建议 →
手写或用 `record` 录制 → `--dry-run` 只读验证 → 正式 `run`。
