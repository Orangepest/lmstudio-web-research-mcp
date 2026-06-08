# Publishing Checklist

Use this checklist before making the repo public on GitHub or Hugging Face.

For clone-to-new-machine setup, see [Transfer Guide](docs/TRANSFER.md).

## 1. Sanity Check

```bash
python scripts/github_publish_check.py
```

Expected result: tests pass, runtime folders are ignored, and the safety scan returns no real credentials or personal absolute paths.

## 2. GitHub

If `origin` already points to Hugging Face, keep it and use a separate `github` remote.

Private-first publish:

```bash
git status --short
python scripts/github_publish_check.py
git add README.md PUBLISHING.md REFACTORING_SUMMARY.md NEXT_ACTIONS.md docs evals requirements.txt requirements-lock.txt mcp.json.example .env.example .gitignore .hfignore mcp_server scripts tests web_research
git diff --cached --stat
git commit -m "Prepare transferable LM Studio research MCP"
gh repo create lmstudio-web-research-mcp --private --source . --remote github --push
```

If you prefer a different repo name, replace `lmstudio-web-research-mcp`. Use `--public` only after reviewing the staged diff and confirming that public release is intended.

Existing GitHub repo:

```bash
git remote add github https://github.com/<user>/lmstudio-web-research-mcp.git
git push -u github main
```

## 3. Hugging Face

This project is best published on Hugging Face as a code artifact first. A runnable Space can be added later with a Dockerfile or app wrapper.

```bash
hf auth whoami
hf repos create USERNAME/lmstudio-web-research-mcp --type model --exist-ok
hf upload USERNAME/lmstudio-web-research-mcp . --type model --exclude ".env" --exclude ".venv/*" --exclude ".runtime/*" --exclude ".git/*" --exclude ".pytest_cache/*" --commit-message "Publish web research MCP"
```

Replace `USERNAME` with your Hugging Face namespace. If you want a runnable Space later, add a Dockerfile or app wrapper and publish it as a Space.

## 4. Public Positioning

Suggested tagline:

> A local-first MCP web research server for LM Studio: live search, page reading, citations, blocked-source handling, and safe source recovery.

Do not include `.env`, browser profiles, logs, local databases, or runtime caches in any public upload.
