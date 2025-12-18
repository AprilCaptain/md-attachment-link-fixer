# Repository Guidelines

## Project Structure & Module Organization
- `md_link_fixer/core.py`: Core pipeline for scanning a notes root, renaming non-Markdown attachments (`yyyyMMddHHmmssSSS##.<ext>`), repairing Markdown links, and writing reports/temp files.
- `md_link_fixer/ui.py`: PySide6 GUI (project list, form editing, async runs, log capture, summary tables).
- `md_link_fixer.py`: CLI entry that dispatches to `run_pipeline` or launches the PySide6 UI (`--ui`).
- `md_link_fixer_ui.py`: Thin wrapper that calls the PySide6 UI launcher.
- `assets/app.ico`: Icon used for the packaged UI.
- Temporary runtime files (`attachment_rename_map.json`, `file_path_index.json`) are created next to the executable or in `--data-dir` and are auto-removed when the run finishes.

## Build, Test, and Development Commands
- Run in the current folder: `python md_link_fixer.py` (defaults to renaming images only; uses stdlib pipeline).
- Override scope: `python md_link_fixer.py --root D:\notes --rename-types image office` or use `all` to rename every non-Markdown attachment.
- Persist temp files elsewhere: `python md_link_fixer.py --data-dir D:\data\md-fixer`.
- GUI mode (PySide6): `python md_link_fixer.py --ui` (or `python md_link_fixer_ui.py`); set the project path in the first launch dialog.
- Package (console): `pyinstaller --onefile --console --icon=assets/app.ico md_link_fixer.py`
- Package (GUI): `pyinstaller --onefile --windowed --icon=assets/app.ico md_link_fixer_ui.py`
- Use `--verbose` while developing to see detailed matching and renaming logs.

## Coding Style & Naming Conventions
- Python 3; pipeline stays stdlib-only, UI relies on PySide6. Keep imports stdlib-first, then PySide6 for GUI files.
- Follow PEP 8 with 4-space indentation; prefer descriptive function names and typed signatures where practical.
- Keep CLI flags synchronized between the parser and README/AGENTS docs. When changing rename rules, update `RENAMING_CATEGORIES`/`CATEGORY_LABELS` together.
- Strings and logs should stay concise; user-facing messages should be bilingual-safe (ASCII plus UTF-8).

## Testing Guidelines
- No automated test suite yet; perform manual runs on a small sample tree such as:
  ```
  sample-notes/
    note.md
    images/pic 1.png
    docs/manual.pdf
  ```
  Run with `--verbose` and confirm: attachments are renamed once, Markdown links are rewritten, hidden folders (e.g., `.git`, `.idea`) stay untouched, and temp JSON files are cleaned up.
- For UI changes, launch `--ui`, configure a test project, and verify the run/summary cards render and buttons respond.

## Commit & Pull Request Guidelines
- Commits: short, imperative subjects (e.g., `Fix markdown link resolution for moved files`); group related changes together.
- PRs should include: purpose and scope, key flags used during validation (`--rename-types`, `--data-dir`), screenshots for UI tweaks, and a brief checklist of manual tests run.
- Link issues when available and mention any known limitations (e.g., skipped ambiguous matches) in the description.

## Security & Configuration Tips
- The tool skips hidden directories by default; avoid passing system roots as `--root` to reduce accidental scans.
- If running from a packaged binary, prefer `--data-dir` on a writable path to keep temp artifacts out of version control.
