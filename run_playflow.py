# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：pyinstaller -F -n playflow --collect-all playwright run_playflow.py"""
import sys

from playflow.cli import main

if __name__ == "__main__":
    sys.exit(main())
