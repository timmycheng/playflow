# -*- coding: utf-8 -*-
"""download 附件动作的过滤逻辑单元测试（无浏览器，用 Fake 页面对象）。"""
from pathlib import Path

import pytest

from playflow.dom import download


class FakeDownload:
    suggested_filename = "report.pdf"

    def __init__(self):
        self.saved = None

    def save_as(self, path):
        self.saved = path


class FakeDownloadCtx:
    def __init__(self):
        self.value = FakeDownload()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeEl:
    def __init__(self, href=None, text="下载", download_attr=None):
        self.href = href
        self.text = text
        self.download_attr = download_attr

    def evaluate(self, js):
        return {"tag": "button", "text": self.text, "visible": True, "id": "",
                "name": "", "type": "", "placeholder": "", "value": ""}

    def get_attribute(self, name):
        if name == "href":
            return self.href
        if name == "download":
            return self.download_attr
        return None

    def click(self):
        pass


class FakeLocator:
    def __init__(self, els):
        self.els = els

    def count(self):
        return len(self.els)

    def nth(self, i):
        return self.els[i]


class FakeFrame:
    def __init__(self, loc):
        self.loc = loc

    def locator(self, selector):
        return self.loc


class FakePage:
    url = "http://x/usage"

    def __init__(self):
        self.downloads = []

    def expect_download(self, timeout=None):
        ctx = FakeDownloadCtx()
        self.downloads.append(ctx.value)
        return ctx

    def go_back(self, *args, **kwargs):
        raise AssertionError("不该回退页面")


def _frames(*els):
    return [FakeFrame(FakeLocator(list(els)))]


def _logf(*args, **kwargs):
    pass


@pytest.mark.parametrize("href", [None, "#", "javascript:void(0)"])
def test_explicit_selector_accepts_non_href_elements(tmp_path, href):
    """显式 selector 时应信任用户选择：无 href 的按钮/JS 链接也能下载。"""
    page = FakePage()
    out = download(page, _frames(FakeEl(href=href, text="导出报表")), tmp_path,
                   selector="button#export", logf=_logf)
    assert out is not None and Path(out).name == "report.pdf"
    assert len(page.downloads) == 1


def test_autodiscovery_still_filters_non_download_elements(tmp_path):
    page = FakePage()
    assert download(page, _frames(FakeEl(href=None, text="提交")), tmp_path,
                    logf=_logf) is None
    assert page.downloads == []


def test_autodiscovery_matches_download_keyword(tmp_path):
    page = FakePage()
    out = download(page, _frames(FakeEl(href="http://x/attachment", text="附件下载")),
                   tmp_path, logf=_logf)
    assert out is not None and Path(out).name == "report.pdf"
    assert len(page.downloads) == 1
