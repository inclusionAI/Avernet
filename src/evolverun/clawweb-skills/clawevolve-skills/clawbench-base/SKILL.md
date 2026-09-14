---
name: clawbench-base
description: Run and diagnose ClawBench evaluations through ClawMind and ClawWeb. Use when the user wants to trigger /clawbench, inspect benchmark progress, locate run artifacts, or troubleshoot evaluation execution.
metadata:
  version: "1.2.1"
---

# ClawBench

ClawBench is the OpenClaw evaluation workflow exposed through ClawMind and ClawWeb. It runs evaluation domains, records task results, captures transcripts, and can generate diagnostic reports.

Do not expose internal benchmark runtime names to end users. Refer to this capability as **ClawBench** in user-facing responses, UI copy, docs, and release material.

## When to Use

- The user wants to run an OpenClaw evaluation with `/clawbench`.
- The user wants to repair or regenerate a ClawBench report with `/clawbenchreport`.
- The user asks whether a ClawBench run was intercepted by ClawMind.
- The user wants to locate run status, result files, transcripts, or diagnosis output.
- The user reports that ClawBench is pending, stuck, or failed.
- The user needs a publishing-safe explanation of the ClawBench workflow.

## Common Commands

Run all templates in a domain:

```bash
/clawbench run --domainId <domain_id>
```

Run one template:

```bash
/clawbench run --domainId <domain_id> --templateName <template_name>
```

Run as an explicit ClawWeb owner on a shared evaluation machine:

```bash
/clawbench run --domainId <domain_id> --ownerId <user_id>
```


Repair or regenerate the report for an existing run:

```bash
/clawbenchreport run --benchId <bench_run_id>
```

Useful local checks:

```bash
tail -n 200 ~/.openclaw/logs/gateway.log
curl --noproxy '*' -s http://localhost:3001/api/bench/runs/<benchRunId>
find ~/.openclaw/workspace -path '*benchmark*' -maxdepth 8 -type f
```

## Included Runtime Files

This skill ships the local ClawBench runner implementation:

- `scripts/`: runner, adapter, grading, upload, and utility scripts.
- `tests/`: core library tests.
- `report/`: report template and report-generation skill source.

Task datasets are intentionally not packaged in this skill. Runtime tasks are expected to come from ClawWeb/centralized configuration.

## Execution Path

1. OpenClaw receives `/clawbench ...`.
2. ClawMind resolves the ClawBench facade binding. Report repair uses the separate `/clawbenchreport` facade.
3. ClawMind loads the workflow spec from ClawWeb API/DB.
4. Normal workflow creates a ClawWeb bench run record. Report repair workflow resolves an existing bench run instead.
5. Workflow runs the local evaluation engine.
6. Workflow uploads result artifacts and task summaries to ClawWeb.
7. Workflow prepares report context and optionally runs `clawbench-report`.
8. ClawWeb displays run status, score, pass rate, task records, and report summary.

## Key Log Signals

```text
[clawmind] extractWrappedWorkflowSlashCommand result { commandName: 'clawbench' }
[clawmind] facade registry built
[clawmind] handleRun entry
NODE_EXECUTING node=create-bench-run
NODE_EXECUTING node=run-evaluation
NODE_EXECUTING node=upload-results
NODE_EXECUTING node=prepare-report
NODE_EXECUTING node=generate-report
```

Run artifacts usually appear under:

```text
~/.openclaw/workspace/<runtime>/<runStamp>/<domainId>/benchmark/<scene>/
```

Important files:

```text
*_benchmark_report.json
*_transcripts/<task_id>.jsonl
```

## Troubleshooting

If `/clawbench` is not intercepted:

- Check gateway logs for `extractWrappedWorkflowSlashCommand`.
- Confirm ClawMind loaded facade bindings from ClawWeb.
- Confirm the facade binding contains command `clawbench`.
- Confirm the OpenClaw session is sending messages to gateway.

If run creation fails:

- Check `POST /api/bench/runs` errors in gateway log.
- Verify ClawWeb is running at the URL configured in ClawMind.
- Verify run time fields are written as Unix seconds where required.

If evaluation hangs at agent creation:

- Check whether ordinary OpenClaw CLI commands return.
- Background timers in ClawMind must not block one-shot CLI processes; auxiliary timers should use `unref()`.
- ClawMind API server should not start in ordinary CLI contexts unless explicitly required.

If report generation fails with missing skill:

- Install or link `clawbench-report` and any workflow-compatible skill alias expected by the workflow.
- The expected skill directory must contain `SKILL.md` under one of:
  - `~/.openclaw/workspace/skills/<skillName>/SKILL.md`
  - `~/.openclaw/skills/<skillName>/SKILL.md`
