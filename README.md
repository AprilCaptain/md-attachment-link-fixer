# md_link_fixer  
### Markdown Attachment Renamer & Link Auto-Fixer  
### Markdown路径修复工具

---

## 📌 功能 Features

`md_link_fixer.py` 是一个用于 Markdown 笔记库的智能路径修复工具。  
适用于：Obsidian、VSCode、Typora、自建笔记目录 等。

### ✔ 自动重命名附件（非 Markdown 文件）
- 使用唯一文件名格式：  
  `yyyyMMddHHmmssSSS + 两位随机数 + 扩展名`
- 已符合命名规则的文件不会再次改名。
- `.exe`、`.app`、脚本自身不会被修改。

### ✔ 自动修复 Markdown 文件中的相对路径引用
支持语法：
- `![]()`
- `[]()`
- `<img src="">`
- `<image href="">`

### ✔ Markdown 文件移动/改名也能自动修复
如果 Markdown 文件被你手动移动或改名，本工具也会尝试自动修复引用：

修复策略：
1. **精确匹配文件名**
2. **两端模糊匹配（filename in basename）**
3. 若匹配到多个 → **跳过（安全优先）**

### ✔ 附件（图片、PDF、视频等）仅精确文件名匹配
如果文件移动到其他目录，只要文件名唯一，也能自动修复引用。

### ✔ 自动忽略隐藏目录
自动跳过所有以 `.` 开头的目录，例如：
- `.git`
- `.obsidian`
- `.idea`
- `.config`
- 等等

### ✔ 执行结束自动删除临时 JSON 文件
- `attachment_rename_map.json`
- `file_path_index.json`

### ✔ 无第三方依赖（纯标准库）
可直接打包成 Windows EXE / macOS APP。

---

## 🚀 使用方式 Usage

### 1. 将 `md_link_fixer.py` 放在你的笔记根目录  
```text
your-notes/
   ├── md_link_fixer.py
   ├── note1.md
   ├── images/
   │   └── ...
   └── ...
```

### 2. 双击运行（或使用命令）
```yaml
python md_link_fixer.py
# 仅重命名图片是默认行为，可用 --rename-types 设置分类（image/video/audio/office/other 或 all）
python md_link_fixer.py --rename-types image office
python md_link_fixer.py --rename-types other          # 仅重命名非 Markdown 的其它文件
python md_link_fixer.py --rename-types all            # 全部非 Markdown
python md_link_fixer.py --data-dir D:\data\md-fixer   # 固化数据输出目录

# 启动界面模式
python md_link_fixer.py --ui
# UI 首次运行会要求填写文档项目路径与数据存放路径，并可为项目设置名称和分类。
```

### 3. 等待完成  
程序会输出：

- 附件重命名（如需要）
- Markdown 链接修复结果
- 自动删除临时 JSON 文件

---

## 🛠 打包为 EXE（可选）

```shell
# 命令行版（console）
pyinstaller --onefile --console --icon=assets/app.ico md_link_fixer.py
# 图形界面版（windowed）
pyinstaller --onefile --windowed --icon=assets/app.ico md_link_fixer_ui.py
```

---

## 🛠 打包为 macOS APP（可选）

```shell
pyinstaller --onefile --windowed --icon=icon.icns md_link_fixer_ui.py
```

---

## ⚠ 注意事项 Notes

- 本工具不会修改 `.md` 文件的文件名。
- 重命名分类仅作用于重命名步骤，Markdown 引用修复仍会扫描所有附件；支持 image / video / audio / office / other / all。
- 运行后会输出重复命名文件的 Markdown 表格报告，并在命令行模式下提示回车结束。
- 若 Markdown 文件名模糊匹配结果多于 1 个，为避免错误，将不会自动修复。
- 请确保你的 Markdown 文件名尽量具有唯一性。

### UI 更新
- 运行摘要页以表格展示重命名、修正、重名与统计信息，并附带运行元信息。
- 重命名分类支持下拉多选，可一次选择多类或“全部”。
- 系统设置与新增项目的路径选择按钮采用紧邻输入框的`...`样式。
- 项目列表卡片自适应宽度，避免右侧被遮挡。

---

## 📄 License
MIT License
