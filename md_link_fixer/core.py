#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Markdown attachment renamer & link fixer core (PySide-ready)

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

import json
import logging
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------- 常量 ----------

EXCLUDE_DIR_NAMES = {
    '__pycache__',
    '_MEIPASS', '_MEI', '_internal'
}

MARKDOWN_EXTS = {'.md', '.markdown', '.mdown', '.mkd', '.mkdown'}

MAPPING_FILENAME = 'attachment_rename_map.json'
INDEX_FILENAME = 'file_path_index.json'

RENAMING_CATEGORIES = {
    "image": {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.tif', '.tiff', '.heic'},
    "video": {'.mp4', '.mov', '.mkv', '.avi', '.wmv', '.flv', '.webm'},
    "audio": {'.mp3', '.wav', '.aac', '.flac', '.m4a', '.ogg'},
    "office": {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.csv', '.wps'},
}

DEFAULT_RENAME_CATEGORY = "image"

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
LOGO_PATH = str(ASSETS_DIR / "app.ico")

CATEGORY_LABELS = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "office": "办公",
    "all": "全部",
}
CATEGORY_ORDER = ["image", "video", "audio", "office"]

def normalize_categories(selected_types: Optional[List[str]]):
    if not selected_types:
        return [DEFAULT_RENAME_CATEGORY]
    lowered = [t.lower() for t in selected_types if t]
    filtered = [t for t in lowered if t in RENAMING_CATEGORIES or t == "all"]
    if "all" in filtered:
        return ["all"]
    ordered = [t for t in CATEGORY_ORDER if t in filtered]
    return ordered or [DEFAULT_RENAME_CATEGORY]

def category_labels(rename_types: Optional[List[str]]):
    normalized = normalize_categories(rename_types)
    if "all" in normalized:
        return [CATEGORY_LABELS["all"]]
    return [CATEGORY_LABELS[t] for t in CATEGORY_ORDER if t in normalized]

def category_label_from_types(rename_types: Optional[List[str]]):
    return "、".join(category_labels(rename_types))

# ---------- 基础工具 ----------

def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # When running from source, point to repository root for assets
    return str(BASE_DIR)

def get_config_dir() -> str:
    """Config storage directory (kept inside app structure, e.g. repo/.data)."""
    return os.path.join(get_app_root(), ".data")


CONFIG_DIR = get_config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "projects.json")
LEGACY_CONFIG_DIR = os.path.join(Path.home(), ".md_link_fixer")
LEGACY_CONFIG_PATH = os.path.join(LEGACY_CONFIG_DIR, "projects.json")


def normalize_display_path(path: str) -> str:
    if not path:
        return path
    try:
        return os.path.normpath(path)
    except Exception:
        return path


def setup_logger(verbose=False, extra_handlers=None):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='[%(levelname)s] %(message)s', force=True)
    if extra_handlers:
        for handler in extra_handlers:
            handler.setLevel(level)
            logging.getLogger().addHandler(handler)


def load_projects_config() -> Tuple[List[Dict], Dict]:
    # Migrate legacy config (home dir) into current config directory.
    if not os.path.exists(CONFIG_PATH) and os.path.exists(LEGACY_CONFIG_PATH):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(legacy, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    if not os.path.exists(CONFIG_PATH):
        return [], {"data_dir": normalize_display_path(os.path.join(get_app_root(), ".data"))}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            projects = data
            settings = {}
        else:
            projects = data.get("projects", [])
            settings = data.get("settings", {}) or {}
            if not isinstance(settings, dict):
                settings = {}
        if not settings.get("data_dir"):
            for proj in projects:
                data_dir = proj.get("data_dir")
                if data_dir:
                    settings["data_dir"] = data_dir
                    break
        if not settings.get("data_dir"):
            settings["data_dir"] = normalize_display_path(os.path.join(get_app_root(), ".data"))
        return projects, settings
    except Exception:
        return [], {}


def save_projects_config(projects: List[Dict], settings: Optional[Dict] = None):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    payload = {"projects": projects, "settings": settings or {}}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def open_path(path: str):
    if not path:
        return
    if os.name == "nt":
        os.startfile(path)
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


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

def resolve_allowed_extensions(categories: Optional[List[str]]):
    normalized = normalize_categories(categories)
    allow_all = "all" in normalized

    unknown = [
        c for c in (categories or [])
        if c and c.lower() not in RENAMING_CATEGORIES and c.lower() not in {"all", "other"}
    ]
    if unknown:
        raise ValueError(f"Unknown rename categories: {', '.join(unknown)}")

    allowed = set()
    for cat in normalized:
        if cat in RENAMING_CATEGORIES:
            allowed |= RENAMING_CATEGORIES[cat]

    label = category_label_from_types(normalized)
    return allowed, allow_all, normalized, label


def walk_attachments(root_dir: str, self_exec: str, allowed_exts: Optional[set], allow_all: bool):
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
            _, ext = os.path.splitext(filename)
            ext = ext.lower()
            if not allow_all and allowed_exts is not None and ext not in allowed_exts:
                continue

            result.append((abs_path, rel_path))

    return result


def rename_attachments(root_dir: str, self_exec: str, allowed_exts: Optional[set], allow_all: bool):
    attachments = walk_attachments(root_dir, self_exec, allowed_exts, allow_all)
    logging.info(f"检测到未规范附件：{len(attachments)} 个")

    mapping = {}
    used = set()
    renamed = 0
    details = []

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
            renamed += 1
            rel_dir = os.path.dirname(rel_old_posix)
            details.append({
                "old": os.path.basename(rel_old_posix),
                "new": os.path.basename(rel_new_posix),
                "path": rel_dir,
            })
        except Exception as e:
            logging.error(f"重命名失败：{rel_old_posix}，错误：{e}")

    return mapping, len(attachments), renamed, details


def save_mapping(root_dir: str, mapping: Dict[str, str], data_dir: Optional[str] = None) -> str:
    target_dir = data_dir or root_dir
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, MAPPING_FILENAME)
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


def save_index(root_dir: str, index: Dict[str, List[str]], data_dir: Optional[str] = None) -> str:
    target_dir = data_dir or root_dir
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, INDEX_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"files": index}, f, ensure_ascii=False, indent=2)
    return path


def detect_duplicate_filenames(index: Dict[str, List[str]]):
    buckets = {}
    for rel in index.get("markdown", []) + index.get("attachments", []):
        name = os.path.basename(rel)
        buckets.setdefault(name, []).append(rel)

    duplicates = {k: v for k, v in buckets.items() if len(v) > 1}
    if not duplicates:
        return {}, "未发现重复命名文件。", []

    lines = ["| 文件名 | 路径 |", "| --- | --- |"]
    dup_list = []
    for name in sorted(duplicates.keys()):
        paths = "<br>".join(sorted(duplicates[name]))
        lines.append(f"| `{name}` | {paths} |")
        for p in sorted(duplicates[name]):
            dup_list.append({"name": name, "path": p})

    return duplicates, "\n".join(lines), dup_list


def write_reports(data_dir: str, summary: Dict):
    data_dir = normalize_display_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, "latest_summary.json")
    md_path = os.path.join(data_dir, "latest_report.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    lines = [
        "# 运行报告",
        "",
        f"- 工作目录：{summary['root']}",
        f"- 重命名分类：{summary['rename_categories']}",
        f"- 重命名候选：{summary['rename_candidates']}，实际重命名：{summary['renamed_files']}",
        f"- Markdown 修复：{summary['markdown_fixed']}，替换次数：{summary['replacements']}",
        f"- 失效引用：{summary.get('invalid_reference_count', 0)}",
        "",
        "## 重复命名文件",
        summary["duplicate_table"],
    ]

    invalid_refs = summary.get("invalid_references") or []
    if invalid_refs:
        lines.extend([
            "",
            "## 失效引用",
            "",
            "| Markdown | 引用路径 |",
            "| --- | --- |",
        ])
        for item in invalid_refs:
            lines.append(f"| `{item.get('file', '')}` | {item.get('link', '')} |")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logging.info(f"报告已写入：{json_path} , {md_path}")


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
        return url, True

    abs_candidate = None
    # 映射修复
    try:
        abs_candidate = os.path.normpath(os.path.join(md_dir, url))
        rel_from_root = os.path.relpath(abs_candidate, root_dir).replace(os.sep, "/")
    except Exception:
        rel_from_root = None

    if rel_from_root and rel_from_root in mapping:
        new_rel = mapping[rel_from_root]
        new_abs = os.path.join(root_dir, new_rel.replace("/", os.sep))
        if os.path.exists(new_abs):
            return os.path.relpath(new_abs, md_dir).replace(os.sep, "/"), True

    filename = os.path.basename(url)

    # Markdown 文件特殊处理
    if filename.lower().endswith(".md"):
        found_rel = find_markdown_by_filename(filename, markdown_paths)
    else:
        found_rel = find_attachment_by_filename(filename, attachment_paths)

    if found_rel:
        new_abs = os.path.join(root_dir, found_rel.replace("/", os.sep))
        if os.path.exists(new_abs):
            return os.path.relpath(new_abs, md_dir).replace(os.sep, "/"), True

    if abs_candidate and os.path.exists(abs_candidate):
        return url, True

    return url, False


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
    broken_links: List[str] = []

    def md_repl(match):
        nonlocal replaced_count
        prefix, body, suffix = match.groups()

        m = re.match(r'^(\s*<?)([^>\s]+)(>?)(.*)$', body, re.DOTALL)
        if not m:
            return match.group(0)

        pre, url, angle, tail = m.groups()
        new_url, resolved = transform_path(url, md_dir, root_dir, mapping, markdown_paths, attachment_paths)

        if new_url != url:
            replaced_count += 1
        if not resolved:
            broken_links.append(url)

        return f"{prefix}{pre}{new_url}{angle}{tail}{suffix}"

    def html_repl(match):
        nonlocal replaced_count
        prefix, url, suffix = match.groups()

        new_url, resolved = transform_path(url, md_dir, root_dir, mapping, markdown_paths, attachment_paths)

        if new_url != url:
            replaced_count += 1
        if not resolved:
            broken_links.append(url)

        return f"{prefix}{new_url}{suffix}"

    content = MD_LINK_PATTERN.sub(md_repl, content)
    content = HTML_SRC_PATTERN.sub(html_repl, content)

    return content, replaced_count, broken_links


def process_markdown_files(root_dir: str, index: Dict[str, List[str]], mapping: Dict[str, str]):
    markdown_paths = index["markdown"]
    attachment_paths = index["attachments"]

    total_files = 0
    total_replacements = 0
    changed_files = []
    invalid_references = []

    for rel_md in markdown_paths:
        abs_md = os.path.join(root_dir, rel_md.replace("/", os.sep))
        logging.info(f"处理 Markdown：{rel_md}")

        try:
            with open(abs_md, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logging.error(f"读取失败：{rel_md}，{e}")
            continue

        new_content, count, broken_links = replace_in_markdown(
            content, abs_md, root_dir, mapping, markdown_paths, attachment_paths
        )

        for link in broken_links:
            invalid_references.append({"file": rel_md, "link": link})

        if count == 0:
            continue

        total_files += 1
        total_replacements += count
        changed_files.append(rel_md)

        with open(abs_md, "w", encoding="utf-8") as f:
            f.write(new_content)

    logging.info(f"Markdown 修复完成：修改 {total_files} 个文件，共 {total_replacements} 处替换")
    return total_files, total_replacements, changed_files, invalid_references


# ---------- 删除临时文件 ----------

def safe_delete(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
            logging.info(f"已删除临时文件：{path}")
        except Exception as e:
            logging.error(f"删除失败：{path} 错误：{e}")


# ---------- 主程序 ----------

def run_pipeline(root_dir: str, rename_categories: Optional[List[str]], verbose=False, extra_handlers=None, data_dir: Optional[str] = None):
    root_dir = normalize_display_path(os.path.abspath(root_dir))
    allowed_exts, allow_all, normalized_types, category_label = resolve_allowed_extensions(rename_categories)

    setup_logger(verbose, extra_handlers)

    logging.info("====================================")
    logging.info(" Markdown路径修复工具 ")
    logging.info(f" 工作根目录：{root_dir}")
    logging.info(f" 重命名分类：{category_label}")
    logging.info("====================================")

    random.seed()

    self_exec = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)

    # Duplicate report should reflect the state BEFORE any renaming.
    index_before = build_file_index(root_dir)
    duplicates, duplicate_table, duplicate_list = detect_duplicate_filenames(index_before)

    mapping, detected, renamed, rename_details = rename_attachments(root_dir, self_exec, allowed_exts, allow_all)
    mapping_path = save_mapping(root_dir, mapping, data_dir)

    index_after = build_file_index(root_dir)
    index_path = save_index(root_dir, index_after, data_dir)

    md_files, replacements, changed_files, invalid_refs = process_markdown_files(root_dir, index_after, mapping)

    safe_delete(mapping_path)
    safe_delete(index_path)

    logging.info("------ 重复命名检查 ------")
    logging.info(duplicate_table)

    logging.info("------ 运行结果 ------")
    logging.info(f"重命名候选：{detected} | 实际重命名：{renamed}")
    logging.info(f"Markdown 修复：{md_files} | 替换次数：{replacements}")
    logging.info(f"失效引用：{len(invalid_refs)}")

    summary = {
        "root": root_dir,
        "rename_candidates": detected,
        "renamed_files": renamed,
        "markdown_fixed": md_files,
        "replacements": replacements,
        "rename_categories": category_label,
        "rename_category_keys": normalized_types,
        "duplicates": duplicates,
        "duplicate_table": duplicate_table,
        "rename_details": rename_details,
        "fixed_files": changed_files,
        "duplicate_list": duplicate_list,
        "invalid_references": invalid_refs,
        "invalid_reference_count": len(invalid_refs),
    }

    if data_dir:
        write_reports(data_dir, summary)

    logging.info("全部处理完成。")
    return summary
