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

import argparse
import json
import logging
import os
import random
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import shutil

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
ALL_KNOWN_EXTS = set().union(*RENAMING_CATEGORIES.values())

CONFIG_DIR = os.path.join(Path.home(), ".md_link_fixer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "projects.json")
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")

CATEGORY_LABELS = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "office": "办公",
    "other": "其他",
    "all": "全部",
}
CATEGORY_LABEL_TO_KEY = {v: k for k, v in CATEGORY_LABELS.items()}

# ---------- 基础工具 ----------

def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def setup_logger(verbose=False, extra_handlers=None):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='[%(levelname)s] %(message)s', force=True)
    if extra_handlers:
        for handler in extra_handlers:
            handler.setLevel(level)
            logging.getLogger().addHandler(handler)


def load_projects_config() -> Tuple[List[Dict], Dict]:
    if not os.path.exists(CONFIG_PATH):
        return [], {}
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
        if "data_dir" not in settings:
            for proj in projects:
                data_dir = proj.get("data_dir")
                if data_dir:
                    settings["data_dir"] = data_dir
                    break
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
    if not categories:
        categories = [DEFAULT_RENAME_CATEGORY]

    normalized = [c.lower() for c in categories]
    allow_all = "all" in normalized
    allow_other = "other" in normalized

    unknown = [c for c in normalized if c not in RENAMING_CATEGORIES and c not in {"all", "other"}]
    if unknown:
        raise ValueError(f"Unknown rename categories: {', '.join(unknown)}")

    allowed = set()
    for cat in normalized:
        if cat in RENAMING_CATEGORIES:
            allowed |= RENAMING_CATEGORIES[cat]

    label = ", ".join(normalized)
    return allowed, allow_other, allow_all, label


def walk_attachments(root_dir: str, self_exec: str, allowed_exts: Optional[set], allow_other: bool, allow_all: bool):
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
            if not allow_all:
                if allowed_exts is not None and ext in allowed_exts:
                    pass
                elif allow_other and ext not in ALL_KNOWN_EXTS:
                    pass
                else:
                    continue

            result.append((abs_path, rel_path))

    return result


def rename_attachments(root_dir: str, self_exec: str, allowed_exts: Optional[set], allow_other: bool, allow_all: bool):
    attachments = walk_attachments(root_dir, self_exec, allowed_exts, allow_other, allow_all)
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
        "",
        "## 重复命名文件",
        summary["duplicate_table"],
    ]
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
        if os.path.exists(new_abs):
            return os.path.relpath(new_abs, md_dir).replace(os.sep, "/")

    filename = os.path.basename(url)

    # Markdown 文件特殊处理
    if filename.lower().endswith(".md"):
        found_rel = find_markdown_by_filename(filename, markdown_paths)
    else:
        found_rel = find_attachment_by_filename(filename, attachment_paths)

    if found_rel:
        new_abs = os.path.join(root_dir, found_rel.replace("/", os.sep))
        if os.path.exists(new_abs):
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
    changed_files = []

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
        changed_files.append(rel_md)

        with open(abs_md, "w", encoding="utf-8") as f:
            f.write(new_content)

    logging.info(f"Markdown 修复完成：修改 {total_files} 个文件，共 {total_replacements} 处替换")
    return total_files, total_replacements, changed_files


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
    root_dir = os.path.abspath(root_dir)
    allowed_exts, allow_other, allow_all, category_label = resolve_allowed_extensions(rename_categories)

    setup_logger(verbose, extra_handlers)

    logging.info("====================================")
    logging.info(" Markdown 附件重命名 & 路径修复工具 ")
    logging.info(f" 工作根目录：{root_dir}")
    logging.info(f" 重命名分类：{category_label}")
    logging.info("====================================")

    random.seed()

    self_exec = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)

    mapping, detected, renamed, rename_details = rename_attachments(root_dir, self_exec, allowed_exts, allow_other, allow_all)
    mapping_path = save_mapping(root_dir, mapping, data_dir)

    index = build_file_index(root_dir)
    index_path = save_index(root_dir, index, data_dir)

    md_files, replacements, changed_files = process_markdown_files(root_dir, index, mapping)
    duplicates, duplicate_table, duplicate_list = detect_duplicate_filenames(index)

    safe_delete(mapping_path)
    safe_delete(index_path)

    logging.info("------ 重复命名检查 ------")
    logging.info(duplicate_table)

    logging.info("------ 运行结果 ------")
    logging.info(f"重命名候选：{detected} | 实际重命名：{renamed}")
    logging.info(f"Markdown 修复：{md_files} | 替换次数：{replacements}")

    summary = {
        "root": root_dir,
        "rename_candidates": detected,
        "renamed_files": renamed,
        "markdown_fixed": md_files,
        "replacements": replacements,
        "rename_categories": category_label,
        "duplicates": duplicates,
        "duplicate_table": duplicate_table,
        "rename_details": rename_details,
        "fixed_files": changed_files,
        "duplicate_list": duplicate_list,
    }

    if data_dir:
        write_reports(data_dir, summary)

    logging.info("全部处理完成。")
    return summary


def parse_args():
    parser = argparse.ArgumentParser(description="Markdown 附件重命名 & 路径修复工具")
    parser.add_argument("--root", help="指定工作根目录（默认使用脚本所在目录）")
    parser.add_argument(
        "--rename-types",
        nargs="+",
        default=[DEFAULT_RENAME_CATEGORY],
        help="重命名分类列表，可选 image video audio office other 或 all",
    )
    parser.add_argument("--data-dir", help="固化数据存储目录（可选）")
    parser.add_argument("--ui", action="store_true", help="启动图形界面")
    parser.add_argument("--verbose", action="store_true", help="输出调试信息")
    return parser.parse_args()


def main_cli(args):
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


def launch_ui(default_root: Optional[str] = None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    PROJECT_CATEGORIES = list(RENAMING_CATEGORIES.keys()) + ["other"]
    CATEGORY_OPTIONS = [CATEGORY_LABELS[c] for c in PROJECT_CATEGORIES] + [CATEGORY_LABELS["all"]]
    projects, settings = load_projects_config()
    app = tk.Tk()
    app.title("Markdown 附件重命名 & 路径修复工具")
    app.configure(bg="#f4f5f7")
    try:
        app.iconbitmap(LOGO_PATH)
    except Exception:
        pass
    try:
        import tkinter.font as tkfont
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=10)
    except Exception:
        pass

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Accent.TButton", padding=(12, 8), foreground="#ffffff", background="#3b82f6", borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#2563eb")])
    style.configure("Card.TFrame", background="#ffffff", relief="flat")
    style.configure("Icon.TButton", padding=6, foreground="#1f2937", background="#e5edff", borderwidth=0)
    style.map("Icon.TButton", background=[("active", "#d8e3ff")])
    style.configure("TButton", padding=(10, 6), background="#ffffff", foreground="#1f2937")
    style.configure("Custom.TFrame", background="#f4f5f7")
    style.configure("TLabel", background="#f4f5f7", foreground="#1f2937")
    style.configure("TLabelframe", background="#f4f5f7", foreground="#1f2937")
    style.configure("TEntry", fieldbackground="#ffffff", foreground="#1f2937")
    style.configure("TCombobox", fieldbackground="#ffffff", foreground="#1f2937")
    style.configure("Treeview", background="#ffffff", foreground="#1f2937", fieldbackground="#ffffff", bordercolor="#d7dce4")
    style.configure("Treeview.Heading", background="#e8edf5", foreground="#1f2937")
    style.configure("TNotebook", background="#f4f5f7")
    style.configure("TNotebook.Tab", padding=(10, 6))

    colors = {
        "bg": "#f4f5f7",
        "card": "#ffffff",
        "hover": "#eef2f6",
        "selected": "#e3eaf5",
        "danger": "#fff2f0",
        "text": "#1f2937",
        "muted": "#6b7280",
        "accent": "#3b82f6",
        "border": "#d7dce4",
    }

    icons = {
        "run": "▶",
        "open": "📂",
        "info": "ℹ",
        "delete": "🗑",
        "settings": "⚙",
        "add": "＋",
    }

    log_view = {"widget": None}
    log_buffer: List[str] = []

    def append_log(line: str):
        if not line:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{stamp}] {line}"
        log_buffer.append(formatted)

        def _write():
            widget = log_view["widget"]
            if not widget:
                return
            widget.configure(state="normal")
            for msg in log_buffer[:]:
                widget.insert("end", msg + "\n")
            log_buffer.clear()
            widget.see("end")
            widget.configure(state="disabled")

        app.after(0, _write)

    def clear_log():
        widget = log_view["widget"]
        if not widget:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")
        log_buffer.clear()

    class UILogHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            append_log(msg)

    state = {"projects": projects, "selected": 0, "settings": settings or {}}
    state["settings"].setdefault("data_dir", "")
    running = {"flag": False}
    logo_img = None
    if os.path.exists(LOGO_PATH):
        try:
            logo_img = tk.PhotoImage(file=LOGO_PATH)
        except Exception:
            logo_img = None

    def persist_projects():
        for proj in state["projects"]:
            proj.pop("data_dir", None)
        save_projects_config(state["projects"], state["settings"])

    def ensure_selection():
        if not state["projects"]:
            state["selected"] = 0
        else:
            state["selected"] = max(0, min(state["selected"], len(state["projects"]) - 1))

    def add_project(root_val: str, name_val: str):
        project = {
            "name": name_val.strip() or Path(root_val).name,
            "root": root_val,
            "rename_types": [DEFAULT_RENAME_CATEGORY],
        }
        state["projects"].append(project)
        state["selected"] = len(state["projects"]) - 1
        persist_projects()

    # ---------- Onboarding ----------
    def show_onboarding():
        for child in app.winfo_children():
            child.destroy()
        frame = ttk.Frame(app, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        app.columnconfigure(0, weight=1)
        app.rowconfigure(0, weight=1)

        data_path = tk.StringVar(value=state["settings"].get("data_dir") or "")
        root_path = tk.StringVar(value=default_root or get_app_root())
        name_var = tk.StringVar(value="")

        ttk.Label(frame, text="首次使用 - 系统设置与项目", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(frame, text="数据存储位置（系统设置，必填）：").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=data_path, width=60).grid(row=1, column=1, sticky="we", padx=(4, 4))
        ttk.Button(frame, text="选择…", command=lambda: data_path.set(filedialog.askdirectory(initialdir=data_path.get() or get_app_root()) or data_path.get())).grid(row=1, column=2, sticky="e")

        ttk.Label(frame, text="文档项目位置（必填）：").grid(row=2, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=root_path, width=60).grid(row=2, column=1, sticky="we", padx=(4, 4))
        ttk.Button(frame, text="选择…", command=lambda: root_path.set(filedialog.askdirectory(initialdir=root_path.get() or get_app_root()) or root_path.get())).grid(row=2, column=2, sticky="e")

        ttk.Label(frame, text="自定义名称（可选）：").grid(row=3, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=name_var, width=60).grid(row=3, column=1, sticky="we", padx=(4, 4))

        def submit():
            root_val = root_path.get().strip()
            data_val = data_path.get().strip()
            if not root_val or not data_val:
                messagebox.showerror("缺少信息", "请填写文档项目位置和数据存储位置。")
                return
            os.makedirs(data_val, exist_ok=True)
            state["settings"]["data_dir"] = data_val
            add_project(root_val, name_var.get())
            build_main_ui()

        ttk.Button(frame, text="提交并进入", command=submit).grid(row=4, column=0, columnspan=3, pady=(12, 0))
        frame.columnconfigure(1, weight=1)

    # ---------- Main UI ----------
    def build_main_ui():
        ensure_selection()
        if not state["projects"]:
            return show_onboarding()

        for child in app.winfo_children():
            child.destroy()

        wrapper = tk.Frame(app, bg=colors["bg"])
        wrapper.grid(row=0, column=0, sticky="nsew")
        app.columnconfigure(0, weight=1)
        app.rowconfigure(0, weight=1)

        header = tk.Frame(
            wrapper,
            bg=colors["selected"],
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=colors["border"],
            highlightcolor=colors["border"],
        )
        header.grid(row=0, column=0, columnspan=2, sticky="we")
        if logo_img:
            tk.Label(header, image=logo_img, bg=colors["selected"]).pack(side="left")
        tk.Label(header, text="文档项目管理", bg=colors["selected"], fg=colors["text"], font=("Segoe UI", 14, "bold")).pack(side="left", padx=(8, 0))

        content = tk.Frame(wrapper, bg=colors["bg"])
        content.grid(row=1, column=0, sticky="nsew")
        wrapper.rowconfigure(1, weight=1)
        wrapper.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # Project list
        left = tk.Frame(content, bg=colors["bg"], padx=8, pady=6)
        left.grid(row=0, column=0, sticky="nswe")
        tk.Label(left, text="项目文档列表", bg=colors["bg"], fg=colors["text"], font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

        canvas = tk.Canvas(left, bg=colors["bg"], highlightthickness=0, height=360)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas, bg=colors["bg"])
        canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def refresh_scroll(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        list_frame.bind("<Configure>", refresh_scroll)

        # Detail + log
        right = tk.Frame(content, bg=colors["bg"], padx=10, pady=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        project_name = tk.StringVar()
        root_path = tk.StringVar()
        category_var = tk.StringVar(value=CATEGORY_LABELS[DEFAULT_RENAME_CATEGORY])
        status_var = tk.StringVar(value="等待运行...")
        state["summary"] = None

        def category_label_from_types(rename_types: Optional[List[str]]):
            if rename_types and "all" in rename_types:
                return CATEGORY_LABELS["all"]
            key = rename_types[0] if rename_types else DEFAULT_RENAME_CATEGORY
            return CATEGORY_LABELS.get(key, CATEGORY_LABELS[DEFAULT_RENAME_CATEGORY])

        def normalize_category_label(label: str):
            key = CATEGORY_LABEL_TO_KEY.get(label)
            return key or DEFAULT_RENAME_CATEGORY

        def get_active_data_dir():
            data_dir = (state["settings"].get("data_dir") or "").strip()
            if data_dir:
                return data_dir
            for proj in state["projects"]:
                candidate = (proj.get("data_dir") or "").strip()
                if candidate:
                    return candidate
            return ""

        def update_project_from_fields():
            persist_projects()

        def set_fields_from_project(index: int):
            proj = state["projects"][index]
            project_name.set(proj.get("name") or Path(proj["root"]).name)
            root_path.set(proj["root"])
            rename_types = proj.get("rename_types", [DEFAULT_RENAME_CATEGORY])
            category_var.set(category_label_from_types(rename_types))
            render_tags(category_var.get())

        def make_link_label(parent, text, target_path):
            lbl = tk.Label(parent, text=text, bg=colors["card"], fg=colors["accent"], cursor="hand2")
            lbl.bind("<Button-1>", lambda e, p=target_path: open_path(p))
            return lbl

        def move_project_data(old_dir: str, new_dir: str):
            if not old_dir or not os.path.exists(old_dir):
                return
            if os.path.abspath(old_dir) == os.path.abspath(new_dir):
                return
            for item in os.listdir(old_dir):
                src = os.path.join(old_dir, item)
                dst = os.path.join(new_dir, item)
                try:
                    shutil.move(src, dst)
                except Exception as exc:
                    append_log(f"[WARN] 移动 {src} 到 {dst} 失败：{exc}")
            try:
                if not os.listdir(old_dir):
                    os.rmdir(old_dir)
            except Exception:
                pass

        def center_window(win: tk.Toplevel):
            win.update_idletasks()
            w = win.winfo_width() or win.winfo_reqwidth()
            h = win.winfo_height() or win.winfo_reqheight()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = (sw // 2) - (w // 2)
            y = (sh // 2) - (h // 2)
            win.geometry(f"+{x}+{y}")

        def show_settings():
            dialog = tk.Toplevel(app)
            dialog.title("系统设置")
            dialog.grab_set()

            data_var = tk.StringVar(value=state["settings"].get("data_dir", ""))

            ttk.Label(dialog, text="数据存储位置：").grid(row=0, column=0, sticky="w", pady=(8, 2))
            ttk.Entry(dialog, textvariable=data_var, width=50).grid(row=0, column=1, sticky="we", padx=(4, 4))

            def choose_dir():
                chosen = filedialog.askdirectory(initialdir=data_var.get() or get_app_root()) or data_var.get()
                if chosen:
                    data_var.set(chosen)

            def save_settings():
                new_dir = data_var.get().strip()
                if not new_dir:
                    messagebox.showerror("缺少信息", "请先选择数据存储位置。")
                    return
                old_dir = (state["settings"].get("data_dir") or "").strip()
                if old_dir and os.path.abspath(old_dir) != os.path.abspath(new_dir):
                    if messagebox.askyesno("迁移数据", f"确认将数据从\n{old_dir}\n移动到\n{new_dir}\n吗？"):
                        os.makedirs(new_dir, exist_ok=True)
                        move_project_data(old_dir, new_dir)
                else:
                    os.makedirs(new_dir, exist_ok=True)
                state["settings"]["data_dir"] = new_dir
                persist_projects()
                dialog.destroy()

            ttk.Button(dialog, text="选择…", command=choose_dir).grid(row=0, column=2, sticky="e")
            btns = ttk.Frame(dialog)
            btns.grid(row=1, column=0, columnspan=3, sticky="e", pady=(10, 4))
            ttk.Button(btns, text="保存", command=save_settings).pack(side="left", padx=4)
            ttk.Button(btns, text="关闭", command=dialog.destroy).pack(side="left", padx=4)
            dialog.columnconfigure(1, weight=1)
            center_window(dialog)

        def show_add_project():
            dialog = tk.Toplevel(app)
            dialog.title("新增项目")
            dialog.grab_set()

            name_var = tk.StringVar(value="")
            root_var = tk.StringVar(value=get_app_root())

            ttk.Label(dialog, text="文档项目位置：").grid(row=0, column=0, sticky="w", pady=(8, 2))
            ttk.Entry(dialog, textvariable=root_var, width=50).grid(row=0, column=1, sticky="we", padx=(4, 4))
            ttk.Button(dialog, text="选择…", command=lambda: root_var.set(filedialog.askdirectory(initialdir=root_var.get() or get_app_root()) or root_var.get())).grid(row=0, column=2, sticky="e")

            ttk.Label(dialog, text="自定义名称（可选）：").grid(row=1, column=0, sticky="w", pady=(8, 2))
            ttk.Entry(dialog, textvariable=name_var, width=50).grid(row=1, column=1, sticky="we", padx=(4, 4))

            def submit_new():
                data_dir = get_active_data_dir()
                if not data_dir:
                    messagebox.showerror("缺少信息", "请先在系统设置中设置数据存储位置。")
                    return
                root_val = root_var.get().strip()
                if not root_val:
                    messagebox.showerror("缺少信息", "请填写文档项目位置。")
                    return
                add_project(root_val, name_var.get())
                ensure_selection()
                set_fields_from_project(state["selected"])
                render_project_list()
                dialog.destroy()

            btns = ttk.Frame(dialog)
            btns.grid(row=2, column=0, columnspan=3, sticky="e", pady=(10, 4))
            ttk.Button(btns, text="保存", command=submit_new).pack(side="left", padx=4)
            ttk.Button(btns, text="取消", command=dialog.destroy).pack(side="left", padx=4)
            dialog.columnconfigure(1, weight=1)
            center_window(dialog)

        ttk.Button(header, text=f"{icons['settings']} 系统设置", style="Accent.TButton", command=show_settings).pack(side="right")
        ttk.Button(header, text=f"{icons['add']} 新增项目", style="Accent.TButton", command=show_add_project).pack(side="right", padx=(0, 6))

        def select_project(idx: int):
            state["selected"] = idx
            set_fields_from_project(idx)
            render_project_list()

        def run_project(idx: Optional[int] = None):
            if running["flag"]:
                return
            if idx is None:
                idx = state["selected"]
            if idx >= len(state["projects"]):
                return
            proj = state["projects"][idx]
            data_dir = get_active_data_dir()
            if not proj.get("root") or not data_dir:
                messagebox.showerror("缺少信息", "请先在系统设置中填写数据存储位置，并提供文档项目位置。")
                return
            if not messagebox.askyesno("执行确认", "确定执行重命名检查和链接修复吗？"):
                return
            running["flag"] = True
            os.makedirs(data_dir, exist_ok=True)
            status_var.set("运行中，请稍候...")
            clear_log()
            append_log("开始执行：扫描附件、重命名和修复 Markdown 链接。")

            def worker():
                try:
                    handler = UILogHandler()
                    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
                    summary = run_pipeline(
                        proj["root"],
                        proj.get("rename_types") or [DEFAULT_RENAME_CATEGORY],
                        verbose=False,
                        data_dir=data_dir,
                        extra_handlers=[handler],
                    )
                    state["summary"] = summary
                    msg = (
                        f"重命名 {summary['renamed_files']}/{summary['rename_candidates']}；"
                        f"Markdown 修复 {summary['markdown_fixed']}，替换 {summary['replacements']} 处。"
                    )
                    app.after(0, lambda: (status_var.set("完成"), render_summary(summary)))
                    app.after(0, lambda: messagebox.showinfo("完成", msg))
                    append_log("任务完成。")
                except Exception as exc:
                    append_log(f"[ERROR] 运行失败：{exc}")
                    app.after(0, lambda: (status_var.set("运行失败"), messagebox.showerror("运行失败", str(exc))))
                finally:
                    running["flag"] = False

            threading.Thread(target=worker, daemon=True).start()

        def remove_project(idx: int):
            if idx >= len(state["projects"]):
                return
            if not messagebox.askyesno("删除项目", "确定要删除该项目吗？不会删除文件，只会移除列表。"):
                return
            state["projects"].pop(idx)
            ensure_selection()
            persist_projects()
            build_main_ui()

        def show_details(idx: int):
            if idx >= len(state["projects"]):
                return
            proj = state["projects"][idx]
            dialog = tk.Toplevel(app)
            dialog.title("项目详情")
            dialog.grab_set()

            name_var = tk.StringVar(value=proj.get("name") or Path(proj.get("root", "")).name)
            category_var_dlg = tk.StringVar(value=category_label_from_types(proj.get("rename_types", [DEFAULT_RENAME_CATEGORY])))

            ttk.Label(dialog, text="名称：").grid(row=0, column=0, sticky="w", pady=(8, 2))
            ttk.Entry(dialog, textvariable=name_var, width=50).grid(row=0, column=1, sticky="we", padx=(4, 4))

            ttk.Label(dialog, text="文档项目位置：").grid(row=1, column=0, sticky="w", pady=(6, 2))
            ttk.Label(dialog, text=proj.get("root", ""), foreground=colors["muted"]).grid(row=1, column=1, sticky="w")

            ttk.Label(dialog, text="重命名分类：").grid(row=2, column=0, sticky="w", pady=(8, 2))
            dlg_combo = ttk.Combobox(dialog, state="readonly", values=CATEGORY_OPTIONS, textvariable=category_var_dlg)
            dlg_combo.grid(row=2, column=1, sticky="we", padx=(4, 4))

            def confirm_update():
                if not messagebox.askyesno("确认修改", "确认修改名称和分类吗？"):
                    return
                proj["name"] = name_var.get().strip() or Path(proj.get("root", "")).name
                cat_key = normalize_category_label(category_var_dlg.get())
                proj["rename_types"] = ["all"] if cat_key == "all" else [cat_key]
                proj.pop("data_dir", None)
                persist_projects()
                set_fields_from_project(idx)
                render_project_list()
                dialog.destroy()

            btn_frame = ttk.Frame(dialog)
            btn_frame.grid(row=3, column=0, columnspan=3, pady=(10, 4), sticky="e")
            ttk.Button(btn_frame, text="保存", command=confirm_update).pack(side="left", padx=4)
            ttk.Button(btn_frame, text="关闭", command=dialog.destroy).pack(side="left", padx=4)

            dialog.columnconfigure(1, weight=1)
            center_window(dialog)

        def render_project_list():
            for child in list_frame.winfo_children():
                child.destroy()

            for i, proj in enumerate(state["projects"]):
                exists = os.path.exists(proj.get("root", ""))
                is_selected = i == state["selected"]
                bg = colors["selected"] if is_selected else colors["card"]
                missing_bg = colors["danger"] if not exists else bg
                category_label = category_label_from_types(proj.get("rename_types", [DEFAULT_RENAME_CATEGORY]))

                card = tk.Frame(
                    list_frame,
                    bg=missing_bg,
                    bd=0,
                    relief="solid",
                    highlightthickness=1,
                    highlightbackground=colors["border"],
                    highlightcolor=colors["border"],
                    padx=10,
                    pady=6,
                )
                card.pack(fill="x", pady=4)

                top_row = tk.Frame(card, bg=missing_bg)
                top_row.pack(fill="x")
                name = proj.get("name") or Path(proj.get("root", "")).name
                tk.Label(top_row, text=name, bg=missing_bg, fg=colors["text"], font=("Segoe UI", 10, "bold")).pack(side="left")
                status_txt = "路径缺失" if not exists else "正常"
                status_fg = "#c53030" if not exists else colors["muted"]
                tk.Label(top_row, text=status_txt, bg=missing_bg, fg=status_fg, font=("Segoe UI", 9)).pack(side="right")

                root_display = proj.get("root", "")
                path_row = tk.Frame(card, bg=missing_bg)
                path_row.pack(fill="x")
                tk.Label(path_row, text="位置：", bg=missing_bg, fg=colors["muted"]).pack(side="left")
                link_root = tk.Label(
                    path_row,
                    text=root_display or "未配置",
                    bg=missing_bg,
                    fg=colors["accent"],
                    anchor="w",
                    cursor="hand2",
                    wraplength=420,
                    justify="left",
                )
                link_root.pack(side="left", fill="x", expand=True)
                link_root.bind("<Button-1>", lambda e, idx=i: show_details(idx))

                tk.Label(card, text=f"分类: {category_label}", bg=missing_bg, fg=colors["muted"], anchor="w").pack(fill="x")

                actions = tk.Frame(card, bg=missing_bg)
                view_btn = ttk.Button(actions, text=icons["info"], style="Icon.TButton", command=lambda idx=i: show_details(idx))
                run_btn = ttk.Button(actions, text=icons["run"], style="Icon.TButton", command=lambda idx=i: run_project(idx))
                open_btn = ttk.Button(actions, text=icons["open"], style="Icon.TButton", command=lambda p=proj.get("root", ""): open_path(p))
                delete_btn = ttk.Button(actions, text=icons["delete"], style="Icon.TButton", command=lambda idx=i: remove_project(idx))
                view_btn.pack(side="left", padx=2)
                run_btn.pack(side="left", padx=2)
                open_btn.pack(side="left", padx=2)
                delete_btn.pack(side="left", padx=2)
                actions.pack(side="right", anchor="e", pady=(4, 0))
                actions.pack_forget()

                def on_enter(e, c=card, a=actions):
                    c.configure(bg=colors["hover"])
                    for child in c.winfo_children():
                        child.configure(bg=colors["hover"])
                    a.pack(side="right", anchor="e", pady=(4, 0))

                def on_leave(e, c=card, a=actions, sel=is_selected, miss=not exists):
                    bg_color = colors["selected"] if sel else (colors["danger"] if miss else colors["card"])
                    c.configure(bg=bg_color)
                    for child in c.winfo_children():
                        child.configure(bg=bg_color)
                    if not sel:
                        a.pack_forget()

                card.bind("<Enter>", on_enter)
                card.bind("<Leave>", on_leave)
                card.bind("<Button-1>", lambda e, idx=i: select_project(idx), add="+")
                for child in card.winfo_children():
                    child.bind("<Button-1>", lambda e, idx=i: select_project(idx), add="+")

                if is_selected:
                    actions.pack(side="right", anchor="e", pady=(4, 0))

        # Detail form
        tk.Label(right, text="项目详情", bg=colors["bg"], fg=colors["text"], font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(right, text="名称：").grid(row=1, column=0, sticky="w", pady=(6, 2))
        ttk.Label(right, textvariable=project_name).grid(row=1, column=1, columnspan=2, sticky="w", padx=(4, 4))

        ttk.Label(right, text="位置：").grid(row=2, column=0, sticky="w", pady=(6, 2))
        ttk.Label(right, textvariable=root_path, foreground=colors["muted"]).grid(row=2, column=1, columnspan=2, sticky="w", padx=(4, 4))

        ttk.Label(right, text="分类：").grid(row=3, column=0, sticky="w", pady=(6, 2))
        tags_frame = tk.Frame(right, bg=colors["bg"])
        tags_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=(4, 4))

        def render_tags(label_text: str):
            for child in tags_frame.winfo_children():
                child.destroy()
            tag = tk.Label(tags_frame, text=label_text, bg=colors["selected"], fg=colors["text"], padx=6, pady=2, bd=0, relief="flat")
            tag.pack(side="left", padx=(0, 6))

        render_tags(category_var.get())

        ttk.Label(right, textvariable=status_var, foreground=colors["accent"]).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 2))

        notebook = ttk.Notebook(right)
        notebook.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        right.rowconfigure(5, weight=1)

        summary_tab = tk.Frame(notebook, bg=colors["bg"])
        log_tab = tk.Frame(notebook, bg=colors["bg"])
        notebook.add(summary_tab, text="运行摘要")
        notebook.add(log_tab, text="运行日志")

        summary_frame = tk.Frame(summary_tab, bg=colors["bg"])
        summary_frame.pack(fill="both", expand=True)

        log_text = tk.Text(
            log_tab,
            bg=colors["card"],
            fg=colors["text"],
            wrap="word",
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"],
        )
        log_text.configure(state="disabled")
        log_scroll = ttk.Scrollbar(log_tab, command=log_text.yview)
        log_text.configure(yscrollcommand=log_scroll.set)
        log_text.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=(4, 4))
        log_scroll.pack(side="right", fill="y", pady=(4, 4))
        log_view["widget"] = log_text
        if log_buffer:
            log_text.configure(state="normal")
            for msg in log_buffer[:]:
                log_text.insert("end", msg + "\n")
            log_text.see("end")
            log_text.configure(state="disabled")
            log_buffer.clear()

        def render_table(parent, title: str, columns, rows):
            section = tk.Frame(parent, bg=colors["bg"], pady=4)
            section.pack(fill="both", expand=True, pady=4)
            tk.Label(section, text=title, bg=colors["bg"], fg=colors["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 2))
            tree = ttk.Treeview(section, columns=columns, show="headings", height=min(8, len(rows) + 1))
            for col, width in columns:
                tree.heading(col, text=col)
                tree.column(col, width=width, anchor="w")
            for row in rows:
                tree.insert("", "end", values=row)
            vsb = ttk.Scrollbar(section, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

        def render_summary(summary: Optional[Dict]):
            for child in summary_frame.winfo_children():
                child.destroy()
            if not summary:
                tk.Label(summary_frame, text="运行结果将以表格展示", bg=colors["bg"], fg=colors["muted"]).pack(anchor="w", padx=4, pady=4)
                return

            rename_rows = [
                ("重命名", item["old"], item["new"], item.get("path", ""))
                for item in summary.get("rename_details", [])
            ]
            fixed_rows = [
                ("修正信息", os.path.basename(p), p)
                for p in summary.get("fixed_files", [])
            ]
            dup_rows = [
                ("重名文件", item["name"], item["path"])
                for item in summary.get("duplicate_list", [])
            ]
            stats_rows = [(
                "统计信息",
                summary.get("renamed_files", 0),
                summary.get("markdown_fixed", 0),
                len(summary.get("duplicate_list", [])),
            )]

            render_table(summary_frame, "重命名表格", [("信息类型", 80), ("修改前", 180), ("修改后", 200), ("路径", 220)], rename_rows)
            render_table(summary_frame, "修正表格", [("信息类型", 80), ("文件", 200), ("路径", 260)], fixed_rows)
            render_table(summary_frame, "重名文件表格", [("信息类型", 80), ("文件", 200), ("路径", 260)], dup_rows)
            render_table(summary_frame, "统计表格", [("信息类型", 100), ("重命名数量", 120), ("修正文件数量", 140), ("重名文件数量", 140)], stats_rows)

        # Populate fields and list
        set_fields_from_project(state["selected"])
        render_project_list()
        render_summary(state.get("summary"))

    build_main_ui()
    app.mainloop()


def main():
    args = parse_args()
    if args.ui:
        launch_ui(args.root)
    else:
        main_cli(args)


if __name__ == "__main__":
    main()
