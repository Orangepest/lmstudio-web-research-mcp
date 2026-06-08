# LM Studio Web Research MCP

Assistant-style web access for local LM Studio models. This server gives models a small online research toolkit: search the open web, read pages or PDF URLs, and collect citation-ready evidence from top results.

This is not a crawler, local file tool, memory tool, or permanent RAG index. It uses free no-key search pages, direct HTTP fetches, optional Chromium rendering, PDF URL extraction, query-focused passage ranking, link discovery, and process-local session caching.

## MCP Tools

- `safe_web_search(query)`
  - One-parameter search wrapper for local models that are brittle at XML tool calls.
- `safe_repair_tool_call(raw)`
  - One-parameter helper that rewrites common malformed LM Studio XML tool calls into the closest safe one-parameter call.
- `safe_research_agent(request)`
  - Single one-parameter research entrypoint. Routes a plain request to search, read, research, runtime, campaign, synthesis, or director internally. Heavy "deep research", "report", "giga", "huge", "due diligence", and market-scan requests route to the background runtime by default to avoid LM Studio tool-call timeouts.
- `safe_read_url(url)`
  - One-parameter URL reader using conservative defaults.
- `safe_research(query)`
  - One-parameter research wrapper using conservative local-model defaults.
- `safe_deep_research(question)`
  - One-parameter deep research wrapper using compact output, `breadth=3`, `read_top_per_query=1`, `follow_up_rounds=1`, `render=false`, and `report_format="executive_brief"`.
- `safe_research_mission(request)`
  - One-parameter mission orchestrator that applies a work profile, runs deep research, checks a quality gate, and can optionally export or source-pack the completed run.
- `safe_research_runtime(request)`
  - One-parameter background mission runtime. Previews by default; can submit queued missions, start/stop the local worker, and poll jobs/checkpoints/runs from one status surface.
- `safe_research_campaign(request)`
  - One-parameter multi-job campaign planner. Previews by default; can decompose one objective into several queued background research jobs when `apply=true` and `queue=true`.
- `safe_research_director(request)`
  - One-parameter autonomous research director. Previews by default; can create a campaign, discover prior related runs and source memory, inspect quality gates, spawn bounded follow-up jobs, run bounded wave cycles, stop when budget is exhausted, and synthesize/export when ready with `apply=true`.
- `safe_synthesize_research_campaign(request)`
  - One-parameter campaign synthesis/export tool. Previews by default; writes a final campaign dossier plus source, claim, audit, and manifest indexes when `apply=true`. Add `local_synthesis=true` to request an optional local-model rewrite with deterministic fallback.
- `safe_resume_deep_research(run_id)`
  - One-parameter resume wrapper for interrupted deep research runs.
- `web_search(query, max_results=10, freshness=None, site=None)`
  - Searches the open web and returns normalized `title`, `url`, `source`, `snippet`, `rank`, `provider`, and `backend_attempts` telemetry.
- `read_url(url, query=None, render=False)`
  - Reads one HTTP/HTTPS page or PDF URL and returns `final_url`, `requested_url`, `access_strategy`, `status_code`, `content_type`, `title`, `summary`, `text`, `links`, `evidence`, `fetched_at`, `content_hash`, and `snapshot`. Captcha or anti-bot blocks return `ok=false`, `blocked=true`, `block_type`, and `block_marker`.
- `discover_links(url, query=None, render=False, file_types=None, limit=50)`
  - Pulls links and online files from a page. Use `file_types=["pdf"]` or similar when the model needs source documents.
- `research_web(query, max_results=8, read_top=4, freshness=None, site=None, render=False, report_format="long_report")`
  - Searches, reads top unique results, ranks evidence, and returns `sources`, `source_quality`, `research_quality`, `evidence`, `claims`, `claim_review`, `reports`, `final_report`, `citation_validation`, `recommended_next_searches`, `uncertainties`, `recent_changes`, `citations`, `selection_trace`, structured `failures`, `blocked_sources`, and `manual_visit_links`.
- `deep_research(question, breadth=3, read_top_per_query=1, freshness=None, render=False, report_format="executive_brief", follow_up_rounds=1)`
  - Runs several planned searches, merges and dedupes sources/evidence, optionally runs autonomous gap-follow-up searches, and returns a research dossier with checkpoints for resume.
- `list_research_runs(limit=20)`
  - Lists recent persisted research jobs with run IDs, timestamps, source counts, evidence counts, and claim counts.
- `safe_work_loop_status(request)`
  - One-parameter status reader for unattended work loops. Use `active`, `latest`, `limit=N`, or `loop_id: <id>` to inspect loop progress and recent events.
- `safe_cleanup_work_loops(request)`
  - One-parameter stale loop cleanup. Previews by default; writes require `loop_id: <id>` plus `apply=true`, and legacy missing-PID cleanup additionally requires `include_legacy_missing_pid=true`.
- `safe_submit_research_job(request)`
  - One-parameter research job submitter. Previews by default; writes require `submit=true` or `apply=true`.
- `safe_research_job_status(request)`
  - One-parameter research job status reader for queued, running, completed, failed, or cancelled jobs.
- `safe_cancel_research_job(request)`
  - One-parameter research job canceller. Requires an explicit `job_id: <id>`.
- `safe_research_checkpoint_status(request)`
  - One-parameter status reader for resumable `deep_research` checkpoints.
- `safe_interrupt_research_checkpoints(request)`
  - One-parameter checkpoint interrupter. Previews by default; writes require explicit `run_id` values plus `apply=true`.
- `safe_list_research_runs(request)`
  - One-parameter recent-run list using a fixed limit of 10. The request text is only a label.
- `safe_find_research_runs(query)`
  - One-parameter prior-run search for follow-up workflow.
- `safe_research_context(query)`
  - One-parameter automatic prior-context loader. Use at the start of a fresh chat when the user asks to continue previous research and no run ID is visible.
- `find_research_runs(query, limit=5)`
  - Finds prior research runs relevant to a query so follow-up questions can continue without requiring the user to paste a run ID.
- `safe_get_research_run(run_id)`
  - One-parameter compact reader for a persisted research run.
- `safe_export_research_run(request)`
  - One-parameter exporter for saved research runs. Use a run ID, comma-separated/bulleted run IDs, `latest=N` or `latest: N`, and `find=query` or `find: query`; duplicates are ignored, and optional lines include `redact=true`, `profile=private-share`, `zip=true`, and `dry_run=true`.
- `safe_build_source_pack(request)`
  - One-parameter source-pack builder for saved research runs. Defaults to redacted output for sharing; use run IDs, `latest=N` / `latest: N`, or `find=query` / `find: query`; duplicates are ignored, and `dry_run=true` previews without writing files.
- `get_research_run(run_id)`
  - Loads a persisted research job, including its sources, evidence, claims, strategy, and run metadata.
- `invalidate_research_cache(max_age_seconds=None, content_hash=None, clear_all=False)`
  - Removes cached search/read results by age, content hash, or full cache clear.
- `resume_deep_research(run_id)`
  - Resumes an interrupted `deep_research` checkpoint from its next unfinished planned query.
- `continue_research_run(run_id, follow_up_query, max_results=8, read_top=3, freshness=None, render=False, report_format="long_report")`
  - Loads a prior run, runs a focused follow-up search, merges and dedupes sources/evidence, recomputes claims, and saves a linked child run.
- `safe_continue_research_run(request)`
  - One-parameter continuation wrapper. Put `run_id` on the first line and the follow-up query on following lines.

By default, the live MCP server exposes only `safe_*` one-parameter tools to LM Studio. Set `MCP_TOOL_PROFILE=agent_strict` to expose only `safe_research_agent(request)` for brittle local models, or `MCP_TOOL_PROFILE=agent` to expose `safe_research_agent(request)` plus `safe_repair_tool_call(raw)`. This prevents local models from choosing brittle multi-parameter XML calls like `web_search(query, max_results, ...)`. Set `MCP_EXPOSE_ADVANCED_TOOLS=true` only if you deliberately want the advanced multi-parameter tools exposed in LM Studio.

## Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m mcp_server.server
```

Default HTTP endpoint:

```text
http://127.0.0.1:8000/mcp
```

For command-launched MCP clients, use `MCP_TRANSPORT=stdio` as shown in [mcp.json.example](mcp.json.example).

For cloning this project onto another machine or preparing a GitHub release, see [Transfer Guide](docs/TRANSFER.md).

## Recommended Model Flow

1. Use `safe_research_agent` for most LM Studio sessions; it routes the request internally while keeping one stable XML shape.
2. Use `safe_research` for most online questions when the full safe tool profile is exposed.
3. Use `safe_research_runtime` for long background research that should survive LM Studio tool-call timeouts. In `agent_strict`, `safe_research_agent` routes heavy report/deep-research wording there automatically.
4. Use `safe_research_campaign` when one objective should become several background research missions.
5. Use `safe_research_director` for the biggest jobs where the system should plan a campaign, monitor quality, create follow-ups, and synthesize when ready.
6. Use `safe_synthesize_research_campaign` after campaign subjobs complete and the user wants one final campaign dossier/export bundle.
7. Use `safe_research_mission` for serious research, reports, handoffs, or anything that should run immediately with a profile and quality gate.
8. Use `safe_deep_research` for broad, high-stakes, comparative, or multi-source work when mission packaging is not needed.
9. Use `safe_web_search` when the model needs to inspect candidate URLs first.
10. Use `safe_read_url` for a specific source the user or search results provide.
11. Use `safe_resume_deep_research` for interrupted deep research runs.
12. Use `safe_work_loop_status` when the user asks whether unattended work is still running or what the latest loop did.
13. Use `safe_cleanup_work_loops` only after previewing stale loops and only when the user wants stale loop summaries closed.
14. Use `safe_submit_research_job`, `safe_research_job_status`, and `safe_cancel_research_job` for low-level queued mission planning and queue control.
15. Use `safe_research_checkpoint_status` and `safe_interrupt_research_checkpoints` to inspect or deliberately close stale resumable checkpoints.
16. Use `safe_research_context` first when a fresh chat should automatically recover relevant prior research context.
17. Use `safe_list_research_runs`, `safe_find_research_runs`, and `safe_get_research_run` when continuing or auditing prior work.
18. Use `safe_export_research_run` or `safe_build_source_pack` when the user asks to package, share, hand off, or archive a completed run.
19. Use `safe_continue_research_run` for follow-up research when you have a run ID and a new query.
20. Advanced multi-parameter tools are hidden by default. Enable `MCP_EXPOSE_ADVANCED_TOOLS=true` only when the user explicitly needs freshness, site filters, rendering, or non-default report formats.

For LM Studio preset instructions, example prompts, and tool-selection guidance, see [LM Studio Research Mode](docs/lmstudio-research-mode.md).

To write the exact system prompt to a reusable file and print the macOS clipboard command:

```bash
python scripts/show_research_preset.py --no-prompt
pbcopy < .runtime/lmstudio-research-system-prompt.txt
```

Use `python scripts/show_research_preset.py --copy` to write the file and copy the prompt to the clipboard in one step.

## Queued Research Jobs

Use `safe_submit_research_job` from LM Studio to preview or queue serious research without running it inside the MCP request. Queued jobs are written under `.runtime/research_jobs`.

For the unified background runtime, use:

```text
safe_research_runtime("status")
safe_research_runtime("Compare local deep research tools\nsubmit=true\nstart_worker=true\napply=true")
safe_research_runtime("start_worker=true\napply=true")
safe_research_campaign("Compare local deep research tools\ndepth=deep\nqueue=true\napply=true")
safe_research_director("objective: Compare local deep research tools\ndepth=deep\nbudget_jobs=12\nquality_target=strong\napply=true")
safe_research_director("director_id: <director-id>\naction: autopilot\nstart_worker=true\nmax_iterations=8\nmax_cycles=2\napply=true")
safe_research_director("director_id: <director-id>\naction: autopilot\npolicy=balanced\nstart_worker=true\nmax_iterations=8\napply=true")
safe_research_director("director_id: <director-id>\naction: autopilot_status")
safe_research_director("director_id: <director-id>\naction: wave\nstart_worker=true\nmax_cycles=3\napply=true")
safe_research_director("director_id: <director-id>\naction: dashboard\napply=true")
safe_research_director("director_id: <director-id>\naction: graph\napply=true")
safe_research_director("director_id: <director-id>\naction: graph_actions\ngraph_action=weak_claims\napply=true")
safe_research_director("director_id: <director-id>\naction: runbook\napply=true")
safe_research_director("director_id: <director-id>\naction: runbook_export\nprofile=private-share\napply=true")
safe_research_director("director_id: <director-id>\naction: compare_bundles\nleft=<path>\nright=<path>\napply=true")
safe_research_director("director_id: <director-id>\naction: comparison_actions\nleft=<path>\nright=<path>\nmax_actions=3\napply=true")
safe_research_director("director_id: <director-id>\naction: comparison_replay\nevent_id=<comparison-event-id>\nmax_actions=2\napply=true")
safe_research_director("director_id: <director-id>\naction: recovery\nstale_hours=24")
safe_research_director("director_id: <director-id>\naction: recovery\npolicy=aggressive")
safe_synthesize_research_campaign("campaign_id: <campaign-id>\napply=true")
safe_synthesize_research_campaign("campaign_id: <campaign-id>\nlocal_synthesis=true\napply=true")
```

The runtime is preview-first unless `apply=true` is present. It reports worker state, queued/running/completed job counts, resumable checkpoints, and recent saved runs.
Campaigns are also preview-first. They create a `.runtime/research_campaigns/<campaign-id>/campaign.json` plan and tag each queued job with `campaign:<campaign-id>` so status can reconnect the subjobs. The research director adds a higher-level state file under `.runtime/research_directors/<director-id>/director.json`, watches campaign quality gates, and can create follow-up jobs tagged with `director:<director-id>`. Director preview/create also builds `objective_memory` from prior related saved runs, including reusable source URLs and blocked/failed paths to avoid; follow-up jobs include those hints automatically. Gate decisions are `wait`, `continue`, `synthesize`, or `stop_budget_exhausted` based on completed steps, quality scores, source/claim coverage, primary-source gaps, contradiction handling, and remaining job budget. `action: autopilot` runs a bounded persistent autonomy loop, optionally starts a detached worker, runs repeated waves, writes dashboard/runbook artifacts, records `.runtime/research_directors/<director-id>/autopilots/<autopilot-id>/autopilot.json`, and stops when it needs worker output, synthesizes, exhausts budget, or hits `max_iterations`. Add `policy=balanced|aggressive` to make each autopilot iteration run director recovery first; balanced reviews stale state and surfaces checkpoint/worker recovery, while aggressive can cancel stale director jobs before the next wave. `action: autopilot_status` loads the latest or requested autopilot ledger, and `action: autopilots` lists prior ledgers. `action: wave` runs bounded director cycles, can start/observe the worker, writes `.runtime/research_directors/<director-id>/waves/<wave-id>/wave.json` when applied, and stops when the gate says to wait, synthesize, or stop. `action: dashboard` writes `.runtime/research_directors/<director-id>/dashboard/dashboard.md` and `dashboard.json` with gate state, campaign progress, wave history, follow-ups, synthesis outputs, and graph insights for central claims, weak evidence claims, repeated domains, unresolved contradictions, and next graph actions. `action: graph` writes `.runtime/research_directors/<director-id>/evidence_graph/evidence_graph.json` and `index.md` linking director, campaign steps, jobs, runs, sources, claims, contradictions, waves, recovery reviews, memory sources, avoid paths, and synthesis outputs. `action: graph_actions` previews or queues targeted follow-up jobs from graph recommendations; use `graph_action=weak_claims`, `graph_action=contradictions`, `graph_action=domains`, or omit it for all. `action: runbook` writes `.runtime/research_directors/<director-id>/runbook/runbook.json` and `runbook.md` with dashboard, graph, recovery, graph-action preview, synthesis status, and exact next commands for operator handoff. `action: runbook_export` writes a checksummed export bundle from the runbook; `profile=private-share` redacts URLs and local paths and `profile=full-fidelity` keeps complete artifacts, with `archive=true` by default. `action: compare_bundles` diffs two runbooks, manifests, export directories, or evidence graphs for new/removed claims, source coverage changes, resolved/new contradictions, domain changes, and remaining gaps. `action: comparison_actions` previews or queues follow-up jobs from a bundle diff for newly introduced gaps, unresolved contradictions, and lost source coverage; use `comparison_action=gaps|contradictions|sources` or omit it for all. `action: comparison_replay` requeues stronger follow-ups from a prior comparison-action `event_id`, useful after failed or no-evidence follow-ups. `action: recovery` previews stale/problem waves, failed worker starts, stale director-tagged jobs, and interrupted campaign checkpoints; with explicit apply options it can mark wave recovery review and cancel stuck director jobs. Recovery policies are `conservative`, `balanced`, and `aggressive`: conservative reviews only, balanced can restart the worker and surface checkpoint resume actions, and aggressive can also cancel stale director jobs. After subjobs complete, synthesize the campaign to write a final dossier under `.runtime/mcp_campaign_syntheses/<campaign-id>/`. `local_synthesis=true` asks the configured local LM Studio model to polish the final dossier; invalid rewrites are rejected and the deterministic dossier is kept.

Run queued jobs from a terminal with the separate worker process:

```bash
python scripts/research_job_worker.py --max-jobs 1 --json
python scripts/research_mission_runtime.py "status"
python scripts/research_mission_runtime.py "Compare local research agents\nsubmit=true\nstart_worker=true" --apply
python scripts/research_campaign.py plan "Compare local research agents" --depth deep --queue --json
python scripts/research_director.py "objective: Compare local research agents\ndepth=deep\nbudget_jobs=12\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: autopilot\nstart_worker=true\nmax_iterations=8\nmax_cycles=2\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: autopilot\npolicy=balanced\nstart_worker=true\nmax_iterations=8\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: autopilot_status" --json
python scripts/research_director.py "director_id: <director-id>\naction: advance\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: wave\nstart_worker=true\nmax_cycles=3\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: dashboard\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: graph\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: graph_actions\ngraph_action=contradictions\nmax_actions=3\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: runbook\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: runbook_export\nprofile=private-share\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: compare_bundles\nleft=<path>\nright=<path>\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: comparison_actions\nleft=<path>\nright=<path>\nmax_actions=3\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: comparison_replay\nevent_id=<comparison-event-id>\nmax_actions=2\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: recovery\nstale_hours=24\ncancel_stuck_jobs=true\napply=true" --json
python scripts/research_director.py "director_id: <director-id>\naction: recovery\npolicy=aggressive\nstart_worker=false\napply=true" --json
python scripts/research_campaign.py synthesize <campaign-id> --apply --json
python scripts/research_campaign.py synthesize <campaign-id> --local-synthesis --apply --json
```

For a persistent polling worker with status/stop controls:

```bash
python scripts/research_job_worker_control.py start --json
python scripts/research_job_worker_control.py status --json
python scripts/research_job_worker_control.py stop --json
```

To launch it inside tmux instead of as a detached process:

```bash
python scripts/research_job_worker_control.py start --tmux --json
```

Useful queue commands:

```bash
python scripts/research_jobs.py --json list
python scripts/research_jobs.py --json list --status queued
python scripts/research_jobs.py --json add "Compare current local AI research assistants" --profile careful --priority 2 --tag local-ai
```

The worker leases one job at a time, marks it `running`, records the resulting `run_id`, and marks the job `completed` or `failed`. Expired `leased` or `running` jobs can be reclaimed by a later worker after their lease expires.

## What Was Missing

- Search needed no-key providers that are less challenge-prone than DuckDuckGo alone. The tool now tries local SearXNG HTML first using `SEARXNG_ENGINES=google` by default for low-latency local results, can use local SearXNG JSON when configured, then falls back through Brave HTML and DuckDuckGo Lite. `SEARCH_PROVIDERS` can override provider order, `SEARXNG_ENGINES` / `SEARXNG_ENABLED_ENGINES` / `SEARXNG_DISABLED_ENGINES` can tune the local metasearch mix, `SEARCH_TIMEOUT` caps blocked search-provider stalls separately from page-read timeouts, and blocked public providers are skipped temporarily after 403/429/timeout failures. `SEARCH_SIMILAR_CACHE=true` reuses cached results for common local-model follow-up suffixes such as "official source", "latest", and "additional evidence".
- Search debugging needed backend telemetry. Search results now include provider attempt status, result counts, latency, and fallback messages.
- Source selection needed to stop blindly reading the first few search results. Research now plans reads across the inspect window, reserves room for official/docs/government/repository candidates, and avoids repeated blocked/duplicate domains before trying alternate sources.
- Models needed source discovery, not just text extraction. `read_url` now returns page links, and `discover_links` can filter for PDFs and other online files.
- Research results needed to preserve the search result attached to each fetched source, so models can explain why a source was opened.
- Research output now includes local evidence-derived claims, uncertainty notes, and recent-change notes so reports are not just raw source snippets.
- The project needed to stop carrying old crawler/index/database files and settings.

## Configuration

```text
WEB_RESEARCH_LOG_PATH=.runtime/web_research.log
RESEARCH_RUNS_DIR=.runtime/research_runs
ALLOWED_DOMAINS=
USER_AGENT=Mozilla/5.0 ...
REQUEST_TIMEOUT=25
MAX_CONTENT_CHARS=120000
FETCH_DOMAIN_DELAY_SECONDS=0
FETCH_BLOCK_BACKOFF_SECONDS=3
DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS=35
SEARXNG_URL=http://127.0.0.1:8888
CACHE_TTL_SECONDS=3600
CACHE_MAX_ITEMS=256
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8000
BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=30000
BROWSER_MAX_CONTENT_CHARS=60000
BROWSER_INTERACTION=true
BROWSER_SCROLL_STEPS=4
BROWSER_LOCALE=en-US
BROWSER_TIMEZONE_ID=Asia/Calcutta
BROWSER_PROFILE_DIR=
MCP_COMPACT_RESULTS=false
MCP_RESULT_EXCERPT_CHARS=12000
MCP_RESULT_MAX_ITEMS=8
LOCAL_LLM_CONTRADICTION_REVIEW=false
LOCAL_LLM_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_LLM_MODEL=auto
LOCAL_LLM_TIMEOUT=8
LOCAL_LLM_REPORT_SYNTHESIS=false
LOCAL_LLM_REPORT_MAX_TOKENS=1800
```

`freshness` supports `day`, `week`, `month`, and `year` when the underlying free search page honors the filter. `ALLOWED_DOMAINS` is optional and supports comma-separated wildcard patterns. `BROWSER_PROFILE_DIR` is optional; leave it empty for per-request browser profiles. Set `MCP_COMPACT_RESULTS=true` for memory-constrained local models or very large research runs. The MCP will still save complete `run.json` and `report.md` artifacts, but tool responses sent back to LM Studio contain bounded excerpts, counts, source IDs, and artifact paths instead of the full dossier.

Set `FETCH_DOMAIN_DELAY_SECONDS` to add a polite in-process delay between uncached reads to the same domain. `FETCH_BLOCK_BACKOFF_SECONDS` pauses repeated uncached reads to a domain after a blocked/captcha/HTTP 401/403/429 response. Cached reads do not wait. `DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS` makes multi-query deep research return a resumable checkpoint before LM Studio's tool-call window is likely to cancel the request.

`research_web`, `deep_research`, and `continue_research_run` persist completed jobs under `RESEARCH_RUNS_DIR` and return `run_id`, `run_path`, `final_report_path`, `reports`, `final_report`, `report_format`, `research_quality`, `evidence_index`, `claim_support`, and `persistence`. Reports include `quick_answer`, `source_table`, `executive_brief`, `long_report`, and `comparison_matrix`; `report_format` selects which one is copied into `final_report`. The per-run `evidence_index` chunks selected source text, scores chunks against the research query, and feeds a `Best Evidence` report section so synthesis is grounded in the strongest source passages instead of only raw page blobs. `claim_support` maps extracted claims back to matching indexed chunks, flags claims with no indexed support, and adds claim-support sections plus indexed-evidence columns to reports. Source-claim contradiction tables show disputed claims, supporting sources, conflicting sources, and contradiction-resolution follow-up queries. `deep_research` passes each planned query intent into child source selection, so official, documentation, repository, freshness, counterpoint, contradiction-resolution, and data searches reserve reads for sources that match that intent before generic fallback ranking. Coverage reports now score source quality per intent, exposing low-quality intents, matched-source counts, reliability signals, and an average intent quality score in the report coverage audit. Contradicted claims produce structured contradiction retrieval plans with claim IDs and `contradiction_resolution` intent so follow-up rounds prioritize independent verification, official clarification, and corrections/caveats. `deep_research` defaults to a local-model-safe profile: `breadth=3`, `read_top_per_query=1`, `report_format="executive_brief"`, `follow_up_rounds=1`, and `render=false`. `research_quality` gives a deterministic `weak`, `thin`, `moderate`, or `strong` label with score, strengths, and gaps. `deep_research` can use `follow_up_rounds=0-3` to run gap-follow-up searches after the initial plan, and records those searches in `strategy.auto_follow_up_plan`. Use `0` for speed, `1` for serious research, `2` for deeper coverage, and `3` rarely. Saved runs with a report also write `report.md` and expose `final_report_path`. `deep_research` checkpoints progress after each planned query; use `resume_deep_research` for interrupted runs. Use `find_research_runs`, `list_research_runs`, `get_research_run`, and `continue_research_run` when a local model needs to audit or continue from prior research. Cached search/read results are process-local and can be invalidated with `invalidate_research_cache`.

In compact mode, research outputs still include bounded `blocked_sources`, `manual_visit_links`, `report_synthesis`, and artifact paths. `safe_read_url` also caps returned `links` while reporting the full `link_count` and omitted count.

Run summaries and `safe_get_research_run` include `suggested_actions` so a local model can choose the next safe tool: resume interrupted `deep_research` jobs with `safe_resume_deep_research`, or extend completed runs with `safe_continue_research_run`. `safe_research_context` combines prior-run search, compact run loading, and a suggested next safe request so a fresh LM Studio chat can recover context automatically without making the user paste a run ID.

To preview archival cleanup for old persisted runs:

```bash
python scripts/archive_research_runs.py --keep-latest 50 --older-than-days 30
python scripts/archive_research_runs.py --keep-latest 50 --older-than-days 30 --apply
```

The archive command is dry-run unless `--apply` is present. When applied, it writes a `.tar.gz` under `.runtime/research_runs/_archive/` before removing archived run directories.

`deep_research` also returns `agent_loop` telemetry with `planned_queries`, `completed_queries`, `remaining_queries`, `rounds`, `decisions`, `observed_gaps`, and `stop_reason`. Stop reasons include `strong_enough`, `max_rounds`, `no_new_sources`, `blocked_too_often`, and `low_value_followups`.

Set `LOCAL_LLM_CONTRADICTION_REVIEW=true` to let a locally running LM Studio OpenAI-compatible API review likely claim contradictions. Set `LOCAL_LLM_REPORT_SYNTHESIS=true` to let that same local API rewrite the selected deterministic report into a more polished `final_report`. Both are optional; if disabled or unavailable, deterministic reports and rule-based conflict detection still run.

To preview or apply those optional local synthesis settings in LM Studio's MCP config:

```bash
python scripts/toggle_local_synthesis.py --enable ~/.lmstudio/mcp.json
python scripts/toggle_local_synthesis.py --enable ~/.lmstudio/mcp.json --apply --backup
python scripts/toggle_local_synthesis.py --disable ~/.lmstudio/mcp.json --apply --backup
```

The command is dry-run unless `--apply` is present.

`render=True` uses Chromium through Playwright. Empty static HTML extracts are retried with Chromium automatically. With `BROWSER_INTERACTION=true`, rendered reads make a best-effort pass to dismiss common consent overlays and scroll the page to trigger lazy-loaded content before extraction. `BROWSER_HEADLESS=true` keeps that browser in the background; set `BROWSER_HEADLESS=false` only when you need to watch or manually debug an authorized session.

## Tests

```bash
python -m unittest discover -s tests -v
python scripts/probe_mcp_server.py --cwd "$(pwd)"
```

`scripts/probe_mcp_server.py` defaults to stdio because that matches command-launched LM Studio MCP configs. Use `--transport http` only when you have started the server separately with `MCP_TRANSPORT=streamable-http`.

The test suite enforces that every `safe_*` MCP tool exposes exactly one parameter, preserving the LM Studio XML-safe calling contract.
It also checks that the LM Studio research prompt mentions every exposed `safe_*` tool.
The README MCP Tools list is also checked against the server-declared tool order.

For a single local status summary of the research prompt, LM Studio config, persisted runs, and optional MCP tool probe:

```bash
python scripts/research_stack_status.py
python scripts/research_stack_status.py --probe-tools
python scripts/research_stack_status.py --dry-run
python scripts/research_stack_status.py --json
python scripts/research_stack_status.py --refresh-prompt
python scripts/work_session_preflight.py --profile fast --dry-run
python scripts/work_session_preflight.py --profile careful --probe-tools
python scripts/work_session_preflight.py --profile careful --eval-smoke --eval-mode fixture
python scripts/work_session_preflight.py --profile careful --eval-smoke --eval-mode live
python scripts/work_session.py --profile careful --dry-run
python scripts/work_session.py --profile careful --preflight-eval-mode live --dry-run
python scripts/work_session.py --profile private-share --source-pack --dry-run
python scripts/work_loop.py --profile careful --duration-minutes 60 --interval-minutes 10 --eval-every 3 --probe-tools
python scripts/work_loop.py --profile fast --duration-minutes 60 --interval-minutes 5 --dashboard-every 0 --dry-run
python scripts/cleanup_work_loops.py --json
python scripts/cleanup_work_loops.py --review-failed --loop-id <id> --json
python scripts/work_dashboard.py
python scripts/quality_timeline.py
python scripts/build_source_pack.py --profile private-share --output-dir .runtime/source_packs/private-share
python scripts/export_research_run.py <run_id> --profile private-share --output-dir .runtime/exports
python scripts/research_ci_check.py
python scripts/run_research_eval.py --profile careful
```

If interrupted `deep_research` runs exist, the status command prints exact `safe_resume_deep_research(run_id="...")` calls for the newest resumable jobs.
It also prints suggested next safe tools for the newest completed runs.
Use `--refresh-prompt` to rewrite `.runtime/lmstudio-research-system-prompt.txt` from [LM Studio Research Mode](docs/lmstudio-research-mode.md) before checking status.
Use `--dry-run` before work sessions when you want prompt, docs, config, and saved-run validation without launching the MCP server probe.
The status command also checks README tool-list alignment and whether the LM Studio prompt mentions every `safe_*` tool.
`work_session_preflight.py` writes a timestamped `preflight.json` and `preflight.md` under `.runtime/work_preflights/`, including stack status, risk flags, latest-run budget totals, and optional eval-smoke results. Eval smoke uses deterministic fixture mode by default so preflight readiness is stable and no-network; use `--eval-mode live` for the older web-backed regression smoke, or `--eval-tasks`/`--eval-fixture` to point at a custom fixture set.
`work_session.py` runs a profile-driven operational session with preflight, dashboard, and optional eval/source-pack steps, writing `work_session.json` and `work_session.md` under `.runtime/work_sessions/`. Use `--preflight-eval-mode live` when a full work session should force live preflight smoke instead of the default fixture smoke.
`work_loop.py` runs unattended work-session cycles for a fixed duration or cycle count, writing `work_loop.json`, `work_loop.md`, `events.jsonl`, and per-cycle artifacts under `.runtime/work_loops/`.
Use `tail -f .runtime/work_loops/<loop-id>/events.jsonl` to watch a loop while it runs. `--dashboard-every 0` disables dashboard cycles; evals and source packs are disabled by default unless their `--*-every` flag is greater than zero.
`cleanup_work_loops.py` previews stale loop summaries by default and never deletes loop artifacts. Use `--apply --loop-id <id>` only after reviewing the preview; legacy missing-PID loops require `--include-legacy-missing-pid`. Completed failed loops can be acknowledged without changing `ok=false` by using `--review-failed --loop-id <id> --apply --note "reviewed old failure"`; reviewed failures remain visible but stop failing the dashboard.
`work_dashboard.py` summarizes saved work loops, preflights, evals, and remediation learning benchmarks into `.runtime/work_dashboard.md` and `.runtime/work_dashboard.json`, including a consolidated action summary for stale loops, reviewed failures, preflight risks, eval caps, eval threshold failures, source-selection regressions, remediation strategy-ranking regressions, summary links, preview/apply commands, action-history snapshots under `.runtime/work_dashboard_actions/`, per-action drilldown bundles under `.runtime/work_dashboard_action_drilldowns/`, a prioritized remediation plan under `.runtime/work_dashboard_remediation_plan.md`, remediation execution events under `.runtime/work_dashboard_remediation_events/`, and recurring low/info action suppression so persistent maintenance noise does not crowd the primary table. Use `--mark-remediation-step <step-id> --mark-remediation-status previewed|applied|resolved` to carry step execution state across dashboard refreshes; applied steps that still recur are escalated as `stale_applied`.
`research_ci_check.py` runs the deterministic fixture eval, remediation learning benchmark, and MCP stack probe together, then writes `ci_check.json`, `ci_check.md`, and `remediation_learning_benchmark.json` under `.runtime/ci_checks/`. It refreshes `.runtime/quality_timeline.md` by default; use `--skip-quality-timeline` only when you need the raw CI artifacts without touching the operator timeline.
`quality_timeline.py` summarizes recent CI artifacts into `.runtime/quality_timeline.md` and `.runtime/quality_timeline.json`, comparing stack health, fixture eval score/labels, remediation benchmark failures, and run-to-run regressions in one compact table.
`build_source_pack.py` creates offline handoff packs from saved runs with `sources.jsonl`, `claims.jsonl`, `evidence.jsonl`, `manifest.json`, and `index.md`; use `--redact` when the pack may be shared.

Work profiles are available for operational scripts:

- `fast`: quick checks and lightweight research defaults.
- `careful`: balanced work profile with tool probe and one deterministic fixture eval-smoke task.
- `private-share`: redacted export/source-pack defaults.
- `exhaustive`: deeper research/eval defaults for serious work sessions, with fixture-backed preflight smoke unless `--preflight-eval-mode live` is set.

## Exporting Saved Runs

Export a saved run for review or sharing:

```bash
python scripts/export_research_run.py <run_id> --output-dir .runtime/exports
python scripts/export_research_run.py <run_id> --output-dir .runtime/exports --redact
python scripts/export_research_run.py <run_id> --output-dir .runtime/exports --profile private-share
```

Use `--redact` for work-share bundles. It preserves run metadata, source IDs, counts, quality summaries, and audit status while replacing source URLs and source text with redaction markers.
The `private-share` profile enables the same redaction behavior by default.
Export manifests include budget summaries for sources read, rendered pages, blocked pages, follow-up searches, and agent rounds.

## Research Evaluation Harness

Run a small real-world benchmark before and after research-behavior changes:

```bash
python scripts/run_research_eval.py --limit 3
python scripts/run_research_eval.py --profile careful
python scripts/run_research_eval.py --profile exhaustive
python scripts/run_research_eval.py --tasks evals/research_fixture_tasks.json --fixture evals/fixtures/ci_basic.json --min-score 40
python scripts/remediation_learning_benchmark.py --json
python scripts/research_ci_check.py
python scripts/quality_timeline.py
python scripts/compare_eval_runs.py --latest
```

The default task set lives at [evals/research_tasks.json](evals/research_tasks.json), with regression-focused tasks in [evals/research_regression_tasks.json](evals/research_regression_tasks.json). Deterministic CI fixtures live in [evals/research_fixture_tasks.json](evals/research_fixture_tasks.json) and [evals/fixtures/](evals/fixtures). Results are written under `.runtime/evals/<timestamp>/` with one folder per task containing `payload.json`, `record.json`, `report.md` when available, and `review.md` for human scoring. The run-level `summary.md` is a benchmark leaderboard for source count, primary sources, claim support, indexed evidence, intent quality, contradiction table coverage, contradiction resolution, citation validity, blocked-source handoff, final-report citation use, expected-domain coverage, and the most common failed checks. Tasks can declare `required_checks` so adversarial cases fail harder when key research behaviors regress. `--fixture` patches search/read calls with deterministic fixture responses so CI can exercise the real research pipeline without live web variance.
`research_ci_check.py` is the single-command CI gate for local changes: by default it probes the MCP server tool list, runs [evals/research_fixture_tasks.json](evals/research_fixture_tasks.json) against [evals/fixtures/ci_basic.json](evals/fixtures/ci_basic.json) with `--min-score 40 --fail-on-label fail`, runs the remediation learning benchmark, and refreshes the quality timeline. The fixture set includes a forced contradiction case that must produce source-claim table rows with supporting sources, conflicting sources, and resolution queries, a noisy source-selection case that must skip low-value top results and read stronger buried sources, and a deep-research contradiction case that must execute an actual `contradiction_resolution` follow-up search. The remediation learning benchmark checks repeatable hard scenarios for missing primary sources, failed upgrade switching, learned conflict chronology, and pending duplicate repairs.
`compare_eval_runs.py` compares two eval summaries, or the latest two runs with `--latest`, and reports score deltas, regressions, improvements, task additions/removals, claim-support movement, intent-quality movement, and common weakness changes. `work_dashboard.py` also shows the latest eval trend columns when at least two summaries exist, plus remediation benchmark pass/fail trends from recent CI check artifacts.
Profiles can supply eval limits, thresholds, and per-task research-depth defaults without overwriting explicit task parameters.

## Notes

- Search uses free no-key web pages, so it can be less reliable than a paid search API and may occasionally be challenged or rate-limited.
- Search is live-only. Previously read pages are not stored in or returned from a persistent search index.
- Cache is in-memory only and resets when the MCP process restarts.
- Browser sessions use isolated temporary profiles by default.
- GitHub `blob` URLs are fetched from `raw.githubusercontent.com` when possible; the original URL remains in `url`, and the actual request target is recorded in `requested_url`.
- Captcha and anti-bot challenges are reported as structured failures. The tool does not attempt to solve or bypass captchas; `research_web` skips blocked sources and continues with other live results.
- When a source is blocked, `research_web` tries safe same-domain recovery candidates such as print, AMP, PDF, RSS, feed, and sitemap URLs. Successful recovered sources include `recovered_from`; blocked failures include `recovery_attempts`.
- Blocked sources include `manual_handoff` guidance, and `research_web` returns top-level `manual_visit_links` that clients should show to the user. If you are authorized to access a page, open it manually, complete the site check yourself, then retry with an explicit `BROWSER_PROFILE_DIR`.
- Respect site terms, robots policies, and rate limits.

## Captcha and Blocked Pages

When a page is gated by captcha or anti-bot checks, `read_url` returns a structured blocked response:

```json
{
  "ok": false,
  "blocked": true,
  "block_type": "captcha",
  "block_marker": "captcha"
}
```

`research_web` keeps going with other live results and also returns `blocked_sources` and `manual_visit_links`. Clients should show `manual_visit_links` directly to the user so they can open blocked pages in their own browser when they are authorized to do so. The tool does not use proxy rotation, captcha-solving services, or stealth-driver bypasses.

For blocked search results, `research_web` also tries safe same-domain alternates:

- print-friendly query/path variants
- AMP query/path variants
- same-path PDF variants
- same-domain `sitemap.xml`, RSS, and feed endpoints

If one works, the source includes `recovered_from`. If none work, the blocked failure includes `recovery_attempts`.
