# Transfer Guide

Use this when cloning the project onto another machine or preparing it for GitHub.

## What Transfers

- Source code under `mcp_server/`, `web_research/`, and `scripts/`.
- Tests under `tests/` and deterministic eval fixtures under `evals/`.
- Portable examples: `.env.example`, `mcp.json.example`, `requirements.txt`, and `requirements-lock.txt`.
- Operator docs: `README.md`, `NEXT_ACTIONS.md`, `PUBLISHING.md`, and `docs/lmstudio-research-mode.md`.

## What Does Not Transfer

The repo intentionally ignores runtime state:

- `.runtime/` research runs, logs, queues, work-loop output, browser profiles, exports, and generated prompts.
- `.venv/` local Python virtual environments.
- `.env` local environment overrides.
- `.DS_Store`, cache folders, coverage output, build output, and local databases.

If you need to share completed research, export it first:

```bash
python scripts/export_research_run.py <run_id> --profile private-share --output-dir .runtime/exports
python scripts/build_source_pack.py <run_id> --profile private-share --output-dir .runtime/source_packs/private-share
```

Those exports are still local artifacts. Review them before sending anywhere.

## Fresh Clone Setup

### Windows One-Command Install

Open PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://raw.githubusercontent.com/Orangepest/lmstudio-web-research-mcp/main/scripts/install_windows.ps1 | iex"
```

This downloads the repo, creates `.venv`, installs requirements, installs Playwright Chromium, writes `%USERPROFILE%\.lmstudio\mcp.json`, validates the config, and smoke-tests the MCP server. Restart LM Studio after it finishes.

Requirements: Python 3.12+ must be installed. Git is optional; if Git is missing, the installer downloads the GitHub ZIP instead.

If the one-liner fails, run this manual version so the error is visible:

```powershell
git clone https://github.com/Orangepest/lmstudio-web-research-mcp.git "$env:USERPROFILE\mcp-servers\lmstudio-web-research-mcp"
cd "$env:USERPROFILE\mcp-servers\lmstudio-web-research-mcp"
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1 -SkipClone
```

### Manual Setup

```bash
git clone <github-url> lmstudio-web-research-mcp
cd lmstudio-web-research-mcp

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
python scripts\merge_lmstudio_mcp.py "$env:USERPROFILE\.lmstudio\mcp.json" --research-dir "$PWD" --platform windows --apply
python scripts\validate_lmstudio_mcp.py "$env:USERPROFILE\.lmstudio\mcp.json" --research-dir "$PWD" --platform windows
```

## LM Studio Setup

Copy `mcp.json.example` into LM Studio's MCP config and replace the placeholder paths.

macOS and Linux command example:

```json
{
  "command": "/ABSOLUTE/PATH/TO/lmstudio-web-research-mcp/.venv/bin/python",
  "cwd": "/ABSOLUTE/PATH/TO/lmstudio-web-research-mcp"
}
```

Windows command example:

```json
{
  "command": "C:/ABSOLUTE/PATH/TO/lmstudio-web-research-mcp/.venv/Scripts/python.exe",
  "cwd": "C:/ABSOLUTE/PATH/TO/lmstudio-web-research-mcp"
}
```

Keep the default transfer-safe tool settings unless you deliberately want the larger tool surface:

```text
MCP_TOOL_PROFILE=agent_strict
MCP_EXPOSE_ADVANCED_TOOLS=false
MCP_COMPACT_RESULTS=true
MCP_RESULT_EXCERPT_CHARS=3500
MCP_RESULT_MAX_ITEMS=4
```

After editing the LM Studio config, restart LM Studio and run:

```bash
python scripts/research_stack_status.py --probe-tools
```

Expected result:

```text
Research stack status: OK
```

## Research Prompt

Generate the LM Studio system prompt on each machine instead of committing the generated file:

```bash
python scripts/show_research_preset.py --no-prompt
```

The generated prompt is written to `.runtime/lmstudio-research-system-prompt.txt`.

## GitHub Safety Check

Before pushing a public repo:

```bash
python scripts/github_publish_check.py
```

For a quick safety scan without running the full test suite:

```bash
python scripts/github_publish_check.py --skip-tests
```

The check verifies that common runtime/private paths are ignored, visible untracked files do not include runtime state, text files do not contain obvious secrets, and tests pass unless skipped.

## GitHub Remote Layout

This checkout may already use `origin` for Hugging Face. Add GitHub as a separate remote:

```bash
git remote add github https://github.com/<user>/lmstudio-web-research-mcp.git
git push -u github main
```

If the GitHub repo does not exist yet and GitHub CLI is authenticated:

```bash
gh repo create lmstudio-web-research-mcp --private --source . --remote github --push
```

Use `--public` only after `python scripts/github_publish_check.py` passes and you have reviewed the staged diff.
