# 发布指南（维护者）

## 方式一（推荐）：Trusted Publishing，打 tag 自动发布

一次性配置（需要 PyPI 账号所有者在网页操作）：

1. 登录 pypi.org → Account settings → Publishing → **Add a new pending publisher**：
   - PyPI project name: `playflow`
   - Owner: `timmycheng`，Repository: `playflow`，Workflow filename: `publish.yml`
2. 之后每次发布只需：

```bash
# 更新 playflow/__init__.py 的 __version__ 与 CHANGELOG.md 后：
git add -A && git commit -m "..."
git tag -a v0.1.0 -m "playflow 0.1.0"
git push origin main --tags     # push tag 触发 .github/workflows/publish.yml 自动上传
```

## 方式二：本机 twine 上传

```bash
# 1. 更新 playflow/__init__.py 里的 __version__ 与 CHANGELOG.md

# 2. 构建
pip install -e .[dev]
python -m build

# 3. 本地检查
pip install twine
twine check dist/*

# 4. 上传（需要 PyPI API Token，首次会提示输入）
twine upload dist/playflow-<版本>*
```

注意：`playflow` 这个名字在 PyPI 上当前未被占用，**建议尽早发一次 0.1.0 占名**。

## 内网离线安装

```bash
# 外网机制作离线包（含全部依赖）
pip download playflow -d wheels

# 拷贝 wheels/ 到内网
pip install --no-index --find-links=wheels playflow
```

可选依赖同理：`pip download "playflow[xlsx,notify]" -d wheels`。

## 打包单文件 exe（内网 Windows 双击用）

```bat
pip install pyinstaller
pyinstaller -F -n playflow --collect-all playwright run_playflow.py
```

产物 `dist\playflow.exe`，与 `workflow.yaml` 放同一目录即可。

## 本地验收（发布前必跑）

```bash
pip install -e .[dev]
pytest                        # 单元测试（无需浏览器）
python tests/selftest.py      # 端到端验收（自动拉起 mock 平台，需本机 Chrome）
ruff check .
```
