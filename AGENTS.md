# Repository Guidelines

## Project Structure & Module Organization
- `md_link_fixer.py` is the CLI/UI core; it scans from the directory where it lives, renames non-Markdown attachments (based on selected categories), and rewrites Markdown links. Keep utility functions near related logic (indexing, renaming, replacement) to preserve readability.
- `md_link_fixer_ui.py` boots the UI directly for packaging a windowed build.
- `assets/app.ico` is used when packaging to Windows executables; `.idea/` holds IDE metadata and should stay untracked in commits.
- Temporary files (`attachment_rename_map.json`, `file_path_index.json`) are generated and cleaned up automatically; if the process is interrupted, remove them before re-running.

## Build, Test, and Development Commands
- `python md_link_fixer.py [--rename-types image video audio office other|all] [--data-dir <path>]` — CLI run; default rename scope is images only. `other` means non-Markdown files not in the other groups.
- `python md_link_fixer.py --ui` — launch the Tkinter UI; first run asks for project root and data storage directory.
- `pyinstaller --onefile --console --icon=assets/app.ico md_link_fixer.py` — build the CLI exe (no external dependencies).
- `pyinstaller --onefile --windowed --icon=assets/app.ico md_link_fixer_ui.py` — build the windowed UI exe (adjust icon path for `.icns` on macOS).

## Coding Style & Naming Conventions
- Python 3, standard library only; use 4-space indentation and avoid tabs.
- Functions, variables, and filenames use `snake_case`; constants are `UPPER_SNAKE_CASE`.
- Prefer `os.path` helpers for portability and guard against hidden directories (`.`-prefixed) as the script already does.
- Logging should go through the existing `logging` setup; keep messages concise and action-oriented.

## Testing Guidelines
- No formal test suite yet. Validate changes by running `python md_link_fixer.py` against a small, disposable notes folder containing nested Markdown and attachments; confirm expected rename and link counts in the logs, and review the duplicate-file markdown table in the output/report.
- For complex changes, create a temporary fixture tree (e.g., `tests/fixtures/sample_note.md` plus attachments) and document manual steps in the PR description.

## Commit & Pull Request Guidelines
- Use short, imperative commit messages; Conventional Commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`) are preferred to clarify intent.
- PRs should explain the problem, the approach, and manual validation steps (`python md_link_fixer.py` run on a sample). Include before/after link examples or log excerpts if behavior changes.
- Keep diffs minimal—avoid formatting churn and unrelated file edits, especially within generated or IDE files.

## Security & Safety Notes
- The tool rewrites files in place; work on a copy of your notes or ensure version control before running.
- Hidden directories (e.g., `.git`, `.obsidian`) are skipped by default; retain that safeguard for new logic.
