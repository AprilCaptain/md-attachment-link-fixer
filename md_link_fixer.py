#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Entry point for Markdown attachment renamer & link fixer (CLI + PySide6 UI).
"""

import argparse
import logging
import sys

from md_link_fixer.core import DEFAULT_RENAME_CATEGORY, get_app_root, run_pipeline


def parse_args():
    parser = argparse.ArgumentParser(description="Markdown路径修复工具")
    parser.add_argument("--root", help="指定工作根目录（默认使用脚本所在目录）")
    parser.add_argument(
        "--rename-types",
        nargs="+",
        default=[DEFAULT_RENAME_CATEGORY],
        help="重命名分类列表，可选 image video audio office 或 all",
    )
    parser.add_argument("--data-dir", help="固化数据存储目录（可选）")
    parser.add_argument("--ui", action="store_true", help="启动图形界面（PySide6）")
    parser.add_argument("--verbose", action="store_true", help="输出调试信息")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.ui:
        from md_link_fixer.ui import launch_ui
        launch_ui(args.root)
        return

    root_dir = args.root or get_app_root()
    try:
        run_pipeline(root_dir, args.rename_types, verbose=args.verbose, data_dir=args.data_dir)
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR, format='[%(levelname)s] %(message)s', force=True)
        logging.error(str(exc))
        sys.exit(1)
    if sys.stdin.isatty():
        try:
            input("按回车退出...")
        except EOFError:
            pass


if __name__ == "__main__":
    main()
