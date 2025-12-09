#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
md_link_fixer.py (Final Version with Markdown-matching + Hidden-folder ignore)

功能概要：
    - 自动重命名所有未规范的非 Markdown 附件，使其具备唯一性
      （19 位数字 + 2 位随机数）
    - 自动修复 Markdown 内所有相对路径引用：
        * 针对 markdown → 如果找不到原路径：
            - 先按文件名精确匹配
            - 再按文件名模糊匹配（filename in basename）
            - 匹配多条时跳过（避免误伤）
        * 针对附件（图片 / 视频 / PDF 等）→ 按文件名精确匹配
    - 自动忽略所有以 . 开头的隐藏目录
    - 支持 ![](), [](), <img>, <image src="">
    - 纯标准库，无日志文件输出
    - 程序结束自动删除 JSON 临时映射文件
"""

import os
import sys
import re
import json
import logging
import random
from datetime import datetime
from typing import Dict, List, Tuple

# ---------- 常量 ----------

EXCLUDE_DIR_NAMES = {
    '__pycache__',
    '_MEIPASS', '_MEI', '_internal'
}

MARKDOWN_EXTS = {'.md', '.markdown', '.mdown', '.mkd', '.mkdown'}

MAPPING_FILENAME = 'attachment_rename_map.json'
INDEX_FILENAME = 'file_path_index.json'

# ---------- 基础工具 ----------

def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def setup_logger(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='[%(levelname)s] %(message)s')


def is_markdown_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in MARKDOWN_EXTS


def is_normalized_filename(filename: str) -> bool:
    name, _ = os.path.splitext(filename)
    return name.isdigit() and len(name) == 19


def generate_unique_filename(target_dir: str, ext: str, used: set) -> str:
    while True:
        now = datetime.now()
        base = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
        rand = f"{random.randint(0, 99):02d}"
        new_name = f"{base}{rand}{ext}"

        if new_name in used:
            continue

        abs_new = os.path.join(target_dir, new_name)
        if not os.path.exists(abs_new):
            used.add(new_name)
            return new_name

# ---------- 扫描 + 重命名附件 ----------

def walk_attachments(root_dir: str, self_exec: str):
    result = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # 忽略以 . 开头目录，以及特殊目录和 .app
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith('.')
            and d not in EXCLUDE_DIR_NAMES
            and not d.endswith('.app')
        ]

        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(abs_path, root_dir)

            if os.path.abspath(abs_path) == os.path.abspath(self_exec):
                continue
            if filename.lower().endswith('.exe'):
                continue
            if is_markdown_file(filename):
                continue
            if is_normalized_filename(filename):
                continue

            result.append((abs_path, rel_path))

    return result


def rename_attachments(root_dir: str, self_exec: str):
    attachments = walk_attachments(root_dir, self_exec)
    logging.info(f"检测到未规范附件：{len(attachments)} 个")

    mapping = {}
    used = set()

    for abs_old, rel_old in attachments:
        dirpath, filename = os.path.split(abs_old)
        _, ext = os.path.splitext(filename)

        new_name = generate_unique_filename(dirpath, ext, used)
        abs_new = os.path.join(dirpath, new_name)

        rel_old_posix = rel_old.replace(os.sep, "/")
        rel_new_posix = os.path.relpath(abs_new, root_dir).replace(os.sep, "/")

        logging.info(f"重命名：{rel_old_posix} → {rel_new_posix}")

        try:
            os.rename(abs_old, abs_new)
            mapping[rel_old_posix] = rel_new_posix
        except Exception as e:
            logging.error(f"重命名失败：{rel_old_posix}，错误：{e}")

    return mapping


def save_mapping(root_dir: str, mapping: Dict[str, str]) -> str:
    path = os.path.join(root_dir, MAPPING_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"files": [
            {"old": k, "new": v} for k, v in mapping.items()
        ]}, f, ensure_ascii=False, indent=2)
    return path

# ---------- 文件索引 ----------

def build_file_index(root_dir: str):
    index = {"markdown": [], "attachments": []}

    for dirpath, dirnames, filenames in os.walk(root_dir):

        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".")
            and d not in EXCLUDE_DIR_NAMES
            and not d.endswith(".app")
        ]

        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel_posix = os.path.relpath(abs_path, root_dir).replace(os.sep, "/")

            if is_markdown_file(filename):
                index["markdown"].append(rel_posix)
            else:
                index["attachments"].append(rel_posix)

    logging.info(f"索引完成：Markdown {len(index['markdown'])} 个，附件 {len(index['attachments'])} 个")
    return index


def save_index(root_dir: str, index: Dict[str, List[str]]) -> str:
    path = os.path.join(root_dir, INDEX_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"files": index}, f, ensure_ascii=False, indent=2)
    return path


# ---------- Markdown 解析与替换 ----------

MD_LINK_PATTERN = re.compile(r'(!?\[[^\]]*\]\()([^\)]+)(\))')
HTML_SRC_PATTERN = re.compile(
    r'(<(?:img|image)\b[^>]*?\s(?:src|href)\s*=\s*["\'])([^"\']+)(["\'])',
    re.IGNORECASE
)

def is_external_or_absolute(path: str) -> bool:
    p = path.lower()
    return (
        p.startswith(("http://", "https://", "ftp://", "mailto:", "tel:", "data:"))
        or p.startswith("//")
        or p.startswith("#")
        or path.startswith(("/", "\\"))
        or re.match(r"^[a-zA-Z]:[\\/]", path)
    )


def find_attachment_by_filename(filename: str, attachments: List[str]):
    candidates = [p for p in attachments if os.path.basename(p) == filename]
    return candidates[0] if len(candidates) == 1 else None


def find_markdown_by_filename(filename: str, markdown_paths: List[str]):
    all_mds = [p for p in markdown_paths if p.lower().endswith(".md")]

    # 精确匹配
    exact = [p for p in all_mds if os.path.basename(p) == filename]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    # 模糊匹配（两端模糊）
    lower_name = filename.lower()
    fuzzy = [p for p in all_mds if lower_name in os.path.basename(p).lower()]

    if len(fuzzy) == 1:
        return fuzzy[0]
    return None  # 多个 or 0 个


def transform_path(
    url: str,
    md_dir: str,
    root_dir: str,
    mapping: Dict[str, str],
    markdown_paths: List[str],
    attachment_paths: List[str],
):
    url = url.strip()

    if not url or is_external_or_absolute(url):
        return url

    # 映射修复
    try:
        abs_candidate = os.path.normpath(os.path.join(md_dir, url))
        rel_from_root = os.path.relpath(abs_candidate, root_dir).replace(os.sep, "/")
    except Exception:
        rel_from_root = None

    if rel_from_root and rel_from_root in mapping:
        new_rel = mapping[rel_from_root]
        new_abs = os.path.join(root_dir, new_rel.replace("/", os.sep))
        return os.path.relpath(new_abs, md_dir).replace(os.sep, "/")

    filename = os.path.basename(url)

    # Markdown 文件特殊处理
    if filename.lower().endswith(".md"):
        found_rel = find_markdown_by_filename(filename, markdown_paths)
    else:
        found_rel = find_attachment_by_filename(filename, attachment_paths)

    if found_rel:
        new_abs = os.path.join(root_dir, found_rel.replace("/", os.sep))
        return os.path.relpath(new_abs, md_dir).replace(os.sep, "/")

    return url


def replace_in_markdown(
    content: str,
    md_abs_path: str,
    root_dir: str,
    mapping: Dict[str, str],
    markdown_paths: List[str],
    attachment_paths: List[str]
):
    md_dir = os.path.dirname(md_abs_path)
    replaced_count = 0

    def md_repl(match):
        nonlocal replaced_count
        prefix, body, suffix = match.groups()

        m = re.match(r'^(\s*<?)([^>\s]+)(>?)(.*)$', body, re.DOTALL)
        if not m:
            return match.group(0)

        pre, url, angle, tail = m.groups()
        new_url = transform_path(url, md_dir, root_dir, mapping, markdown_paths, attachment_paths)

        if new_url != url:
            replaced_count += 1

        return f"{prefix}{pre}{new_url}{angle}{tail}{suffix}"

    def html_repl(match):
        nonlocal replaced_count
        prefix, url, suffix = match.groups()

        new_url = transform_path(url, md_dir, root_dir, mapping, markdown_paths, attachment_paths)

        if new_url != url:
            replaced_count += 1

        return f"{prefix}{new_url}{suffix}"

    content = MD_LINK_PATTERN.sub(md_repl, content)
    content = HTML_SRC_PATTERN.sub(html_repl, content)

    return content, replaced_count


def process_markdown_files(root_dir: str, index: Dict[str, List[str]], mapping: Dict[str, str]):
    markdown_paths = index["markdown"]
    attachment_paths = index["attachments"]

    total_files = 0
    total_replacements = 0

    for rel_md in markdown_paths:
        abs_md = os.path.join(root_dir, rel_md.replace("/", os.sep))
        logging.info(f"处理 Markdown：{rel_md}")

        try:
            with open(abs_md, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logging.error(f"读取失败：{rel_md}，{e}")
            continue

        new_content, count = replace_in_markdown(
            content, abs_md, root_dir, mapping, markdown_paths, attachment_paths
        )

        if count == 0:
            continue

        total_files += 1
        total_replacements += count

        with open(abs_md, "w", encoding="utf-8") as f:
            f.write(new_content)

    logging.info(f"Markdown 修复完成：修改 {total_files} 个文件，共 {total_replacements} 处替换")


# ---------- 删除临时文件 ----------

def safe_delete(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
            logging.info(f"已删除临时文件：{path}")
        except Exception as e:
            logging.error(f"删除失败：{path} 错误：{e}")


# ---------- 主程序 ----------

def main():
    root_dir = get_app_root()
    setup_logger()

    logging.info("====================================")
    logging.info(" Markdown 附件重命名 & 路径修复工具 ")
    logging.info(f" 工作根目录：{root_dir}")
    logging.info("====================================")

    random.seed()

    self_exec = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)

    mapping = rename_attachments(root_dir, self_exec)
    mapping_path = save_mapping(root_dir, mapping)

    index = build_file_index(root_dir)
    index_path = save_index(root_dir, index)

    process_markdown_files(root_dir, index, mapping)

    safe_delete(mapping_path)
    safe_delete(index_path)

    logging.info("全部处理完成，可以关闭窗口。")


if __name__ == "__main__":
    main()
