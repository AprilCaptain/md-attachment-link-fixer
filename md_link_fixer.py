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
    "office": {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.csv'},
}

DEFAULT_RENAME_CATEGORY = "image"
ALL_KNOWN_EXTS = set().union(*RENAMING_CATEGORIES.values())

CONFIG_DIR = os.path.join(Path.home(), ".md_link_fixer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "projects.json")
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")

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


def load_projects_config() -> List[Dict]:
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("projects", [])
    except Exception:
        return []


def save_projects_config(projects: List[Dict]):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"projects": projects}, f, ensure_ascii=False, indent=2)


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
        except Exception as e:
            logging.error(f"重命名失败：{rel_old_posix}，错误：{e}")

    return mapping, len(attachments), renamed


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
        return {}, "未发现重复命名文件。"

    lines = ["| 文件名 | 路径 |", "| --- | --- |"]
    for name in sorted(duplicates.keys()):
        paths = "<br>".join(sorted(duplicates[name]))
        lines.append(f"| `{name}` | {paths} |")

    return duplicates, "\n".join(lines)


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
    return total_files, total_replacements


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

    mapping, detected, renamed = rename_attachments(root_dir, self_exec, allowed_exts, allow_other, allow_all)
    mapping_path = save_mapping(root_dir, mapping, data_dir)

    index = build_file_index(root_dir)
    index_path = save_index(root_dir, index, data_dir)

    md_files, replacements = process_markdown_files(root_dir, index, mapping)
    duplicates, duplicate_table = detect_duplicate_filenames(index)

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
    projects = load_projects_config()
    app = tk.Tk()
    app.title("Markdown 附件重命名 & 路径修复工具")
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
    style.configure("Accent.TButton", padding=6)
    style.configure("Card.TFrame", background="#ffffff", relief="flat")

    colors = {
        "bg": "#f6f8fb",
        "card": "#ffffff",
        "hover": "#eef2fb",
        "selected": "#dde7ff",
        "danger": "#ffe6e6",
        "text": "#1f2933",
        "muted": "#6b7280",
        "accent": "#4f46e5",
    }

    state = {"projects": projects, "selected": 0}
    running = {"flag": False}
    logo_img = None
    if os.path.exists(LOGO_PATH):
        try:
            logo_img = tk.PhotoImage(file=LOGO_PATH)
        except Exception:
            logo_img = None

    def persist_projects():
        save_projects_config(state["projects"])

    def ensure_selection():
        if not state["projects"]:
            state["selected"] = 0
        else:
            state["selected"] = max(0, min(state["selected"], len(state["projects"]) - 1))

    def add_project(root_val: str, data_val: str, name_val: str):
        project = {
            "name": name_val.strip() or Path(root_val).name,
            "root": root_val,
            "data_dir": data_val,
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

        root_path = tk.StringVar(value=default_root or get_app_root())
        data_path = tk.StringVar(value="")
        name_var = tk.StringVar(value="")

        ttk.Label(frame, text="首次使用 - 配置项目", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(frame, text="文档项目位置（必填）：").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=root_path, width=60).grid(row=1, column=1, sticky="we", padx=(4, 4))
        ttk.Button(frame, text="选择…", command=lambda: root_path.set(filedialog.askdirectory(initialdir=root_path.get() or get_app_root()) or root_path.get())).grid(row=1, column=2, sticky="e")

        ttk.Label(frame, text="数据存放位置（必填）：").grid(row=2, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=data_path, width=60).grid(row=2, column=1, sticky="we", padx=(4, 4))
        ttk.Button(frame, text="选择…", command=lambda: data_path.set(filedialog.askdirectory(initialdir=data_path.get() or get_app_root()) or data_path.get())).grid(row=2, column=2, sticky="e")

        ttk.Label(frame, text="自定义名称（可选）：").grid(row=3, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=name_var, width=60).grid(row=3, column=1, sticky="we", padx=(4, 4))

        def submit():
            root_val = root_path.get().strip()
            data_val = data_path.get().strip()
            if not root_val or not data_val:
                messagebox.showerror("缺少信息", "请填写文档项目位置和数据存放位置。")
                return
            os.makedirs(data_val, exist_ok=True)
            add_project(root_val, data_val, name_var.get())
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

        header = tk.Frame(wrapper, bg=colors["bg"], padx=12, pady=10)
        header.grid(row=0, column=0, columnspan=2, sticky="we")
        if logo_img:
            tk.Label(header, image=logo_img, bg=colors["bg"]).pack(side="left")
        tk.Label(header, text="文档项目管理", bg=colors["bg"], fg=colors["text"], font=("Segoe UI", 14, "bold")).pack(side="left", padx=(8, 0))

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
        data_path = tk.StringVar()
        all_var = tk.BooleanVar(value=False)
        cat_vars = {name: tk.BooleanVar(value=False) for name in PROJECT_CATEGORIES}

        log_box = tk.Text(right, height=14, width=90, state="disabled", bg="#0b1220", fg="#d1e7ff")

        class TextHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                log_box.after(0, lambda: append_log(msg))

        def append_log(message: str):
            log_box.configure(state="normal")
            log_box.insert(tk.END, message + "\n")
            log_box.see(tk.END)
            log_box.configure(state="disabled")

        def collect_categories():
            if all_var.get():
                return ["all"]
            selected = [name for name, var in cat_vars.items() if var.get()]
            return selected or [DEFAULT_RENAME_CATEGORY]

        def update_project_from_fields():
            if not state["projects"]:
                return
            proj = state["projects"][state["selected"]]
            proj["name"] = project_name.get().strip() or Path(proj["root"]).name
            proj["root"] = root_path.get().strip()
            proj["data_dir"] = data_path.get().strip()
            proj["rename_types"] = collect_categories()
            persist_projects()
            render_project_list()

        def set_fields_from_project(index: int):
            proj = state["projects"][index]
            project_name.set(proj.get("name") or Path(proj["root"]).name)
            root_path.set(proj["root"])
            data_path.set(proj.get("data_dir", ""))
            rename_types = proj.get("rename_types", [DEFAULT_RENAME_CATEGORY])
            all_var.set("all" in rename_types)
            for name in PROJECT_CATEGORIES:
                cat_vars[name].set(name in rename_types)

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
            if not proj.get("root") or not proj.get("data_dir"):
                messagebox.showerror("缺少信息", "请填写文档项目位置和数据存放位置。")
                return
            if not messagebox.askyesno("执行确认", "确定执行重命名检查和链接修复吗？"):
                return
            running["flag"] = True
            os.makedirs(proj["data_dir"], exist_ok=True)
            log_box.configure(state="normal")
            log_box.delete("1.0", tk.END)
            log_box.configure(state="disabled")
            update_project_from_fields()

            def worker():
                try:
                    handler = TextHandler()
                    handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
                    summary = run_pipeline(
                        proj["root"],
                        proj.get("rename_types") or [DEFAULT_RENAME_CATEGORY],
                        verbose=False,
                        extra_handlers=[handler],
                        data_dir=proj["data_dir"],
                    )
                    msg = (
                        f"重命名 {summary['renamed_files']}/{summary['rename_candidates']}；"
                        f"Markdown 修复 {summary['markdown_fixed']}，替换 {summary['replacements']} 处。"
                    )
                    app.after(0, lambda: append_log(msg))
                    app.after(0, lambda: messagebox.showinfo("完成", msg))
                except Exception as exc:
                    app.after(0, lambda: messagebox.showerror("运行失败", str(exc)))
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
            all_var_dlg = tk.BooleanVar(value=("all" in proj.get("rename_types", [])))
            cat_vars_dlg = {
                name: tk.BooleanVar(value=(name in proj.get("rename_types", [])))
                for name in PROJECT_CATEGORIES
            }

            ttk.Label(dialog, text="名称：").grid(row=0, column=0, sticky="w", pady=(8, 2))
            ttk.Entry(dialog, textvariable=name_var, width=50).grid(row=0, column=1, sticky="we", padx=(4, 4))

            ttk.Label(dialog, text="文档项目位置：").grid(row=1, column=0, sticky="w", pady=(6, 2))
            ttk.Label(dialog, text=proj.get("root", ""), foreground=colors["muted"]).grid(row=1, column=1, sticky="w")

            ttk.Label(dialog, text="数据存放位置：").grid(row=2, column=0, sticky="w", pady=(6, 2))
            ttk.Label(dialog, text=proj.get("data_dir", ""), foreground=colors["muted"]).grid(row=2, column=1, sticky="w")

            ttk.Label(dialog, text="重命名分类：").grid(row=3, column=0, sticky="w", pady=(8, 2))
            cat_row = 4
            col = 0
            for name in PROJECT_CATEGORIES:
                ttk.Checkbutton(dialog, text=name, variable=cat_vars_dlg[name]).grid(row=cat_row, column=col, sticky="w")
                col += 1
                if col > 2:
                    cat_row += 1
                    col = 0
            ttk.Checkbutton(dialog, text="全量（all）", variable=all_var_dlg).grid(row=cat_row, column=col, sticky="w")

            def collect_categories_dialog():
                if all_var_dlg.get():
                    return ["all"]
                selected = [name for name, var in cat_vars_dlg.items() if var.get()]
                return selected or [DEFAULT_RENAME_CATEGORY]

            def confirm_update():
                if not messagebox.askyesno("确认修改", "确认修改名称和分类吗？"):
                    return
                proj["name"] = name_var.get().strip() or Path(proj.get("root", "")).name
                proj["rename_types"] = collect_categories_dialog()
                persist_projects()
                set_fields_from_project(idx)
                render_project_list()
                dialog.destroy()

            btn_frame = ttk.Frame(dialog)
            btn_frame.grid(row=cat_row + 1, column=0, columnspan=3, pady=(10, 4), sticky="e")
            def confirm_delete():
                if not messagebox.askyesno("确认删除", "确认删除该项目配置吗？不会删除实际文件。"):
                    return
                state["projects"].pop(idx)
                ensure_selection()
                persist_projects()
                dialog.destroy()
                build_main_ui()

            ttk.Button(btn_frame, text="修改", command=confirm_update).pack(side="left", padx=4)
            ttk.Button(btn_frame, text="删除", command=confirm_delete).pack(side="left", padx=4)
            ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side="left", padx=4)

            dialog.columnconfigure(1, weight=1)

        def render_project_list():
            for child in list_frame.winfo_children():
                child.destroy()

            for i, proj in enumerate(state["projects"]):
                exists = os.path.exists(proj.get("root", ""))
                is_selected = i == state["selected"]
                bg = colors["selected"] if is_selected else colors["card"]
                missing_bg = colors["danger"] if not exists else bg

                card = tk.Frame(list_frame, bg=missing_bg, bd=1, relief="solid", highlightthickness=0, padx=10, pady=6)
                card.pack(fill="x", pady=4)

                top_row = tk.Frame(card, bg=missing_bg)
                top_row.pack(fill="x")
                name = proj.get("name") or Path(proj.get("root", "")).name
                tk.Label(top_row, text=name, bg=missing_bg, fg=colors["text"], font=("Segoe UI", 10, "bold")).pack(side="left")
                status_txt = "路径缺失" if not exists else "正常"
                status_fg = "#c53030" if not exists else colors["muted"]
                tk.Label(top_row, text=status_txt, bg=missing_bg, fg=status_fg, font=("Segoe UI", 9)).pack(side="right")

                root_display = proj.get("root", "")
                data_display = proj.get("data_dir", "")
                tk.Label(card, text="Root:", bg=missing_bg, fg=colors["muted"], anchor="w").pack(fill="x")
                link_root = tk.Label(card, text=root_display, bg=missing_bg, fg=colors["accent"], anchor="w", cursor="hand2")
                link_root.pack(fill="x")
                link_root.bind("<Button-1>", lambda e, p=root_display: open_path(p))

                tk.Label(card, text="Data:", bg=missing_bg, fg=colors["muted"], anchor="w").pack(fill="x")
                link_data = tk.Label(card, text=data_display, bg=missing_bg, fg=colors["accent"], anchor="w", cursor="hand2")
                link_data.pack(fill="x")
                link_data.bind("<Button-1>", lambda e, p=data_display: open_path(p))

                tk.Label(card, text=f"分类: {', '.join(proj.get('rename_types', [DEFAULT_RENAME_CATEGORY]))}", bg=missing_bg, fg=colors["muted"], anchor="w").pack(fill="x")

                actions = tk.Frame(card, bg=missing_bg)
                view_btn = ttk.Button(actions, text="查看", command=lambda idx=i: show_details(idx))
                run_btn = ttk.Button(actions, text="运行", command=lambda idx=i: run_project(idx))
                open_btn = ttk.Button(actions, text="打开目录", command=lambda p=proj.get("root", ""): open_path(p))
                data_btn = ttk.Button(actions, text="数据目录", command=lambda p=proj.get("data_dir", ""): open_path(p))
                view_btn.pack(side="left", padx=2)
                run_btn.pack(side="left", padx=2)
                open_btn.pack(side="left", padx=2)
                data_btn.pack(side="left", padx=2)
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
                card.bind("<Button-1>", lambda e, idx=i: select_project(idx))
                for child in card.winfo_children():
                    child.bind("<Button-1>", lambda e, idx=i: select_project(idx))

                if is_selected:
                    actions.pack(side="right", anchor="e", pady=(4, 0))

        # Detail form
        tk.Label(right, text="项目详情", bg=colors["bg"], fg=colors["text"], font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(right, text="名称：").grid(row=1, column=0, sticky="w", pady=(6, 2))
        ttk.Entry(right, textvariable=project_name).grid(row=1, column=1, sticky="we", padx=(4, 4))

        ttk.Label(right, text="文档项目位置：").grid(row=2, column=0, sticky="w", pady=(6, 2))
        ttk.Entry(right, textvariable=root_path).grid(row=2, column=1, sticky="we", padx=(4, 4))
        ttk.Button(right, text="选择…", command=lambda: (root_path.set(filedialog.askdirectory(initialdir=root_path.get() or get_app_root()) or root_path.get()), update_project_from_fields())).grid(row=2, column=2, sticky="e")
        ttk.Button(right, text="打开", command=lambda: open_path(root_path.get())).grid(row=2, column=3, sticky="e")

        ttk.Label(right, text="数据存放位置：").grid(row=3, column=0, sticky="w", pady=(6, 2))
        ttk.Entry(right, textvariable=data_path).grid(row=3, column=1, sticky="we", padx=(4, 4))
        def choose_data_dir():
            new_dir = filedialog.askdirectory(initialdir=data_path.get() or get_app_root()) or data_path.get()
            if not new_dir:
                return
            new_dir = new_dir.strip()
            if not new_dir:
                return
            old_dir = data_path.get().strip()
            if old_dir and os.path.abspath(old_dir) != os.path.abspath(new_dir):
                if not messagebox.askyesno("迁移数据", f"确认将数据从\n{old_dir}\n移动到\n{new_dir}\n吗？"):
                    return
                os.makedirs(new_dir, exist_ok=True)
                move_project_data(old_dir, new_dir)
            data_path.set(new_dir)
            update_project_from_fields()
        ttk.Button(right, text="设置", command=choose_data_dir).grid(row=3, column=2, sticky="e")
        ttk.Button(right, text="打开", command=lambda: open_path(data_path.get())).grid(row=3, column=3, sticky="e")

        ttk.Label(right, text="重命名分类（仅影响重命名步骤）：").grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 4))
        cat_row = 5
        col = 0
        for name in PROJECT_CATEGORIES:
            chk = ttk.Checkbutton(right, text=name, variable=cat_vars[name], command=update_project_from_fields)
            chk.grid(row=cat_row, column=col, sticky="w")
            col += 1
            if col > 2:
                cat_row += 1
                col = 0
        ttk.Checkbutton(right, text="全量（all）", variable=all_var, command=update_project_from_fields).grid(row=cat_row, column=col, sticky="w")

        ttk.Button(right, text="保存修改", command=update_project_from_fields).grid(row=cat_row + 1, column=0, pady=(10, 6), sticky="w")
        ttk.Button(right, text="运行当前", command=lambda: run_project(state["selected"])).grid(row=cat_row + 1, column=1, pady=(10, 6), sticky="e")

        ttk.Label(right, text="日志:").grid(row=cat_row + 2, column=0, columnspan=3, sticky="w", pady=(8, 2))
        log_box.grid(row=cat_row + 3, column=0, columnspan=3, sticky="nsew")
        right.rowconfigure(cat_row + 3, weight=1)

        # Populate fields and list
        set_fields_from_project(state["selected"])
        render_project_list()

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
