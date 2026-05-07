# Publishing Checklist

Use this checklist before making the repo public on GitHub or Hugging Face.

## 1. Sanity Check

```bash
python -m unittest discover -s tests -v
rg -n "api[_-]?key|secret|token|password|Bearer|C:\\\\Users|\\.env|credential" -S . --glob "!.venv/**" --glob "!.runtime/**" --glob "!.pytest_cache/**"
```

Expected result: tests pass, and the secret scan returns no real credentials or personal absolute paths.

## 2. GitHub

```bash
git init
git add README.md PUBLISHING.md REFACTORING_SUMMARY.md requirements.txt mcp.json.example .env.example .gitignore .hfignore mcp_server scripts tests web_research
git commit -m "Publish live-only web research MCP"
gh repo create lmstudio-web-research-mcp --public --source . --remote origin --push
```

If you prefer a different repo name, replace `lmstudio-web-research-mcp`.

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
