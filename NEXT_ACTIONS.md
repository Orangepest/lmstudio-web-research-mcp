# LM Studio Research MCP One-Hour Work Loop

Use this file when the user asks for a longer unattended work slice.

## Loop

1. Pick the highest-value pending task below.
2. Implement the smallest useful version.
3. Add or update focused tests.
4. Run focused tests, then the full suite.
5. Run `scripts/research_stack_status.py --probe-tools`.
6. Record what changed and the next task.

Stop early only when tests fail and the fix is not clear, a task needs user input, or a change would risk the working LM Studio config.

## Current Priorities

- [x] Add source-intent matching so research coverage can tell whether selected sources actually satisfy the query intent.
- [x] Add stronger citation-audit reporting: claim-to-citation coverage, uncited claims, and unsupported report sections.
- [x] Add source freshness metadata and freshness gap detection for "latest/current" questions.
- [x] Improve autonomous follow-up planning from coverage gaps, not only quality gaps.
- [x] Add a small local evaluation set for research coverage and source quality regressions.
- [x] Add optional export bundles for research runs: report, sources table, claims table, and audit JSON.
- [x] Add export bundle manifest and human-readable index for research runs.
- [x] Add batch export support for latest/found research runs.
- [x] Add eval threshold reporting so regression runs can fail CI-style when quality drops.
- [x] Add source-quality downgrade reasons for weak or duplicate-heavy source sets.
- [x] Add run comparison tooling for parent/follow-up research chains.

## Next-Level Research Quality

- [x] Add deterministic adversarial final-answer review before reports are returned.
- [x] Add a proper multi-agent planner/reviewer loop for deep research.
- [x] Add stronger document and PDF ingestion.
- [x] Add a local vector index across saved pages and research runs.
- [x] Add source credibility scoring by domain/entity, beyond simple source type rules.
- [x] Add stronger browser automation for JS-heavy pages.
- [x] Add contradiction-focused final review and follow-up search planning.

## Work-Grade Hardening

- [x] Surface browser interaction settings in stack status and enforce them in LM Studio config validation.
- [x] Add redaction controls for exported reports and source tables.
- [x] Add a no-network dry-run mode for validating prompts, config, and saved runs before work sessions.
- [x] Add per-run budget summaries for sources read, follow-up rounds, blocked pages, and rendered pages.

## Big Work Track

- [x] Add a work-session preflight command that writes JSON/Markdown readiness artifacts.
- [x] Add a regression dashboard over saved eval/preflight runs.
- [x] Add an offline source pack builder from saved research runs for work handoff.
- [x] Add a policy/profile system for different work modes: fast, careful, private-share, and exhaustive.

## Profile Integration Polish

- [x] Wire work profiles into exports so `private-share` defaults to redacted bundles.
- [x] Wire work profiles into eval runs so profiles supply limits, thresholds, and depth defaults.
- [x] Add a profile-driven work-session runner for repeatable preflight, dashboard, eval, and source-pack steps.

## MCP Packaging Tools

- [x] Add `safe_export_research_run` so LM Studio can export saved runs through one-parameter tool calls.
- [x] Add `safe_build_source_pack` so LM Studio can build redacted source handoff packs through one-parameter tool calls.
- [x] Refresh LM Studio research prompt guidance for packaging and handoff workflows.
- [x] Add packaging preview mode with `dry_run=true`/`preview=true` to avoid accidental file-writing from fuzzy tool calls.
- [x] Harden packaging input parsing for bulleted, comma-separated, and `run_ids=` local-model output.
- [x] Dedupe packaging run IDs and report `run_count` in packaging previews/results.
- [x] Make empty packaging previews fail clearly and return export `run_ids`/`run_count` after real writes.
- [x] Accept `key: value` packaging options for common local-model output forms.

## Research Mission Orchestrator

- [x] Add `safe_research_mission` as a profile-driven one-parameter research orchestrator.
- [x] Add mission dry-run planning, quality gate summaries, and optional export/source-pack packaging.

## Unattended Work Automation

- [x] Add `scripts/work_loop.py` for timed unattended work-session cycles with events and per-cycle artifacts.
- [x] Add repeat/duration convenience support to `scripts/work_session.py`.
- [x] Fix unattended-loop follow-up bugs: duration mode repeat cap, dashboard disable semantics, and cycle exception recording.

## Loop Dashboard Integration

- [x] Add work-loop summaries to `scripts/work_dashboard.py` so long unattended sessions appear beside preflights and evals.
- [x] Add dashboard tests for latest loop status, failed-cycle counts, and report links.

## MCP Work-Loop Status

- [x] Add a safe MCP status tool for LM Studio to inspect unattended work-loop progress without shell access.

## Stale Work Cleanup

- [x] Add stale work-loop cleanup tooling so killed loops can be closed deliberately without deleting artifacts.
- [x] Add `safe_cleanup_work_loops` as a preview-first MCP wrapper with explicit apply requirements.

## Research Job Manager

- [x] Add a file-backed research job store under `.runtime/research_jobs`.
- [x] Add `scripts/research_jobs.py` for local queue add/list/update operations.
- [x] Add `safe_submit_research_job` as a preview-first MCP queue submitter with explicit `submit=true`/`apply=true` writes.
- [x] Add `safe_research_job_status` for LM Studio-safe queued/running/completed job inspection.
- [x] Add `safe_cancel_research_job` for explicit job cancellation.
- [x] Refresh README, stack probe expectations, and LM Studio prompt guidance for the job tools.
- [x] Add lease-based research job execution primitives so stale `leased`/`running` jobs can be recovered.
- [x] Add `scripts/research_job_worker.py` to run queued jobs outside the MCP request handler.
- [x] Add worker tests for success, failure, empty queue, and lease lifecycle handling.
- [x] Add worker watch mode for persistent polling without tying up the MCP server.
- [x] Add `scripts/research_job_worker_control.py` for start/status/stop and tmux launch support.

## Research Checkpoint Cleanup

- [x] Add run-store checkpoint listing for resumable `deep_research` checkpoints.
- [x] Add interrupt metadata that marks stale checkpoints `interrupted` while preserving payload and resume support.
- [x] Add `safe_research_checkpoint_status` for one-parameter checkpoint inspection.
- [x] Add `safe_interrupt_research_checkpoints` as a preview-first MCP wrapper with explicit `run_id` and `apply=true` requirements.
- [x] Refresh README, stack probe expectations, and LM Studio prompt guidance for checkpoint tooling.

## Research Quality Engine

- [x] Add a per-run `evidence_index` that chunks selected source text and scores query-relevant passages.
- [x] Add `Best Evidence` report sections from top indexed chunks.
- [x] Feed evidence-index coverage into research quality scoring and gap detection.
- [x] Refresh README and LM Studio prompt guidance for evidence-index usage.
- [x] Tune LM Studio prompt to default to no tool call, use a speed/tool budget ladder, and stop retrying when tools are disabled.
- [x] Improve live source selection with planned reads that reserve slots for strong primary/docs/repository sources and diversify domains before fallback reads.
- [x] Add cross-source claim support tables using the evidence index.
- [x] Add intent-aware source selection for deep-research child searches.
- [x] Add per-intent source quality scoring to coverage reports.
- [x] Add contradiction-focused source retrieval for disputed claims.
- [x] Add research-run regression evals for contradiction resolution.
- [x] Add benchmark trend comparison between eval runs.
- [x] Add source-claim contradiction tables to reports.
- [x] Add eval task fixtures with mocked search/read responses for deterministic CI.
- [x] Add CI wrapper target that runs fixture eval plus stack probe.
- [x] Add contradiction-table scoring to eval metrics.
- [x] Add seeded contradiction fixture that forces conflicted table rows through the full eval pipeline.
- [x] Add eval score caps for unresolved high-risk review, citation, and contradiction failures.
- [x] Add local result-reranker eval fixtures for source selection under noisy search results.
- [x] Add eval gates for actual contradiction-resolution follow-up searches in deep research.
- [x] Add source-selection trend columns to eval comparison/dashboard output.
- [x] Add score-cap and source-selection summaries to work-session preflight artifacts.
- [x] Add deterministic fixture mode to work-session preflight eval smoke.
- [x] Surface preflight eval mode and score-cap summaries in the work dashboard.
- [x] Add dashboard/runtime cleanup guidance for failed and stale work-loop artifacts.
- [x] Add a reviewed/archived state for failed work-loop artifacts so acknowledged failures stop failing the dashboard.
- [x] Add a consolidated dashboard action summary for stale loops, reviewed failures, eval caps, and preflight risks.
- [x] Add action-summary history snapshots so dashboard actions can be trended over time and noisy recurring maintenance issues can be separated from new regressions.
- [x] Add action-age tracking and recurring-action suppression rules so persistent low-risk dashboard noise stops obscuring new high-value regressions.
- [x] Resolve MCP search review issues: strict site matching, SearXNG JSON parsing, DuckDuckGo Lite parsing, blank-query guard, and clearer fallback flow.
- [x] Add a unified background research mission runtime for submit/start/status polling over jobs, workers, checkpoints, and saved runs.
- [x] Add multi-job research campaigns that decompose one ambitious objective into coordinated queued background missions.
- [x] Fix campaign lifecycle reconciliation so campaign status derives subjob status/run IDs from tagged jobs.
- [x] Add campaign-level synthesis/export that merges completed campaign subjob reports into one final dossier with source and claim indexes.
- [x] Add optional local-model narrative synthesis over campaign dossiers, with deterministic fallback and quality checks.
- [x] Add an autonomous research director that creates campaigns, assesses quality gaps, queues bounded follow-ups, and triggers synthesis/export.
- [x] Add director quality gates that automatically decide stop/continue/synthesize from source coverage, contradiction handling, and missing-step risk.
- [x] Add director wave automation that can start/observe the worker and advance director state across bounded cycles without manual polling.
- [x] Add director dashboard/history views so multiple waves, gates, follow-ups, and synthesis outputs are easy to inspect from one report.
- [x] Add director recovery controls for stale waves, failed worker starts, stuck queued jobs, and interrupted checkpoints.
- [x] Add director auto-recovery policy presets that decide when wave review, worker restart, checkpoint resume, and stuck-job cancellation are allowed.
- [x] Add director objective memory and source reuse so follow-up campaigns can discover prior related runs, reuse high-value sources, and avoid repeating failed/blocked retrieval paths.
- [x] Add director evidence graph export that links campaign steps, runs, reusable sources, claims, contradictions, follow-up jobs, recovery actions, and synthesis outputs in one machine-readable artifact.
- [x] Add graph-aware director dashboards that summarize central claims, weak evidence clusters, repeated source domains, unresolved contradiction chains, and next best graph actions.
- [x] Add director graph action execution so selected graph recommendations can queue targeted follow-up jobs for weak claims, contradiction chains, and source-domain diversification.
- [x] Add director runbook export that packages dashboard, evidence graph, synthesis, recovery state, and exact next commands into one operator handoff bundle.
- [x] Add director runbook archive/export profiles for private-share and full-fidelity handoff bundles, including optional redaction and checksums.
- [x] Add director bundle comparison so two runbooks or graph exports can be diffed for new claims, changed source coverage, resolved contradictions, and remaining gaps.
- [x] Add director comparison actions so bundle diffs can queue targeted follow-up work for newly introduced gaps, lost source coverage, and unresolved contradictions.
- [x] Add director comparison action history to dashboards and runbooks so queued diff follow-ups can be audited against the bundle comparison that caused them.
- [x] Add comparison-action status reconciliation so dashboards show whether diff follow-up jobs are queued, running, completed, failed, or cancelled.
- [x] Add comparison-action result impact tracking so completed diff follow-ups can be linked back to resolved gaps, restored source coverage, or remaining failures.
- [x] Add comparison-action retry/escalation recommendations for failed or no-evidence follow-ups.
- [x] Add comparison-action replay support so failed or no-evidence follow-ups can be requeued from the original comparison event with stronger instructions.
- [x] Add comparison-action replay deduplication so repeated replays avoid queuing duplicate targets already retried recently.
- [x] Add comparison-action replay dashboard summaries so replay lineage and duplicate skips are visible without inspecting job tags.
- [x] Add replay exhaustion recommendations so already-replayed failed/no-evidence targets escalate to a changed strategy instead of endless retry.
- [x] Fix live search-provider failure path by retrying local SearXNG HTML when JSON output is forbidden, adding Brave HTML fallback, and capping search-provider timeout separately.
- [x] Harden LM Studio prompt/tool guidance against malformed XML tool calls by emphasizing one-parameter safe tools and adding parser-failure recovery examples.
- [x] Speed up search by making local SearXNG HTML the default first provider, adding configurable `SEARCH_PROVIDERS`, lowering `SEARCH_TIMEOUT`, and skipping blocked providers during temporary backoff.
- [x] Add SearXNG engine controls and normalized similar-query caching so repeated "official/latest/additional evidence" search variants return instantly after the base query.
- [x] Switch local SearXNG speed default to `engines=google`, cutting live cold search from about 3s to about 0.5s on the observed workload.
- [x] Add automatic prior research context recovery through `safe_research_context` so fresh LM Studio chats can load the best matching saved run and receive the exact next safe continuation/resume request.
- [x] Add search-provider health summaries to stack status, including configured provider order, SearXNG engine settings, recent LM Studio provider failures, and optional live provider smoke probes.
- [x] Add a Claude-style answer readiness gate that scores final dossiers for evidence grounding, coverage, freshness, contradiction handling, source diversity, repetition, and final-review severity before compact results are presented.
- [x] Wire answer readiness into mission/director quality gates so autonomous campaigns continue automatically when a dossier is not ready to present.
- [x] Fix the live LM Studio tool-timeout path by skipping hostile/low-value research domains and avoiding same-domain recovery retries after hard HTTP 403/429 blocks.
- [x] Add source-policy observability to stack status, compact payloads, and run reports so skipped hostile domains and hard-block recovery skips are easy to audit.
- [x] Add adaptive source selection that downranks low-value search results before fetch planning using source-policy signals and topic-specific authority preferences.
- [x] Add live deep-research soft-timeout diagnostics so LM Studio gets a resumable checkpoint with phase timings before the client cancels the tool call.
- [x] Add topic-aware source authority presets for market/product/company research so search planning favors primary data, official docs, app stores, filings, datasets, and reputable analyst/news sources while avoiding low-value SEO pages.
- [x] Add live source-quality telemetry that reports how many planned reads were authority sources, SEO/low-value skips, policy skips, repeated domains, and cache hits so slow/bad research sessions can be diagnosed from one run payload.
- [x] Aggregate source-selection telemetry across multi-query `deep_research` runs so director/mission dashboards can diagnose poor campaign source mix without opening child search payloads.
- [x] Add an evidence-directed remediation planner that classifies research gaps and turns them into concrete follow-up searches for reports, deep-research follow-up planning, compact payloads, and director jobs.
- [x] Add remediation outcome tracking so director follow-up jobs report whether targeted evidence gaps are pending, resolved, remaining, failed, or produced no result.
- [x] Add adaptive remediation strategy upgrades so repeated remaining/failed/no-result outcomes automatically switch query strategy before queuing the next repair.
- [x] Add remediation strategy learning so directors rank upgraded repair tactics by observed success rate per gap type instead of using static priorities forever.
- [x] Persist remediation strategy learning across directors so future campaigns can reuse proven gap-repair tactics before they have local history.
- [x] Add remediation learning exports to director runbooks so shared strategy performance can be audited and transferred between workspaces.
- [x] Add remediation learning import/restore support so exported runbook learning bundles can seed a fresh workspace.
- [x] Harden LM Studio tool-call parsing failure path by exposing only one-parameter safe MCP tools by default, adding `safe_repair_tool_call`, and explicitly disabling advanced multi-parameter tools in the local LM Studio config.
- [x] Add `safe_research_agent(request)` and switch LM Studio to `MCP_TOOL_PROFILE=agent`, exposing only the single research entrypoint plus `safe_repair_tool_call` to reduce malformed XML calls under long-context truncation.
- [x] Add `MCP_TOOL_PROFILE=agent_strict` and switch LM Studio to one exposed tool, `safe_research_agent`, to further reduce malformed XML calls under long-context truncation.
- [x] Add evidence-quality benchmark scenarios for remediation learning so strategy ranking is validated against repeatable hard research fixtures, not only synthetic director jobs.
- [x] Add trend/report surfacing for remediation learning benchmark results in the work dashboard so strategy-ranking regressions are visible beside eval and preflight regressions.
- [x] Add a compact benchmark history report that compares remediation learning, fixture evals, and live stack health in one operator-facing quality timeline.
- [x] Add quality-timeline integration to `research_ci_check.py` so every CI run refreshes the operator timeline automatically.
- [x] Add LM Studio runtime diagnostics to stack status so context truncation, bridge WebSocket closes, parse errors, and latest plugin profile are visible beside server/search health.
- [x] Route `safe_research_agent("Read https://...")` requests directly to URL fetch instead of wasting a search call on the whole instruction.
- [x] Make stack-status MCP probes inherit the LM Studio config environment so `agent_strict` probes report the same single-tool surface LM Studio sees.
- [x] Lower LM Studio compact-result caps to `MCP_RESULT_EXCERPT_CHARS=3500` and `MCP_RESULT_MAX_ITEMS=4` so repeated research calls add less context pressure.
- [x] Add LM Studio runtime timestamps for last log line, last truncation, last bridge close, and last tool call so stale failures can be separated from fresh ones.
- [x] Compact high-growth nested MCP response fields (`claims`, `source_quality`, `citation_audit`, `agent_loop`, `strategy`, coverage and remediation lists) so saved artifacts stay complete but chat-visible tool results are much smaller.
- [x] Route heavy `safe_research_agent` requests containing report/deep/giga/huge/exhaustive/due-diligence wording to the background runtime by default, with `mode: inline_deep` kept as an explicit timeout-risk override.
- [x] Add quality-timeline action surfacing in `work_dashboard.py` so score drops and remediation regressions become first-class dashboard actions.
- [x] Add quality-timeline action grouping and acknowledgement so recurring CI stack/eval/remediation failures stay visible without flooding the dashboard.
- [x] Add dashboard action drilldown exports that write a compact per-action remediation bundle with evidence links, owning artifact paths, recurrence history, and exact next commands.
- [x] Add a dashboard remediation planner that turns high/medium drilldown actions into prioritized `work_session.py`, eval, cleanup, CI, or research-runtime commands.
- [x] Add remediation-plan execution tracking so dashboard steps can be marked previewed/applied/resolved across snapshots without losing the original action context.
- [x] Clarify compact tool results so completed runs with blocked individual sources return `tool_status=completed_with_source_warnings` instead of looking like whole-tool failures.
- [x] Add remediation-plan stale-step detection so applied steps that still recur after fresh dashboard/eval/CI artifacts are automatically escalated.

## Next Roadmap Task

- [ ] Add remediation-plan escalation recommendations that suggest a stronger next command for `stale_applied` steps instead of repeating the same failed repair.

## Last Verified

- Tests: `514 passed`
- MCP probe: OK with 1 exposed tool in LM Studio `agent_strict` profile: `safe_research_agent`; advanced multi-parameter tools stay hidden unless `MCP_EXPOSE_ADVANCED_TOOLS=true`
- Compact MCP response check: recent saved research payloads now compact from roughly `63k-108k` raw JSON chars down to about `14k-18k` chat-visible JSON chars with the LM Studio result caps.
- Agent routing: heavy report/deep-research wording now queues and starts the background runtime through `safe_research_agent`; inline deep research requires explicit `mode: inline_deep`.
- Stack status: prompt/docs/config/search/tool probe are OK; LM Studio config now uses compact-result caps of `3500` chars and `4` items; LM Studio runtime is `CHECK` because the current log still contains repeated context truncation and an MCP bridge WebSocket close from the existing oversized chat. Latest readout: last tool call `18:52:12`, last bridge close `18:52:22`, last truncation `20:23:31`, latest log line `20:26:14`.
- Remediation learning benchmark: `4/4` scenarios passed; CI wrapper writes `remediation_learning_benchmark.json`, refreshes `.runtime/quality_timeline.md` by default, `work_dashboard.py` surfaces recent remediation benchmark pass/fail trends/actions from `.runtime/ci_checks`, and `quality_timeline.py` compares remediation, fixture eval, and stack-health trends across recent CI artifacts
- Quality timeline dashboard actions: `work_dashboard.py` now turns stack failures, fixture eval failures/score drops, remediation failures/regressions, and search-provider failures from the quality timeline into first-class high/medium dashboard actions with report links.
- Quality timeline grouping: repeated quality-timeline issues are grouped by issue type, carry affected CI run IDs in action details, and recurring snapshot-history matches are marked `acknowledged_recurring` while staying visible.
- Dashboard action drilldowns: `work_dashboard.py` now writes `.runtime/work_dashboard_action_drilldowns/index.md`, `index.json`, and per-action `action.md`/`action.json` bundles with related artifact paths, recurrence history, details, and preview/apply/inspect commands.
- Dashboard remediation plan: `work_dashboard.py` now ranks high/medium visible actions into `.runtime/work_dashboard_remediation_plan.md` and `.json`, mapping actions to cleanup, CI, eval, work-session, or research-runtime preview/apply commands.
- Remediation execution tracking: `work_dashboard.py` supports `--mark-remediation-step <id> --mark-remediation-status previewed|applied|resolved`, stores immutable event JSON with copied step context, and carries latest step execution status into future remediation plans.
- Tool-call failure triage: latest LM Studio log shows the server alive with one exposed `safe_research_agent`; recent calls reached the MCP server. The newest apparent failure is a completed run with one blocked Indeed source (`HTTP 403`), now surfaced as `tool_status=completed_with_source_warnings` in compact results and documented in the LM Studio prompt.
- Remediation stale-step detection: applied remediation steps that still recur in later dashboard plans are marked `stale_applied`, escalated to high severity, counted separately, and annotated so the operator knows the prior repair did not clear the issue.
- Work session preflight: fixture eval smoke OK through direct preflight and profile-driven work session dry runs
- Dashboard: pass with 13 consolidated actions, 6 visible medium-priority actions, 7 suppressed recurring low/info actions, and history snapshot comparison
- Research runtime: `safe_research_runtime` and `scripts/research_mission_runtime.py` preview submit/start/status flows without writing unless `apply=true`
- Research campaigns: `safe_research_campaign` and `scripts/research_campaign.py` preview/queue multi-job campaign plans with tagged subjobs; campaign summaries reconcile completed subjob run IDs/statuses
- Campaign synthesis: `safe_synthesize_research_campaign` and `scripts/research_campaign.py synthesize` preview/write final campaign dossier bundles with source, claim, audit, manifest, and index files
- Campaign narrative synthesis: optional `local_synthesis=true` / `--local-synthesis` local-model rewrite is validation-gated and falls back to deterministic dossiers
- Research director: `safe_research_director` and `scripts/research_director.py` preview/create director-managed campaigns, assess run quality gaps, queue follow-up jobs, and trigger synthesis/export
- Director quality gates: `quality_gate.recommended_action` now chooses `wait`, `continue`, `synthesize`, or `stop_budget_exhausted` from campaign completion, score targets, source/claim coverage, primary-source gaps, contradictions, and remaining budget
- Director waves: `safe_research_director` / `scripts/research_director.py` support `action: wave` for bounded cycles that can start/observe the worker, advance gates, and write wave artifacts when applied
- Director autopilot: `action: autopilot` runs bounded repeated waves with optional detached-worker startup, dashboard/runbook writes, and a persistent autonomy ledger under `.runtime/research_directors/<director-id>/autopilots/`
- Director dashboard: `action: dashboard` previews/writes Markdown and JSON reports with gate status, campaign steps, run reviews, follow-up candidates, wave history, and synthesis outputs
- Director recovery: `action: recovery` previews stale/problem waves, failed worker starts, stale director jobs, and interrupted checkpoints; explicit apply options can review waves and cancel stuck director jobs
- Director recovery policies: `policy=conservative|balanced|aggressive` selects repair defaults for wave review, worker restart, checkpoint resume recommendations, and stale job cancellation while staying preview-first unless `apply=true`
- Director objective memory: director preview/create discovers prior related saved runs, stores reusable source URLs and blocked/failed paths, and injects those hints into generated follow-up jobs
- Director evidence graph: `action: graph` previews/writes `evidence_graph.json` and `index.md` linking director, campaign, steps, jobs, runs, sources, claims, contradictions, memory, waves, recovery reviews, and synthesis outputs
- Graph-aware dashboard: `action: dashboard` now includes `graph_summary` with central claims, weak evidence claims, repeated source domains, unresolved contradiction chains, and recommended graph actions
- Graph action execution: `action: graph_actions` previews/queues targeted director follow-up jobs for weak claims, contradiction chains, and repeated-domain diversification with `graph_action=weak_claims|contradictions|domains`
- Director runbook: `action: runbook` previews/writes `runbook.json` and `runbook.md` with dashboard, evidence graph, recovery state, graph-action preview, synthesis status, and exact next commands
- Runbook export profiles: `action: runbook_export` previews/writes private-share or full-fidelity bundles with redaction, checksums, manifest, and tar.gz archive support
- Bundle comparison: `action: compare_bundles` diffs two runbooks, manifests, export directories, or evidence graphs for new/removed claims, source coverage changes, resolved/new contradictions, domain changes, and remaining gaps
- Comparison actions: `action: comparison_actions` previews/queues targeted director follow-up jobs from bundle diffs for new gaps, new unresolved contradictions, and lost source coverage
- Comparison action history: dashboards and runbooks show recent comparison-action events with selected action, source bundles, and planned/created job counts
- Comparison action status reconciliation: dashboards and runbooks summarize queued/running/completed/failed/cancelled job counts for each comparison-action event
- Comparison action impact tracking: dashboards and runbooks summarize completed runs, added sources/claims, primary-source coverage, and remaining contradiction markers for comparison follow-ups
- Comparison action recommendations: dashboards and runbooks recommend retries, escalation, contradiction review, worker wait/start, or rerun comparison based on follow-up status and impact
- Comparison action replay: `action: comparison_replay` requeues stronger follow-up jobs from a prior comparison-action event ID, preserving source bundle paths and tagging replay lineage
- Comparison action replay dedupe: replay jobs include stable target tags, and repeated replay previews skip targets already replayed for the source comparison event
- Comparison action replay summaries: dashboards and runbooks show replay jobs, replay event IDs, replayed target counts, duplicate-skip counts, and replay event lineage
- Comparison action replay exhaustion: dashboards and runbooks recommend changing strategy when replayed comparison follow-ups also fail or add no evidence
- Search fallback: local SearXNG JSON 403s are retried through local SearXNG HTML, Brave HTML is available before DuckDuckGo, and `SEARCH_TIMEOUT` limits blocked provider stalls
- Search speed: default provider order now hits local SearXNG HTML first, exposes `SEARCH_PROVIDERS`, uses a 4s default search timeout, and temporarily skips providers after 403/429/timeout failures
- Search similar-cache: `SEARCH_SIMILAR_CACHE=true` reuses base query results for common model-generated suffix variants, and local SearXNG requests default to `engines=google`
- Search timing: observed failing-style live query now returns through local SearXNG HTML in about 0.5s cold, with suffix variants returning from normalized cache instantly
- Search health: `scripts/research_stack_status.py --probe-search` reports provider order, search timeout, SearXNG URL/engines, recent provider failures, and a live provider smoke result; current live probe returns through local SearXNG HTML in about 0.47s
- Answer readiness: final report payloads now include `answer_readiness` with ready/needs_review/blocked status, score, blockers, warnings, repeated-line checks, and compact MCP visibility
- Mission/director gates: answer readiness now fails mission quality gates and creates director follow-up jobs tagged `director_reason:answer_not_ready` before synthesis
- Source policy: research runs skip ResearchGate/Facebook/Quora and embedded PDF viewer shells before fetching, and hard HTTP 403/429 blocks no longer trigger slow same-domain recovery retries
- Source policy observability: final reports include `## Source Policy Audit`, compact MCP payloads include `source_policy_audit`, and stack status aggregates recent policy/recovery skips
- Adaptive source selection: source-policy skip domains and embedded PDF viewer shells receive strong ranking penalties before fetch planning, while deferred policy skips remain visible in `selection_trace`
- Deep research soft timeout: `DEEP_RESEARCH_SOFT_TIMEOUT_SECONDS` pauses long deep-research calls with `status=in_progress`, `resume_tool_call`, checkpoint updates, and `phase_diagnostics.likely_timeout_phase` before report synthesis or follow-up work can exceed LM Studio's tool window
- Market source authority: market/product/company queries now rerank app stores, investor relations, SEC filings, market datasets, subscription benchmarks, and official/primary data ahead of app-development SEO pages before fetch planning
- Source-selection telemetry: research payloads, compact MCP results, reports, and run budgets now summarize planned/selected authority sources, low-value candidates, policy skips, duplicate skips, repeated domains, read failures, cache hits, and ranking signals
- Deep source-selection aggregation: `deep_research` now keeps child source-selection telemetry per query, exposes top-level aggregated telemetry, and director quality gates total authority/low-value/policy-skip counts across completed runs
- Evidence-directed planner: `remediation_plan` classifies missing primary sources, single-source claims, unresolved conflicts, freshness/citation gaps, SEO-heavy source mix, repeated domains, policy skips, and read failures into prioritized concrete searches used by reports, recommended next searches, deep follow-up planning, compact MCP results, and director follow-up jobs
- Remediation outcomes: director follow-up jobs are tagged with remediation gap/source-run metadata, assessments and dashboards summarize pending/resolved/remaining/failed/no-result outcomes, and completed follow-up runs are checked against their target gap code
- Adaptive remediation upgrades: failed, no-result, or still-remaining remediation outcomes now generate higher-priority `remediation_upgrade:<gap>` director candidates with changed query constraints, explicit strategy labels, dashboard visibility, and `remediation_upgrade` job tags
- Remediation strategy learning: director assessments now aggregate `remediation_upgrade` outcomes by gap and strategy, compute success rates and learned priority deltas, tag queued upgrade jobs with `remediation_strategy:<strategy>`, and rank the next repair tactic from observed results
- Shared remediation learning: applied director advances write each director's local strategy outcomes to `remediation_strategy_learning.json`, assessments merge shared learning from other directors with local outcomes, and new directors can prioritize proven repair tactics before building their own history
- Remediation learning exports: director runbooks now write `remediation_learning.json`, Markdown summarizes top shared strategy performance, and runbook export bundles include both the snapshot and raw `remediation_strategy_learning.json` store when available
- Remediation learning import: `safe_research_director` supports `action: import_learning|restore_learning` with `source=<export dir|tar.gz|learning JSON>`, previews merge impact by default, and writes imported strategy learning into a fresh workspace with `apply=true`
- LM Studio prompt hardening: research-mode guidance now gives exact safe one-parameter tool-call shapes and tells models to switch to `safe_*` tools or answer without tools after XML parser failures
- CI wrapper: OK after campaign tool addition, fixture eval plus stack probe wrote `.runtime/ci_checks/latest/ci_check.md`
- Stack probe: OK; LM Studio config reports `MCP_TOOL_PROFILE=agent_strict`; live search probe returns through local SearXNG HTML in about 0.51s
- CI wrapper: OK with stack probe, fixture eval, and remediation learning benchmark enabled
