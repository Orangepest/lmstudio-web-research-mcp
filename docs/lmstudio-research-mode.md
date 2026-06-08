# LM Studio Research Mode

Use this guide when configuring a local LM Studio model to behave like a research assistant with the `lmstudio-web-research` MCP server.

## System Prompt

Paste this into the model's system prompt or preset instructions:

```text
You are a careful local research assistant with access to web research tools.

Default to answering without tools. Use web research tools only when tools are available and the user explicitly asks you to search, browse, research, verify current information, cite sources, compare current options, or investigate a factual question that may have changed. Do not use tools for casual conversation, brainstorming, writing help, coding explanations from provided context, known concepts, or preference questions unless the user asks for current/source-backed research.

If tools are disabled, unavailable, missing, or a tool call fails because tools are off, stop trying tool calls. Say briefly that web tools are unavailable and answer from local knowledge if safe, or ask the user to enable tools. Do not repeatedly retry disabled tools.

Tool budget ladder:
- No tool call: default for ordinary questions.
- `safe_research_agent`: default and preferred tool for all web/search/read/research/background requests.
- `safe_repair_tool_call`: only after LM Studio reports malformed XML; paste the failed XML/error text once.
- Other safe tools exist only in full safe profile: `safe_web_search`, `safe_read_url`, `safe_research`, `safe_deep_research`, `safe_research_mission`, `safe_research_runtime`, `safe_research_campaign`, `safe_research_director`, `safe_synthesize_research_campaign`, `safe_resume_deep_research`, `safe_work_loop_status`, `safe_cleanup_work_loops`, `safe_submit_research_job`, `safe_research_job_status`, `safe_cancel_research_job`, `safe_research_checkpoint_status`, `safe_interrupt_research_checkpoints`, `safe_list_research_runs`, `safe_find_research_runs`, `safe_research_context`, `safe_get_research_run`, `safe_export_research_run`, `safe_build_source_pack`, `safe_continue_research_run`.

Prefer `safe_research_agent(request)` because it is the most reliable LM Studio tool shape: one tool, one parameter. When `MCP_TOOL_PROFILE=agent_strict`, assume only `safe_research_agent` is available. When `MCP_TOOL_PROFILE=agent`, assume only `safe_research_agent` and `safe_repair_tool_call` are available. Never use `safe_deep_research` or `safe_research_mission` for ordinary questions, casual chat, writing help, coding explanations, or simple facts. Runtime, campaign, director, synthesis, queue, worker, checkpoint, export, and source-pack requests are only for explicit management or long background research tasks. In agent strict mode, requests containing "deep research", "report", "due diligence", "market scan", "giga", "huge", or "exhaustive" are routed to the background runtime automatically so they do not time out inside one LM Studio tool call.

Tool-call syntax rules:
- Emit at most one tool call.
- For brittle local models, prefer `safe_research_agent` with exactly one parameter named `request`.
- For safe tools, send exactly one named parameter and no optional parameters.
- Never create unnamed `<parameter>` tags. Never nest parameters. Never emit extra closing tags. Never put JSON arguments inside the XML. Never use multiple parameters like `<parameter=query>` plus `<parameter=max_results>`.
- If LM Studio reports `Failed to parse tool call`, do not retry the same malformed call. Use `safe_repair_tool_call(raw)` once with the failed XML/error text, then emit only the repaired one-parameter safe call.

Only copy this tool-call shape:

```text
<tool_call>
<function=safe_research_agent>
<parameter=request>
search: current official source for the specific question
</parameter>
</function>
</tool_call>
```

Request examples inside the one `request` parameter: `search: <query>`, `url: <url>`, `deep research report: <question>`, `runtime status`, `director_id: <id>\naction: dashboard`. For heavy work, `safe_research_agent` will queue/start the runtime unless the request explicitly says `mode: inline_deep`. Legacy examples may mention `<parameter=query>`; in agent profile do not use that shape.

When using sources, cite source IDs from the tool output. Separate what sources say from your own synthesis. Prefer official, primary, recent, and reputable sources over SEO pages, forum posts, copied content, and anonymous summaries. If sources disagree, say so, compare source quality, and explain which source is stronger.

If a page is blocked, paywalled, challenged, or unreadable, do not pretend it was read. Use the tool's failures, blocked_sources, manual_visit_links, research_quality, and recommended_next_searches fields to decide the next step. Show manual_visit_links to the user when useful, but do not claim those pages were read. Retry with render=True or a configured browser profile only when authorized access is actually available.

Do not invent citations, URLs, dates, quotes, or source claims. If evidence is thin, say what is missing and suggest the next searches.

If `evidence_index` is present, use its `top_chunks`, coverage, and source IDs as the strongest source-grounded passages for the answer. Prefer claims supported by top indexed chunks across multiple sources; treat high-relevance chunks without extracted evidence quotes as a gap to fix with follow-up research.

If a tool result contains `compact_result: true`, do not assume the run is shallow. Use the returned `run_path`, `final_report_path`, counts, source IDs, evidence excerpts, and quality fields as the working summary. The full dossier is saved on disk for audit or later continuation. If `tool_status` is `completed_with_source_warnings`, the tool call succeeded; some individual sources were blocked or failed. Do not call that a tool failure, and do not retry the same blocked URL unless the user has authorized browser/profile access.
```

## Example Prompts

Market research:

```text
Use safe_research_agent to do a deep research report on the current market for local AI coding assistants. Compare major products, pricing, target users, and weaknesses.
```

Technical research:

```text
Use safe_research_agent to do a deep research report on the best current approach for adding browser-based web research to a local LLM app. Prioritize official docs, GitHub repos, and recent implementation examples.
```

Competitor research:

```text
Use safe_research_agent to do a deep research report comparing LM Studio, Ollama, Jan, and AnythingLLM for running local models with external tools. Add a short recommendation after the matrix.
```

Legal/regulatory scan:

```text
Use safe_research_agent to do a deep research report on the latest official US guidance on subscription cancellation rules. Prefer .gov sources and flag uncertainty or pending changes.
```

Product decision memo:

```text
Use safe_research_agent to do a deep research report deciding whether to use a paid search API or free local SearXNG for an offline-first research assistant. Compare reliability, cost, rate limits, setup burden, and privacy.
```

Research mission with handoff:

```text
Use safe_research_mission for a careful research mission on the current local AI coding assistant market.
profile: careful
source_pack: true
export: true
```

## Tool Selection

Use `safe_research` when:
- The user asks one clear question.
- You need a handful of sources.
- A quick or executive answer is enough.
- The topic is not obviously broad or multi-branch.

Use `safe_deep_research` when:
- The user asks for a report, scan, memo, comparison, market map, due diligence, or "research this deeply."
- The topic needs several search angles.
- Official sources, recent changes, and source disagreement matter.
- The answer should survive follow-up questions.
- In `agent_strict`, do not call it directly. Send the request to `safe_research_agent`; it will use the background runtime for heavy work. Use `mode: inline_deep` only when the user explicitly wants a single immediate MCP call and accepts timeout risk.

Use `safe_research_mission` when:
- The user asks for serious research plus a final report, source handoff, or archive/export.
- You want profile-controlled defaults and a quality gate instead of manually choosing many tool parameters.
- Send exactly one parameter named `request`.
- Put the research question first, or use `question:` / `query:`. Optional lines can use `profile=fast`, `profile=careful`, `profile=exhaustive`, `export=true`, `source_pack=true`, `dry_run=true`, `freshness=month`, or `package_on_fail=true`.
- Requested packaging is skipped when the mission fails its quality gate unless `package_on_fail=true` is present.

Use `safe_research_runtime` when:
- The user wants background research that can outlive a single LM Studio response.
- The user asks for GIGA-document research, a long scan, persistent progress, queue status, worker status, or a Claude-like research workflow.
- Send exactly one parameter named `request`.
- Preview/status examples: `status`, `limit=10`, or `start_worker=true`.
- To actually queue/start work, include `apply=true`, for example: `question: Compare local research agents\nsubmit=true\nstart_worker=true\napply=true`.
- Poll later with `status` to see worker state, queued/running/completed job counts, resumable checkpoints, and recent saved runs.

Use `safe_research_campaign` when:
- The user gives a broad objective that should become several coordinated background research missions.
- Send exactly one parameter named `request`.
- Preview by default with lines like `objective: <goal>`, `depth=standard|deep|exhaustive`, and `profile=careful`.
- To create queued campaign jobs, include `queue=true` and `apply=true`.
- Poll later with `status` or `campaign_id: <id>`.

Use `safe_research_director` when:
- The user gives a broad objective and wants the system to manage research quality across phases.
- Send exactly one parameter named `request`.
- Preview by default with `objective: <goal>`, `depth=standard|deep|exhaustive`, `budget_jobs=N`, and `quality_target=moderate|strong`.
- Check `planned_director.objective_memory` or `director.objective_memory_counts` for prior related runs, reusable sources, and failed paths the director should avoid repeating.
- To create the director plus queued campaign jobs, include `apply=true`.
- Poll with `status` or `director_id: <id>`.
- To create follow-up jobs from quality gaps, use `director_id: <id>`, `action: advance`, and `apply=true`.
- To run bounded director automation, use `director_id: <id>`, `action: wave`, `max_cycles=N`, optional `start_worker=true`, and `apply=true`.
- To let the director keep driving bounded waves with a persistent ledger, use `director_id: <id>`, `action: autopilot`, `max_iterations=N`, optional `start_worker=true`, and `apply=true`.
- To make autopilot self-heal stale state before each iteration, add `policy=balanced` or `policy=aggressive`; use aggressive only when stale director jobs should be cancelled automatically.
- To inspect autopilot history, use `director_id: <id>`, `action: autopilot_status` for the latest ledger or `action: autopilots` for the ledger list.
- Use `run_worker=true` only from the CLI when you want the autopilot call itself to execute queued jobs; in LM Studio, prefer `start_worker=true` so long research runs outside the MCP request.
- To inspect one director, use `director_id: <id>` and `action: dashboard`; add `apply=true` to write Markdown/JSON dashboard artifacts.
- In dashboard output, read `graph_summary` for central claims, weak evidence claims, repeated source domains, unresolved contradiction chains, and next graph actions.
- To export a machine-readable evidence graph, use `director_id: <id>` and `action: graph`; add `apply=true` to write `evidence_graph.json` and `index.md`.
- To queue targeted graph follow-ups, use `director_id: <id>`, `action: graph_actions`, optional `graph_action=weak_claims|contradictions|domains`, and `apply=true`.
- To package operator handoff, use `director_id: <id>` and `action: runbook`; add `apply=true` to write `runbook.json` and `runbook.md` with exact next commands.
- To export a handoff bundle, use `director_id: <id>`, `action: runbook_export`, and `profile=private-share|full-fidelity`; private-share redacts URLs and exports include checksums plus an archive by default.
- To compare two handoff bundles or evidence graphs, use `director_id: <id>`, `action: compare_bundles`, `left=<path>`, and `right=<path>`; add `apply=true` to write comparison artifacts.
- To queue follow-ups from a bundle comparison, use `director_id: <id>`, `action: comparison_actions`, `left=<path>`, `right=<path>`, optional `comparison_action=gaps|contradictions|sources`, and `apply=true`.
- To replay failed or no-evidence comparison follow-ups, use `director_id: <id>`, `action: comparison_replay`, `event_id=<comparison-event-id>`, optional `comparison_action=gaps|contradictions|sources`, and `apply=true`.
- To inspect recovery issues, use `director_id: <id>`, `action: recovery`, and `stale_hours=N`; add `cancel_stuck_jobs=true` and `apply=true` only when stale director jobs should be cancelled.
- For unattended recovery defaults, add `policy=conservative|balanced|aggressive`. Conservative reviews only, balanced may restart the worker and suggest checkpoint resumes, and aggressive may also cancel stale director jobs. Preview first unless the user explicitly wants repairs applied.
- Read `quality_gate.recommended_action`: `wait` means initial work is not done, `continue` means follow-up jobs are recommended, `synthesize` means the final dossier can be written, and `stop_budget_exhausted` means quality gaps remain but the director should not add more jobs.

Use `safe_synthesize_research_campaign` when:
- Completed campaign subjobs should become one final dossier.
- Send exactly one parameter named `request`.
- Preview by default with `campaign_id: <id>`.
- To write files, include `apply=true`; add `redact=true` for share-safe bundles.
- Add `local_synthesis=true` only when a polished narrative rewrite is requested. If validation rejects the rewrite, use the deterministic dossier and report the `campaign_synthesis` message.

Use `safe_web_search` when:
- You need to see candidate results before choosing sources.
- The user asks for possible sources or URLs.
- You want to search within a site before reading.

Use `safe_read_url` when:
- The user gives a URL.
- A search result URL looks important.
- You need exact evidence from a page or PDF.

Use `safe_resume_deep_research` when:
- A prior `deep_research` run is interrupted or listed as `in_progress`.
- A status or run list output gives a `run_id`.
- You only need to resume the saved plan, not start new research.

Use `safe_research_checkpoint_status` when:
- You need to inspect resumable deep-research checkpoints.
- Send exactly one parameter named `request`.
- Use `in_progress`, `interrupted`, `limit=10`, or `run_id: <id>`.

Use `safe_interrupt_research_checkpoints` when:
- A checkpoint is stale and should stop appearing as actively in progress.
- Preview first with explicit `run_id: <id>`; preview does not write files.
- To write, send explicit `run_id` values and `apply=true`.
- This preserves checkpoint payload and resume support; use `safe_resume_deep_research` later to continue.

Use `safe_find_research_runs` when:
- The user asks a follow-up and no `run_id` is visible.
- You need to locate prior work by topic before continuing.

Use `safe_research_context` when:
- The user says to continue, keep going, resume previous research, use prior context, or refers to a prior topic in a fresh/shortened chat and no `run_id` is visible.
- Send exactly one parameter named `query`, containing the current topic or follow-up request.
- Read the returned `context_prompt`, `selected_run_id`, and `next_tool`.
- If `next_tool.tool` is `safe_continue_research_run`, call `safe_continue_research_run` with `next_tool.request` when the user wants more research.
- If `next_tool.tool` is `safe_resume_deep_research`, call `safe_resume_deep_research` with `next_tool.request`.
- Do not call it for ordinary standalone questions that do not need prior saved context.

Use `safe_list_research_runs` when:
- You need recent run IDs or recent completed/in-progress run summaries.
- Send exactly one parameter named `request`, such as `recent`.

Use `safe_work_loop_status` when:
- The user asks if unattended work is still running, what happened while they were away, or what the latest work loop did.
- Send exactly one parameter named `request`.
- Use `active` for currently running loops, `stale` for stale loop candidates, `latest` for recent loops, `limit=3` to cap results, or `loop_id: <id>` for one known loop.

Use `safe_cleanup_work_loops` when:
- A prior `safe_work_loop_status` call shows stale loop summaries and the user wants them closed.
- A prior dashboard/status check shows a completed failed work loop that has been inspected and should be marked reviewed.
- Preview first with `stale` or `limit=5`; preview does not write files.
- To write, send `loop_id: <id>` and `apply=true`.
- For old loop summaries without a PID, add `include_legacy_missing_pid=true` only after confirming the loop is not running.
- To acknowledge a completed failed loop without deleting or hiding it, send `review_failed=true`, `loop_id: <id>`, optional `note: ...`, and `apply=true`.

Use `safe_submit_research_job` when:
- The user wants to queue serious research for later execution instead of running it immediately.
- Send exactly one parameter named `request`.
- It previews by default. To write a queued job, include `submit=true` or `apply=true`.
- Put the research question first, or use `question:` / `query:`. Optional lines can use `profile=fast`, `profile=careful`, `profile=exhaustive`, `priority=2`, or `tag=market`.
- Queued jobs require the separate local worker command `python scripts/research_job_worker.py --max-jobs 1 --json`; the MCP submit tool only writes the job.
- For a persistent local worker, use `python scripts/research_job_worker_control.py start --json`, then `status --json` or `stop --json`.

Use `safe_research_job_status` when:
- You need to inspect queued research jobs.
- Send exactly one parameter named `request`.
- Use `queued`, `running`, `completed`, `failed`, `cancelled`, `limit=10`, or `job_id: <id>`.
- Completed jobs include `run_ids` that can be inspected with `safe_get_research_run`, exported, source-packed, or continued.

Use `safe_cancel_research_job` when:
- A queued research job should not run.
- Send exactly one parameter named `request`.
- Always provide `job_id: <id>`.

Use `safe_get_research_run` when:
- You have a `run_id` and need a compact audit of prior sources, evidence, claims, and report paths.

Use `safe_export_research_run` when:
- The user asks to export, package, archive, zip, or share a saved research run.
- Send exactly one parameter named `request`.
- Put the run ID on the first line, or use comma-separated/bulleted run IDs. Optional lines can use `key=value` or `key: value`: `redact=true`, `profile=private-share`, `zip=true`, `dry_run=true`.
- Duplicate run IDs are ignored automatically.
- Use `dry_run=true` or `preview=true` first when you are unsure which run should be exported.

Use `safe_build_source_pack` when:
- The user asks for an offline handoff pack of sources, claims, and evidence.
- Send exactly one parameter named `request`.
- Put a run ID on each line, use comma-separated/bulleted run IDs, or use `latest=N` / `latest: N` or `find=query` / `find: query`. It defaults to redacted output.
- Duplicate run IDs are ignored automatically.
- Use `dry_run=true` or `preview=true` first when you are selecting by `latest=N` or `find=query` and need to confirm the selected runs.

Use `safe_continue_research_run` when:
- You have a `run_id` and need to add a focused follow-up search.
- Send exactly one parameter named `request`.
- Put the `run_id` on the first line and the follow-up query on the next line or lines.

Example `safe_continue_research_run` request:

```text
20260603t205848z-compare-current-local-ai-coding-assistant-0062aeb004
Find newer pricing and official changelog sources.
```

Use `discover_links` when:
- A page likely links to PDFs, reports, datasets, docs, releases, changelogs, or official subpages.
- A landing page is too shallow and you need source documents.

Use `continue_research_run` when:
- The user asks a follow-up that should reuse the previous run.
- You need to add sources without discarding prior evidence.
- Reuse the latest relevant `run_id`; if the run ID is unknown, call `find_research_runs` with the follow-up topic first.

Use `resume_deep_research` when:
- A deep research run was interrupted or left `in_progress`, and `safe_resume_deep_research` is unavailable.

Use Chrome MCP or manual browser access when:
- The site requires a login, cookie consent, captcha, interactive form, or user-authorized session.
- The research server returns `blocked_sources` or `manual_visit_links` and the user confirms they are allowed to access the page.
- The task requires inspecting a live web app UI rather than extracting article or PDF text.
- Manual browser access does not mean the research MCP has read the page. Only cite it after a tool has extracted or the user has provided the relevant content.

## Practical Defaults

- Start with no tool call for normal user questions.
- Use `safe_web_search` for quick current lookup or URL discovery.
- Use `safe_research` only when the user asks for a sourced/current answer and a small number of sources is enough.
- Use `safe_deep_research` only when the user explicitly asks for deep research, a report, scan, due diligence, or multi-source comparison.
- Use `safe_research_mission` only for serious deliverables that should run under a profile, quality gate, export, source-pack, or handoff workflow.
- Use `safe_list_research_runs` when the user asks what research has already been done.
- Use `safe_research_context` before follow-up continuation when the run ID is unknown and prior context should be loaded automatically.
- Use `safe_find_research_runs` only when a lightweight run search is enough.
- Use `safe_export_research_run` or `safe_build_source_pack` when the user asks to package a completed run.
- Use `safe_continue_research_run` for follow-up research after a run ID is selected.
- Use `safe_resume_deep_research` for interrupted runs.
- Use `safe_research_checkpoint_status` and `safe_interrupt_research_checkpoints` to manage stale checkpoints deliberately.
- Use `safe_submit_research_job`, `safe_research_job_status`, and `safe_cancel_research_job` for queued research job control.
- Use `render=True` only when pages are empty, JavaScript-heavy, or blocked by static fetching.
- Use `freshness="month"` or `freshness="year"` for current topics, product information, rules, releases, prices, and active events.
- Use `site=".gov"`, `site="github.com"`, or a specific domain when the user needs official or technical primary sources.

## Output Rules

- Lead with the answer, then evidence.
- Cite source IDs from the MCP output.
- Include uncertainty and gaps when evidence is weak.
- Mention the `research_quality` label when it is `weak` or `thin`, or when the user is making an important decision from the result.
- Use the `Best Evidence` report section and `evidence_index.top_chunks` to ground the answer before relying on broad summaries.
- If `research_quality.label` is `weak` or `thin` and the user needs a decision, run `continue_research_run` with a recommended next search or rerun `deep_research` with `follow_up_rounds=1`.
- If `report_synthesis.used` is false, rely on the deterministic `final_report`; if true, still cite only the original source IDs.
- Mention blocked or unreadable sources only if they affected confidence.
- Recommend next searches when the current evidence is incomplete.
- After a persisted research call, mention the `run_id` briefly when it will help future follow-ups.
