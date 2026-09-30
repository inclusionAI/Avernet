# ClawBench Tests

Run all tests from the `clawbench` directory:

```bash
python3 -m pytest tests
```

If `pytest` is not installed, use the standard library runner:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

These tests cover the 2026-07-06 regressions:

- `action_run_agentbench` must pass callback env into the `benchmark.py` child process.
- `run_agentbench.log` must include callback status.
- `upload-results` must upload archived transcript JSONL files as `session` artifacts.
- pre/prod workflow YAML must use the correct ClawWeb URL and declare callback env.

If the first two tests fail, `clawmind_adapter.py` is not restoring the 6/26 callback behavior. The expected fix is to construct a child env in `action_run_agentbench` and call `subprocess.run(..., env=child_env)`.
