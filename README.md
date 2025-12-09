# md_link_fixer  
### Markdown Attachment Renamer & Link Auto-Fixer  
### Markdown 附件重命名 & 路径自动修复工具

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
```

### 3. 等待完成  
程序会输出：

- 附件重命名（如需要）
- Markdown 链接修复结果
- 自动删除临时 JSON 文件

---

## 🛠 打包为 EXE（可选）

```shell
pyinstaller --onefile --console --icon=icon.ico md_link_fixer.py
```

---

## 🛠 打包为 macOS APP（可选）

```shell
pyinstaller --onefile --windowed --icon=icon.icns md_link_fixer.py
```

---

## ⚠ 注意事项 Notes

- 本工具不会修改 `.md` 文件的文件名。
- 若 Markdown 文件名模糊匹配结果多于 1 个，为避免错误，将不会自动修复。
- 请确保你的 Markdown 文件名尽量具有唯一性。

---

## 📄 License
MIT License