# ClawBench workflows

The current public execution path uses `clawevolve-bench/scripts/clawbench-workflow.py`, which receives its model, Skill paths and ClawWeb URL from the caller.

Historical environment-specific workflow YAML files are not shipped here because they embed deployment paths and service defaults. Do not recreate public and internal variants of the same workflow; pass environment values at runtime.
