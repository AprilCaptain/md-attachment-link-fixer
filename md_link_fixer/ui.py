from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .core import (
    LOGO_PATH,
    CATEGORY_LABELS,
    DEFAULT_RENAME_CATEGORY,
    RENAMING_CATEGORIES,
    category_label_from_types,
    CONFIG_PATH,
    get_app_root,
    load_projects_config,
    normalize_categories,
    normalize_display_path,
    open_path,
    run_pipeline,
    save_projects_config,
)


class SignalLogHandler(logging.Handler):
    """Redirect logging output to a Qt signal."""

    def __init__(self, signal: QtCore.SignalInstance):
        super().__init__()
        self.signal = signal

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self.signal.emit(msg)


class PipelineWorker(QtCore.QThread):
    log = QtCore.Signal(str)
    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, root_dir: str, rename_types: List[str], data_dir: Optional[str], verbose: bool):
        super().__init__()
        self.root_dir = root_dir
        self.rename_types = rename_types
        self.data_dir = data_dir
        self.verbose = verbose

    def run(self):
        handler = SignalLogHandler(self.log)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        try:
            summary = run_pipeline(
                self.root_dir,
                self.rename_types,
                verbose=self.verbose,
                extra_handlers=[handler],
                data_dir=self.data_dir,
            )
            self.finished.emit(summary)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class TagChip(QtWidgets.QLabel):
    def __init__(self, text: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("TagChip")
        self.setMargin(6)
        self.setAlignment(QtCore.Qt.AlignCenter)


class SummaryCard(QtWidgets.QFrame):
    def __init__(self, title: str, tooltip: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.value_label = QtWidgets.QLabel("--")
        self.value_label.setObjectName("CardValue")
        if tooltip:
            self.setToolTip(tooltip)
            self.title_label.setToolTip(tooltip)
            self.value_label.setToolTip(tooltip)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str):
        self.value_label.setText(text)


class SystemSettingsDialog(QtWidgets.QDialog):
    def __init__(self, current_data_dir: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.resize(520, 180)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        row = QtWidgets.QHBoxLayout()
        self.data_dir_input = QtWidgets.QLineEdit()
        self.data_dir_input.setPlaceholderText("运行时生成的临时/报告文件目录（可选）")
        self.data_dir_input.setText(current_data_dir or "")
        browse_btn = QtWidgets.QPushButton("浏览")
        browse_btn.setObjectName("SoftButton")
        browse_btn.clicked.connect(self._browse_data_dir)
        row.addWidget(self.data_dir_input)
        row.addWidget(browse_btn)
        form.addRow("数据目录", row)

        hint = QtWidgets.QLabel("说明：默认写入应用目录下的 .data 文件夹（可修改）。")
        hint.setObjectName("Muted")
        layout.addLayout(form)
        layout.addWidget(hint)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton("取消")
        cancel_btn.setObjectName("SoftButton")
        ok_btn = QtWidgets.QPushButton("保存")
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _browse_data_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择数据目录", get_app_root())
        if path:
            self.data_dir_input.setText(normalize_display_path(path))

    def data_dir(self) -> str:
        return normalize_display_path(self.data_dir_input.text().strip())


class ProjectDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        project: Optional[Dict[str, object]] = None,
        is_new: bool = False,
        existing_projects: Optional[List[Dict[str, object]]] = None,
        editing_index: Optional[int] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(720)
        self.setSizeGripEnabled(True)
        self._is_new = is_new
        self._name_edited = False
        self._existing_projects = existing_projects or []
        self._editing_index = editing_index

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("例如：Obsidian 笔记库")
        self.name_input.textEdited.connect(self._on_name_edited)
        form.addRow("名称", self.name_input)

        root_row = QtWidgets.QHBoxLayout()
        self.root_input = QtWidgets.QLineEdit()
        self.root_input.setPlaceholderText("请选择笔记根目录")
        root_btn = QtWidgets.QPushButton("浏览")
        root_btn.setObjectName("SoftButton")
        root_btn.clicked.connect(self._browse_root)
        root_row.addWidget(self.root_input)
        root_row.addWidget(root_btn)
        form.addRow("根目录", root_row)

        self.category_container = QtWidgets.QWidget()
        cat_layout = QtWidgets.QHBoxLayout(self.category_container)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(8)
        self.category_boxes: Dict[str, QtWidgets.QCheckBox] = {}
        for key in RENAMING_CATEGORIES.keys():
            cb = QtWidgets.QCheckBox(CATEGORY_LABELS.get(key, key))
            cb.setProperty("cat-key", key)
            cb.stateChanged.connect(self._on_category_changed)
            self.category_boxes[key] = cb
            cat_layout.addWidget(cb)
        cb_all = QtWidgets.QCheckBox(CATEGORY_LABELS.get("all", "all"))
        cb_all.setProperty("cat-key", "all")
        cb_all.stateChanged.connect(self._on_category_changed)
        self.category_boxes["all"] = cb_all
        cat_layout.addWidget(cb_all)
        cat_layout.addStretch()
        form.addRow("重命名类型", self.category_container)

        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton("取消")
        cancel_btn.setObjectName("SoftButton")
        ok_btn = QtWidgets.QPushButton("保存")
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        if project:
            self.set_project(project)
        else:
            self.set_project({"name": "", "root": "", "categories": [DEFAULT_RENAME_CATEGORY]})
        self.adjustSize()

    def _on_name_edited(self):
        self._name_edited = True

    def _browse_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择笔记根目录", get_app_root())
        if not path:
            return
        path = normalize_display_path(path)
        self.root_input.setText(path)
        if self._is_new and not self._name_edited and not self.name_input.text().strip():
            self.name_input.setText(os.path.basename(path) or "未命名项目")

    def _on_category_changed(self):
        sender = self.sender()
        if not isinstance(sender, QtWidgets.QCheckBox):
            return
        cat_key = sender.property("cat-key")
        if cat_key == "all" and sender.isChecked():
            for key, box in self.category_boxes.items():
                if key != "all":
                    box.setChecked(False)
        elif cat_key != "all" and sender.isChecked():
            all_box = self.category_boxes.get("all")
            if all_box:
                all_box.blockSignals(True)
                all_box.setChecked(False)
                all_box.blockSignals(False)

    def set_project(self, project: Dict[str, object]):
        self.name_input.setText(str(project.get("name") or ""))
        self.root_input.setText(normalize_display_path(str(project.get("root") or "")))
        normalized = normalize_categories(list(project.get("categories") or [DEFAULT_RENAME_CATEGORY]))
        for key, box in self.category_boxes.items():
            box.blockSignals(True)
            box.setChecked(key in normalized)
            box.blockSignals(False)
        if not normalized:
            self.category_boxes[DEFAULT_RENAME_CATEGORY].setChecked(True)

    def project(self) -> Dict[str, object]:
        selected = [key for key, box in self.category_boxes.items() if box.isChecked()]
        normalized = normalize_categories(selected)
        return {
            "name": self.name_input.text().strip() or "未命名项目",
            "root": normalize_display_path(self.root_input.text().strip()),
            "categories": normalized or [DEFAULT_RENAME_CATEGORY],
        }

    def _on_accept(self):
        data = self.project()
        if not data["root"]:
            QtWidgets.QMessageBox.warning(self, "缺少路径", "请先选择根目录。")
            return

        root_norm = os.path.normcase(normalize_display_path(str(data["root"])))
        name_norm = str(data.get("name") or "").strip()
        for idx, proj in enumerate(self._existing_projects):
            if self._editing_index is not None and idx == self._editing_index:
                continue
            other_root = normalize_display_path(str(proj.get("root") or ""))
            if other_root and os.path.normcase(other_root) == root_norm:
                QtWidgets.QMessageBox.warning(self, "项目重复", "已存在相同的根目录项目，请勿重复添加。")
                return
            other_name = str(proj.get("name") or "").strip()
            if other_name and name_norm and other_name == name_norm:
                QtWidgets.QMessageBox.warning(self, "项目名称重复", "已存在相同名称的项目，请修改名称以便区分。")
                return
        self.accept()


class FirstRunWizard(QtWidgets.QDialog):
    def __init__(self, default_data_dir: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("首次启动引导")
        self.resize(760, 420)
        self._name_edited = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("欢迎使用 Markdown 链接修复器")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        desc = QtWidgets.QLabel("请先设置数据存放目录，并创建一个项目（笔记根目录 + 重命名类型）。")
        desc.setObjectName("Muted")
        layout.addWidget(desc)

        # System settings
        sys_card = QtWidgets.QFrame()
        sys_card.setObjectName("Card")
        sys_layout = QtWidgets.QVBoxLayout(sys_card)
        sys_layout.setContentsMargins(14, 12, 14, 12)
        sys_layout.setSpacing(10)
        sys_title = QtWidgets.QLabel("系统设置")
        sys_title.setObjectName("SectionTitle")
        sys_layout.addWidget(sys_title)

        sys_form = QtWidgets.QFormLayout()
        sys_form.setHorizontalSpacing(12)
        sys_form.setVerticalSpacing(10)

        data_row = QtWidgets.QHBoxLayout()
        self.data_dir_input = QtWidgets.QLineEdit()
        self.data_dir_input.setText(default_data_dir or "")
        self.data_dir_input.setPlaceholderText("例如：D:\\data\\md-fixer（默认 .data）")
        data_btn = QtWidgets.QPushButton("浏览")
        data_btn.setObjectName("SoftButton")
        data_btn.clicked.connect(self._browse_data_dir)
        data_row.addWidget(self.data_dir_input)
        data_row.addWidget(data_btn)
        sys_form.addRow("数据目录", data_row)
        sys_layout.addLayout(sys_form)
        layout.addWidget(sys_card)

        # Project settings
        proj_card = QtWidgets.QFrame()
        proj_card.setObjectName("Card")
        proj_layout = QtWidgets.QVBoxLayout(proj_card)
        proj_layout.setContentsMargins(14, 12, 14, 12)
        proj_layout.setSpacing(10)
        proj_title = QtWidgets.QLabel("创建项目")
        proj_title.setObjectName("SectionTitle")
        proj_layout.addWidget(proj_title)

        proj_form = QtWidgets.QFormLayout()
        proj_form.setHorizontalSpacing(12)
        proj_form.setVerticalSpacing(10)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("例如：Obsidian 笔记库")
        self.name_input.textEdited.connect(self._on_name_edited)
        proj_form.addRow("名称", self.name_input)

        root_row = QtWidgets.QHBoxLayout()
        self.root_input = QtWidgets.QLineEdit()
        self.root_input.setPlaceholderText("请选择笔记根目录")
        root_btn = QtWidgets.QPushButton("浏览")
        root_btn.setObjectName("SoftButton")
        root_btn.clicked.connect(self._browse_root)
        root_row.addWidget(self.root_input)
        root_row.addWidget(root_btn)
        proj_form.addRow("根目录", root_row)

        self.category_container = QtWidgets.QWidget()
        cat_layout = QtWidgets.QHBoxLayout(self.category_container)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(8)
        self.category_boxes: Dict[str, QtWidgets.QCheckBox] = {}
        for key in RENAMING_CATEGORIES.keys():
            cb = QtWidgets.QCheckBox(CATEGORY_LABELS.get(key, key))
            cb.setProperty("cat-key", key)
            cb.stateChanged.connect(self._on_category_changed)
            self.category_boxes[key] = cb
            cat_layout.addWidget(cb)
        cb_all = QtWidgets.QCheckBox(CATEGORY_LABELS.get("all", "all"))
        cb_all.setProperty("cat-key", "all")
        cb_all.stateChanged.connect(self._on_category_changed)
        self.category_boxes["all"] = cb_all
        cat_layout.addWidget(cb_all)
        cat_layout.addStretch()
        proj_form.addRow("重命名类型", self.category_container)

        proj_layout.addLayout(proj_form)
        layout.addWidget(proj_card, stretch=1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton("稍后再说")
        cancel_btn.setObjectName("SoftButton")
        ok_btn = QtWidgets.QPushButton("开始使用")
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._set_default_categories([DEFAULT_RENAME_CATEGORY])

    def _browse_data_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择数据目录", get_app_root())
        if path:
            self.data_dir_input.setText(normalize_display_path(path))

    def _on_name_edited(self):
        self._name_edited = True

    def _browse_root(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择笔记根目录", get_app_root())
        if not path:
            return
        path = normalize_display_path(path)
        self.root_input.setText(path)
        if not self._name_edited and not self.name_input.text().strip():
            self.name_input.setText(os.path.basename(path) or "未命名项目")

    def _on_category_changed(self):
        sender = self.sender()
        if not isinstance(sender, QtWidgets.QCheckBox):
            return
        cat_key = sender.property("cat-key")
        if cat_key == "all" and sender.isChecked():
            for key, box in self.category_boxes.items():
                if key != "all":
                    box.setChecked(False)
        elif cat_key != "all" and sender.isChecked():
            all_box = self.category_boxes.get("all")
            if all_box:
                all_box.blockSignals(True)
                all_box.setChecked(False)
                all_box.blockSignals(False)

    def _set_default_categories(self, categories: List[str]):
        normalized = normalize_categories(categories)
        for key, box in self.category_boxes.items():
            box.blockSignals(True)
            box.setChecked(key in normalized)
            box.blockSignals(False)

    def data_dir(self) -> str:
        return normalize_display_path(self.data_dir_input.text().strip())

    def project(self) -> Dict[str, object]:
        selected = [key for key, box in self.category_boxes.items() if box.isChecked()]
        normalized = normalize_categories(selected)
        return {
            "name": self.name_input.text().strip() or "未命名项目",
            "root": normalize_display_path(self.root_input.text().strip()),
            "categories": normalized or [DEFAULT_RENAME_CATEGORY],
        }

    def _on_accept(self):
        proj = self.project()
        if not proj["root"]:
            QtWidgets.QMessageBox.warning(self, "缺少路径", "请先选择根目录。")
            return
        self.accept()


class ProjectCardWidget(QtWidgets.QFrame):
    selected = QtCore.Signal(int)
    run_requested = QtCore.Signal(int)
    details_requested = QtCore.Signal(int)
    open_requested = QtCore.Signal(int)
    remove_requested = QtCore.Signal(int)

    def __init__(self, index: int, name: str, root: str, categories: List[str], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("ProjectCard")
        self.setProperty("selected", False)
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        self.name_label = QtWidgets.QLabel(name or "未命名项目")
        self.name_label.setObjectName("ProjectName")
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(TagChip(category_label_from_types(categories)))
        layout.addLayout(header)

        self.root_label = QtWidgets.QLabel(root or "")
        self.root_label.setObjectName("ProjectMeta")
        self.root_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.root_label)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()

        self.run_btn = QtWidgets.QToolButton()
        self.run_btn.setObjectName("ActionButtonPrimary")
        self.run_btn.setToolTip("运行")
        self.run_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay))
        self.run_btn.clicked.connect(lambda: self.run_requested.emit(self.index))

        self.details_btn = QtWidgets.QToolButton()
        self.details_btn.setObjectName("ActionButton")
        self.details_btn.setToolTip("详情")
        self.details_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MessageBoxInformation))
        self.details_btn.clicked.connect(lambda: self.details_requested.emit(self.index))

        self.open_btn = QtWidgets.QToolButton()
        self.open_btn.setObjectName("ActionButton")
        self.open_btn.setToolTip("打开根目录")
        self.open_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon))
        self.open_btn.clicked.connect(lambda: self.open_requested.emit(self.index))

        self.remove_btn = QtWidgets.QToolButton()
        self.remove_btn.setObjectName("ActionButtonDanger")
        self.remove_btn.setToolTip("删除")
        icon = self.style().standardIcon(getattr(QtWidgets.QStyle, "SP_TrashIcon", QtWidgets.QStyle.SP_DialogCloseButton))
        self.remove_btn.setIcon(icon)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.index))

        for btn in (self.run_btn, self.details_btn, self.open_btn, self.remove_btn):
            btn.setAutoRaise(True)
            btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            btn.setIconSize(QtCore.QSize(18, 18))
            btn.setFixedSize(34, 34)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

    def mousePressEvent(self, event: QtGui.QMouseEvent):  # noqa: N802
        self.selected.emit(self.index)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_running(self, running: bool):
        self.run_btn.setEnabled(not running)
        self.details_btn.setEnabled(not running)
        self.open_btn.setEnabled(not running)
        self.remove_btn.setEnabled(not running)


class ProjectEditor(QtWidgets.QWidget):
    """Project details editor shown on-demand (via '详情')."""

    run_requested = QtCore.Signal()
    save_requested = QtCore.Signal()
    remove_requested = QtCore.Signal()
    browse_root_requested = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("项目详情")
        title.setObjectName("SectionTitle")
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.status_label)
        layout.addLayout(header)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("例如：Obsidian 笔记库")
        form.addRow("名称", self.name_input)

        root_row = QtWidgets.QHBoxLayout()
        self.root_input = QtWidgets.QLineEdit()
        self.root_input.setPlaceholderText("请选择笔记根目录")
        root_btn = QtWidgets.QPushButton("浏览")
        root_btn.clicked.connect(self.browse_root_requested)
        root_row.addWidget(self.root_input)
        root_row.addWidget(root_btn)
        form.addRow("根目录", root_row)

        self.category_container = QtWidgets.QWidget()
        cat_layout = QtWidgets.QHBoxLayout(self.category_container)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(8)
        self.category_boxes: Dict[str, QtWidgets.QCheckBox] = {}
        for key in RENAMING_CATEGORIES.keys():
            cb = QtWidgets.QCheckBox(CATEGORY_LABELS.get(key, key))
            cb.setProperty("cat-key", key)
            cb.stateChanged.connect(self._on_category_changed)
            self.category_boxes[key] = cb
            cat_layout.addWidget(cb)
        cb_all = QtWidgets.QCheckBox(CATEGORY_LABELS.get("all", "all"))
        cb_all.setProperty("cat-key", "all")
        cb_all.stateChanged.connect(self._on_category_changed)
        self.category_boxes["all"] = cb_all
        cat_layout.addWidget(cb_all)
        cat_layout.addStretch()
        form.addRow("重命名类型", self.category_container)

        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("运行")
        self.save_btn = QtWidgets.QPushButton("保存/更新")
        self.remove_btn = QtWidgets.QPushButton("删除")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.run_requested)
        self.save_btn.clicked.connect(self.save_requested)
        self.remove_btn.clicked.connect(self.remove_requested)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _on_category_changed(self):
        sender = self.sender()
        if not isinstance(sender, QtWidgets.QCheckBox):
            return
        cat_key = sender.property("cat-key")
        if cat_key == "all" and sender.isChecked():
            for key, box in self.category_boxes.items():
                if key != "all":
                    box.setChecked(False)
        elif cat_key != "all" and sender.isChecked():
            all_box = self.category_boxes.get("all")
            if all_box:
                all_box.blockSignals(True)
                all_box.setChecked(False)
                all_box.blockSignals(False)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_fields(self, name: str, root: str, categories: List[str]):
        self.name_input.setText(name)
        self.root_input.setText(root)
        normalized = normalize_categories(categories)
        for key, box in self.category_boxes.items():
            box.blockSignals(True)
            box.setChecked(key in normalized)
            box.blockSignals(False)
        if not normalized:
            self.category_boxes[DEFAULT_RENAME_CATEGORY].setChecked(True)

    def get_fields(self) -> Dict[str, object]:
        selected = [key for key, box in self.category_boxes.items() if box.isChecked()]
        normalized = normalize_categories(selected)
        return {
            "name": self.name_input.text().strip() or "未命名项目",
            "root": normalize_display_path(self.root_input.text().strip()),
            "categories": normalized or [DEFAULT_RENAME_CATEGORY],
        }

    def set_running(self, running: bool):
        self.run_btn.setEnabled(not running)
        self.save_btn.setEnabled(not running)
        self.remove_btn.setEnabled(not running)
        self.run_btn.setText("运行中..." if running else "运行")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, default_root: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Markdown 链接修复器 (PySide6)")
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QtGui.QIcon(LOGO_PATH))
        self.projects: List[Dict] = []
        self.settings: Dict = {}
        self.current_index: Optional[int] = None
        self.active_worker: Optional[PipelineWorker] = None
        self.latest_summary: Optional[Dict] = None
        self._build_ui()
        self._load_state(default_root)

    def _build_ui(self):
        self.resize(1100, 720)
        self._build_menu()
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        # Left panel (项目卡片 + 详情面板)
        left_card = QtWidgets.QFrame()
        left_card.setObjectName("Card")
        left_layout = QtWidgets.QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("项目列表")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.settings_btn = QtWidgets.QPushButton("系统设置")
        self.settings_btn.setObjectName("SoftButton")
        self.settings_btn.clicked.connect(self.open_system_settings)
        header.addWidget(self.settings_btn)
        self.add_btn = QtWidgets.QPushButton("新增")
        self.add_btn.setObjectName("SoftButton")
        self.add_btn.clicked.connect(self.add_project)
        header.addWidget(self.add_btn)
        left_layout.addLayout(header)

        self.project_scroll = QtWidgets.QScrollArea()
        self.project_scroll.setWidgetResizable(True)
        self.project_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.project_cards_container = QtWidgets.QWidget()
        self.project_cards_layout = QtWidgets.QVBoxLayout(self.project_cards_container)
        self.project_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.project_cards_layout.setSpacing(10)
        self.project_cards_layout.addStretch()
        self.project_scroll.setWidget(self.project_cards_container)
        left_layout.addWidget(self.project_scroll, stretch=1)

        root_layout.addWidget(left_card, stretch=1)

        # Right panel (纯展示：项目信息 + 概览 + 日志)
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setSpacing(10)

        info_card = QtWidgets.QFrame()
        info_card.setObjectName("Card")
        info_layout = QtWidgets.QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(4)
        info_title = QtWidgets.QLabel("当前项目")
        info_title.setObjectName("SectionTitle")
        info_layout.addWidget(info_title)
        self.info_label = QtWidgets.QLabel("未选择项目")
        self.info_label.setObjectName("Muted")
        info_layout.addWidget(self.info_label)
        right_layout.addWidget(info_card, stretch=0)

        # Summary + Logs
        deck = QtWidgets.QTabWidget()
        deck.setObjectName("Deck")
        summary_tab = QtWidgets.QWidget()
        logs_tab = QtWidgets.QWidget()
        deck.addTab(summary_tab, "运行概览")
        deck.addTab(logs_tab, "日志")
        right_layout.addWidget(deck, stretch=1)

        # Summary layout (outer scroll + per-table scroll)
        summary_scroll = QtWidgets.QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        summary_container = QtWidgets.QWidget()
        summary_scroll.setWidget(summary_container)

        summary_layout = QtWidgets.QVBoxLayout(summary_container)
        summary_layout.setContentsMargins(4, 8, 4, 8)
        summary_layout.setSpacing(10)

        summary_tab_layout = QtWidgets.QVBoxLayout(summary_tab)
        summary_tab_layout.setContentsMargins(0, 0, 0, 0)
        summary_tab_layout.setSpacing(0)
        summary_tab_layout.addWidget(summary_scroll)

        card_row = QtWidgets.QHBoxLayout()
        card_row.setSpacing(8)
        self.cards = {
            "rename": SummaryCard("附件重命名", "显示：实际重命名数量 / 候选数量"),
            "markdown": SummaryCard("Markdown 修复", "显示：被修改的 Markdown 文件数 / 链接替换总次数"),
            "duplicates": SummaryCard("重名文件", "显示：重名文件条目数（以重命名操作之前的情况统计）"),
            "invalid": SummaryCard("失效引用", "显示：Markdown 中仍无法解析的引用数量"),
        }
        for card in self.cards.values():
            card_row.addWidget(card)
        summary_layout.addLayout(card_row)

        # 分表展示不同数据（默认最多显示 10 行，其余用表格滚动）
        self.rename_table = QtWidgets.QTableWidget(0, 4)
        self.rename_table.setHorizontalHeaderLabels(["序号", "旧文件", "新文件", "所在路径"])
        self._init_table(self.rename_table)

        self.fixed_table = QtWidgets.QTableWidget(0, 3)
        self.fixed_table.setHorizontalHeaderLabels(["序号", "Markdown 文件", "路径"])
        self._init_table(self.fixed_table)

        self.dup_table = QtWidgets.QTableWidget(0, 3)
        self.dup_table.setHorizontalHeaderLabels(["序号", "文件名", "路径"])
        self._init_table(self.dup_table)

        self.invalid_table = QtWidgets.QTableWidget(0, 3)
        self.invalid_table.setHorizontalHeaderLabels(["序号", "Markdown 文件", "失效引用"])
        self._init_table(self.invalid_table)

        summary_layout.addWidget(self._wrap_table("重命名详情", self.rename_table))
        summary_layout.addWidget(self._wrap_table("修复的 Markdown", self.fixed_table))
        summary_layout.addWidget(self._wrap_table("重名文件", self.dup_table))
        summary_layout.addWidget(self._wrap_table("失效引用", self.invalid_table))

        # Logs layout
        logs_layout = QtWidgets.QVBoxLayout(logs_tab)
        logs_layout.setContentsMargins(4, 8, 4, 8)
        logs_layout.setSpacing(6)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        logs_layout.addWidget(self.log_view)

        root_layout.addLayout(right_layout, stretch=2)

        # Style
        self._apply_style()

    def _init_table(self, table: QtWidgets.QTableWidget):
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        table.setWordWrap(False)

        if table.columnCount() > 0:
            header_item = table.horizontalHeaderItem(0)
            if header_item and header_item.text() == "序号":
                table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
                table.setColumnWidth(0, 64)

        self._update_table_height(table, row_count=0)

    def _wrap_table(self, title: str, table: QtWidgets.QTableWidget) -> QtWidgets.QWidget:
        wrapper = QtWidgets.QFrame()
        wrapper.setObjectName("Card")
        layout = QtWidgets.QVBoxLayout(wrapper)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        layout.addWidget(table)
        return wrapper

    def _update_table_height(self, table: QtWidgets.QTableWidget, row_count: int, max_visible_rows: int = 10):
        visible_rows = min(max(row_count, 1), max_visible_rows)
        header_height = table.horizontalHeader().sizeHint().height()
        row_height = table.verticalHeader().defaultSectionSize()
        frame = table.frameWidth() * 2
        scrollbar_height = 0
        if table.horizontalScrollBarPolicy() != QtCore.Qt.ScrollBarAlwaysOff and table.horizontalScrollBar().isVisible():
            scrollbar_height = table.horizontalScrollBar().sizeHint().height()
        extra = 8
        table.setFixedHeight(header_height + (visible_rows * row_height) + frame + scrollbar_height + extra)

    def _apply_style(self):
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#f6f7fb"))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#1f2937"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 13px; color: #1f2937; }
            QFrame#Card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
            QLabel#SectionTitle { font-size: 16px; font-weight: 700; }
            QLabel#Muted { color: #6b7280; }
            QLabel#CardTitle { color: #6b7280; font-size: 12px; }
            QLabel#CardValue { font-size: 22px; font-weight: 700; color: #111827; }
            QFrame#ProjectCard { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
            QFrame#ProjectCard[selected=\"true\"] { border: 2px solid #2563eb; }
            QLabel#ProjectName { font-size: 14px; font-weight: 700; }
            QLabel#ProjectMeta { color: #6b7280; }
            QPushButton { background: #2563eb; color: white; padding: 8px 14px; border-radius: 8px; border: none; }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:disabled { background: #93c5fd; }
            QPushButton#SoftButton { background: #f3f4f6; color: #111827; border: 1px solid #e5e7eb; }
            QPushButton#SoftButton:hover { background: #e5e7eb; }
            QToolButton#ActionButton,
            QToolButton#ActionButtonPrimary,
            QToolButton#ActionButtonDanger {
                border-radius: 10px;
                border: 1px solid #e5e7eb;
                background: #ffffff;
            }
            QToolButton#ActionButton:hover { background: #f3f4f6; }
            QToolButton#ActionButtonPrimary { border: none; background: #2563eb; color: white; }
            QToolButton#ActionButtonPrimary:hover { background: #1d4ed8; }
            QToolButton#ActionButtonPrimary:disabled { background: #93c5fd; }
            QToolButton#ActionButtonDanger { background: #fff1f2; border: 1px solid #fecdd3; }
            QToolButton#ActionButtonDanger:hover { background: #ffe4e6; }
            QLineEdit { border: 1px solid #d1d5db; border-radius: 8px; padding: 8px; }
            QTabWidget::pane { border: 1px solid #e5e7eb; border-radius: 10px; padding: 6px; }
            QTabBar::tab { padding: 8px 14px; margin-right: 4px; border: 1px solid #e5e7eb; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; background: #f3f4f6; }
            QTabBar::tab:selected { background: #ffffff; color: #1f2937; }
            QPlainTextEdit { border: 1px solid #e5e7eb; border-radius: 10px; background: #0f172a; color: #e2e8f0; font-family: 'JetBrains Mono', 'Consolas', monospace; }
            QTableWidget { border: 1px solid #e5e7eb; border-radius: 10px; }
            QHeaderView::section { background: #f3f4f6; padding: 6px; border: none; }
            """
        )

    def _build_menu(self):
        menu = self.menuBar().addMenu("设置")
        action = menu.addAction("系统设置...")
        action.triggered.connect(self.open_system_settings)

    # ---------- State ----------

    def _load_state(self, default_root: Optional[str]):
        projects, settings = load_projects_config()
        self.projects = projects or []
        self.settings = settings or {}
        if not self.settings.get("data_dir"):
            self.settings["data_dir"] = normalize_display_path(os.path.join(get_app_root(), ".data"))
        if default_root:
            self.projects = [{"name": "临时项目", "root": normalize_display_path(default_root), "categories": [DEFAULT_RENAME_CATEGORY]}] + self.projects
        for proj in self.projects:
            proj.pop("data_dir", None)

        if default_root is None and os.path.exists(CONFIG_PATH) is False and not self.projects:
            self._show_first_run_wizard()

        self.refresh_project_list(select_first=True)

    def _show_first_run_wizard(self):
        dlg = FirstRunWizard(default_data_dir=normalize_display_path(self.settings.get("data_dir") or ""), parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.settings["data_dir"] = dlg.data_dir() or normalize_display_path(os.path.join(get_app_root(), ".data"))
            proj = dlg.project()
            self.projects = [proj] + self.projects
            self.current_index = 0
        self.settings["has_seen_wizard"] = True
        save_projects_config(self.projects, self.settings)

    def refresh_project_list(self, select_first: bool = False):
        while self.project_cards_layout.count() > 1:
            item = self.project_cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for idx, proj in enumerate(self.projects):
            card = ProjectCardWidget(
                index=idx,
                name=proj.get("name", "未命名项目"),
                root=normalize_display_path(proj.get("root", "")),
                categories=proj.get("categories") or [DEFAULT_RENAME_CATEGORY],
            )
            card.selected.connect(self.select_project)
            card.run_requested.connect(self.run_project)
            card.details_requested.connect(self.show_project_details)
            card.open_requested.connect(self.open_project_root)
            card.remove_requested.connect(self.remove_project_by_index)
            self.project_cards_layout.insertWidget(self.project_cards_layout.count() - 1, card)
        if self.projects:
            if select_first or self.current_index is None:
                self.select_project(0, ensure_visible=False)
            elif 0 <= self.current_index < len(self.projects):
                self.select_project(self.current_index, ensure_visible=False)
        else:
            self.current_index = None
            self.info_label.setText("未选择项目")

    def select_project(self, index: int, ensure_visible: bool = True):
        if index < 0 or index >= len(self.projects):
            return
        self.current_index = index
        proj = self.projects[index]
        tags = category_label_from_types(proj.get("categories"))
        self.info_label.setText(
            f"{proj.get('name', '未命名项目')}｜{normalize_display_path(proj.get('root', ''))}｜分类：{tags}"
        )
        for i in range(self.project_cards_layout.count() - 1):
            w = self.project_cards_layout.itemAt(i).widget()
            if isinstance(w, ProjectCardWidget):
                w.set_selected(w.index == index)
        if ensure_visible:
            for i in range(self.project_cards_layout.count() - 1):
                w = self.project_cards_layout.itemAt(i).widget()
                if isinstance(w, ProjectCardWidget) and w.index == index:
                    self.project_scroll.ensureWidgetVisible(w)
                    break

    # ---------- Actions ----------

    def add_project(self):
        dlg = ProjectDialog("新增项目", project=None, is_new=True, existing_projects=self.projects, parent=self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        data = dlg.project()
        self.projects.insert(0, data)
        self.current_index = 0
        save_projects_config(self.projects, self.settings)
        self.refresh_project_list(select_first=True)

    def _start_pipeline(self, data: Dict[str, object]):
        rename_types = data.get("categories") or [DEFAULT_RENAME_CATEGORY]
        root_dir = str(data.get("root") or "")
        data_dir = self.settings.get("data_dir")
        self.active_worker = PipelineWorker(
            root_dir,
            list(rename_types),
            data_dir,
            verbose=True,
        )
        self.active_worker.log.connect(self.append_log)
        self.active_worker.finished.connect(self.on_run_finished)
        self.active_worker.failed.connect(self.on_run_failed)
        self.active_worker.start()

    def run_project(self, index: int):
        if index < 0 or index >= len(self.projects):
            return
        proj = self.projects[index]
        if not proj.get("root"):
            QtWidgets.QMessageBox.warning(self, "缺少路径", "该项目未设置根目录。")
            return
        self.select_project(index)
        self.log_view.clear()
        self._set_ui_running(True)
        self.statusBar().showMessage(f"运行中：{proj.get('name', '未命名项目')}")
        self._start_pipeline(proj)

    def show_project_details(self, index: int):
        if index < 0 or index >= len(self.projects):
            return
        proj = dict(self.projects[index])
        dlg = ProjectDialog(
            "项目详情 / 修改",
            project=proj,
            is_new=False,
            existing_projects=self.projects,
            editing_index=index,
            parent=self,
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self.projects[index] = dlg.project()
        save_projects_config(self.projects, self.settings)
        self.refresh_project_list(select_first=False)
        self.select_project(index, ensure_visible=True)

    def open_project_root(self, index: int):
        if index < 0 or index >= len(self.projects):
            return
        root = normalize_display_path(self.projects[index].get("root", ""))
        open_path(root)

    def remove_project_by_index(self, index: int):
        if index < 0 or index >= len(self.projects):
            return
        name = self.projects[index].get("name", "未命名项目")
        if not self._confirm_dialog("确认删除", f"确定删除项目：{name} 吗？"):
            return
        self.projects.pop(index)
        if self.current_index == index:
            self.current_index = None
        elif self.current_index is not None and self.current_index > index:
            self.current_index -= 1
        save_projects_config(self.projects, self.settings)
        self.refresh_project_list(select_first=True)

    def _confirm_dialog(self, title: str, message: str) -> bool:
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        yes_btn = box.button(QtWidgets.QMessageBox.Yes)
        no_btn = box.button(QtWidgets.QMessageBox.No)
        if yes_btn:
            yes_btn.setText("是")
        if no_btn:
            no_btn.setText("否")
        box.setDefaultButton(QtWidgets.QMessageBox.No)
        return box.exec() == QtWidgets.QMessageBox.Yes
    def open_system_settings(self):
        dlg = SystemSettingsDialog(current_data_dir=normalize_display_path(self.settings.get("data_dir") or ""), parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.settings["data_dir"] = dlg.data_dir() or normalize_display_path(os.path.join(get_app_root(), ".data"))
            save_projects_config(self.projects, self.settings)

    def on_run_finished(self, summary: Dict):
        self._set_ui_running(False)
        self.latest_summary = summary
        self.statusBar().showMessage("运行完成", 5000)
        self._render_summary(summary)
        save_projects_config(self.projects, self.settings)

    def on_run_failed(self, msg: str):
        self._set_ui_running(False)
        QtWidgets.QMessageBox.critical(self, "运行失败", msg)
        self.statusBar().showMessage("运行失败", 5000)

    def append_log(self, message: str):
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _set_ui_running(self, running: bool):
        self.add_btn.setEnabled(not running)
        self.settings_btn.setEnabled(not running)
        for i in range(self.project_cards_layout.count() - 1):
            w = self.project_cards_layout.itemAt(i).widget()
            if isinstance(w, ProjectCardWidget):
                w.set_running(running)

    # ---------- Summary ----------

    def _render_summary(self, summary: Optional[Dict]):
        if not summary:
            return
        self.cards["rename"].set_value(f"{summary.get('renamed_files', 0)}/{summary.get('rename_candidates', 0)}")
        md_files = int(summary.get("markdown_fixed", 0) or 0)
        replacements = int(summary.get("replacements", 0) or 0)
        self.cards["markdown"].set_value(f"{md_files} 文件 / {replacements} 处")
        self.cards["duplicates"].set_value(str(len(summary.get("duplicate_list", []))))
        self.cards["invalid"].set_value(str(summary.get("invalid_reference_count", 0)))

        rename_rows = [
            (item.get("old", ""), item.get("new", ""), normalize_display_path(item.get("path", "")))
            for item in summary.get("rename_details", [])
        ]
        self._fill_table(self.rename_table, rename_rows, 3)

        fixed_rows = [
            (os.path.basename(p), normalize_display_path(p))
            for p in summary.get("fixed_files", [])
        ]
        self._fill_table(self.fixed_table, fixed_rows, 2)

        dup_rows = [
            (item.get("name", ""), normalize_display_path(item.get("path", "")))
            for item in summary.get("duplicate_list", [])
        ]
        self._fill_table(self.dup_table, dup_rows, 2)

        invalid_rows = [
            (normalize_display_path(item.get("file", "")), item.get("link", ""))
            for item in summary.get("invalid_references", [])
        ]
        self._fill_table(self.invalid_table, invalid_rows, 2)

    def _fill_table(self, table: QtWidgets.QTableWidget, rows: List[tuple], columns: int):
        if table.columnCount() != columns + 1:
            table.setColumnCount(columns + 1)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            index_item = QtWidgets.QTableWidgetItem(str(i + 1))
            index_item.setTextAlignment(QtCore.Qt.AlignCenter)
            table.setItem(i, 0, index_item)
            for j in range(columns):
                table.setItem(i, j + 1, QtWidgets.QTableWidgetItem(row[j] if j < len(row) else ""))
        self._update_table_height(table, row_count=len(rows))


def launch_ui(default_root: Optional[str] = None):
    """Entry for GUI mode."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("Markdown Link Fixer")
    window = MainWindow(default_root=default_root)
    window.show()
    app.exec()
