# 安装

## PyPI 安装

```bash
pip install playflow                 # 含 playwright + pyyaml
```

可选依赖：

| extra | 内容 |
|---|---|
| `playflow[http]` | `request` 动作（requests） |
| `playflow[notify]` | webhook 通知（requests） |
| `playflow[xlsx]` | `from_xlsx` 数据源任务（openpyxl） |
| `playflow[dev]` | pytest / ruff / build 等开发工具 |

## 浏览器要求

本机需已安装 Chrome（默认 `channel: chrome`）；只有 Edge 时把 `browser.channel` 配成 `msedge`。
引擎**不需要** `playwright install` 下载浏览器。

## 源码安装（开发）

```bash
git clone https://github.com/timmycheng/playflow.git
cd playflow
pip install -e .[dev]
```

## 内网离线安装

```bash
pip download playflow -d wheels                                # 外网机
pip install --no-index --find-links=wheels playflow            # 内网机
```

也可打包成单文件 exe：`pyinstaller -F -n playflow --collect-all playwright run_playflow.py`。
