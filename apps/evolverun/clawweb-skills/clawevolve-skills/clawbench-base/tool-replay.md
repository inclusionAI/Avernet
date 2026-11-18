# Per-case tool replay fixtures

This proposal describes replay inputs for deterministic tool-based evaluation.
The example is synthetic and can be inspected without network access. A runner
adapter is required to consume the fixture.

## Case and tool calls

Use one object per case and associate `question_id` with the existing task.
Keep the expected answer and scoring rubric with the task. The runner owns
case setup, tool interception, receipts and cleanup.

See [the fixture example](tool-replay.example.json) for the complete object.

| Field | Meaning |
| --- | --- |
| `question_id`, `case_id`, `revision` | Task reference and stable fixture identity. |
| `target` | An MCP server and tool, or a command name. |
| `match.args` | Exact MCP argument map for the first adapter. Normalize the supported CLI spelling before matching. Named argument order is immaterial. Treat extra or duplicate keys as an uncovered query unless a rule explicitly supports them. |
| `match.argv` | Exact ordered command arguments, excluding the executable name. |
| `result`, `else` | The CLI exit code, standard output and standard error for a match or for a configured target with no matching rule. |
| `default_tool_action` | Policy for an undeclared target that enters this dispatcher. The example denies that target. |

Validate the case before running and reject duplicate matching rules. A valid
query with no records returns the original tool's normal empty response. An
uncovered query on a mocked target returns the configured fixture miss and
stays in replay. An undeclared target follows the dispatcher default. If a
benchmark intentionally mixes replay and real tools, the runner must explicitly
allow the real route and record it in the trace.

## Runner and evidence

- Use the same invocation envelope as the evaluated agent. For a CLI adapter,
  replay the exact output streams and status. If the agent uses discovery,
  expose the configured tools with the expected listing format.
- Record the resolved target, normalized inputs, fixture revision, disposition
  and result. A correct tool attempt and receipt are separate evidence.
  A valid query that encounters a fixture gap leaves a required evidence item
  unassessable when no equivalent path supplies it.
- Limit the overlay to the evaluation execution environment. A search-path
  overlay affects name-based execution only. Absolute paths and native tools
  require their own adapters. Verify the baseline is selected by a fresh
  process after cleanup.
