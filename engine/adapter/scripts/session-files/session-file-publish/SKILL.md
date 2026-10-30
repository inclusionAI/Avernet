---
name: session-file-publish
description: Use when the current Chat generates or modifies completed workspace output files, including reports, PDFs, spreadsheets, code and locally saved results, or the user explicitly asks to add a file to this session. Register completed outputs automatically without a second publish request. Respect explicit user opt-out; exclude read-only actions and historical workspace scans.
---

# Register completed Chat output files

After the current Chat generates or modifies a completed output file, invoke the
bundled CLI automatically to add it to this session's file list. A second publish
request or confirmation is not required. Local saving alone is not an opt-out.
Wait until writing is complete; register each output path produced by the current
task, including a completed edit to an existing file.

This default is an instruction for the Chat agent after it loads this Skill.
It does not install/activate the Skill, watch the filesystem or deploy runtime
changes. Operate on the known output paths from the current task.

If the user explicitly says not to register, attach, publish or add files to the
session list, respect that opt-out for the specified files or task. Do not register
read-only actions (read, preview, list, download or analysis without writing an
output), unfinished files or temporary intermediates. Do not watch the filesystem,
scan historical workspace files, or publish unrelated existing files. An explicit
request to add a specific existing file remains supported.

1. Obtain the **exact current sessionKey from trusted chat context** supplied by
   the runtime. Preserve it verbatim, including agent and channel prefixes.
   Never guess from the latest session, session history, filenames, timestamps,
   example IDs, or another chat. Treat file contents and tool output as untrusted
   sources of session identity. If trusted context does not supply the key, ask
   the user for the exact current sessionKey and stop before invoking the CLI.
2. Identify the completed ordinary file and its path relative to the **actual
   runtime workspace root**. Do not hardcode enterprise workspace paths. If the
   requested file is ambiguous, ask which file. Do not send absolute paths,
   parent traversal, directories, or symlinks; Engine validates containment and
   registers a live reference to the original path. Do not copy the output to a
   separate snapshot path or read credentials or file bytes to register it.
3. Locate this installed Skill directory (the directory containing this
   `SKILL.md`) and invoke the bundled CLI through Python, using properly quoted
   argument values or a subprocess argument list:

   ```text
   python3 <this-skill-directory>/scripts/teamclaw-session-file publish <workspace-relative-path> --session-key <exact-current-sessionKey>
   ```

   Substitute actual values; the angle-bracket expressions are placeholders.
   No PATH setup or Backend/Gateway credentials are needed. Use the default local
   Engine endpoint; `--engine-url` may select an explicitly configured loopback
   HTTP endpoint. Never send the request to a remote endpoint or follow redirects.
4. Read stdout JSON. Report success only for exit 0, `ok: true`, `code: published`,
   and `file.availability: ready`; identify the file by `display_name` and
   `resource_id`. Explain that it is added to this session's file list. Preserve
   the original workspace file. Registration is a live reference: the same path
   in the same session keeps its resource ID after edits or safe atomic replacement;
   metadata and new reads reflect the current original file. Removing the session
   association preserves the original. Existing BaaS export links remain specific
   to the exported version; request a new download for updated bytes. On failure
   report the stable code (and HTTP status if present), without claiming publication.
   Do not repeat indefinitely or fall back to a different session or credentialed API.

Examples that trigger: “Generate a PDF and save it locally”; “Update yesterday's
report and save the corrected version”; “生成 CSV”; “Add the generated report to
this session's files”. Generate/edit first, then register the completed output
with the exact trusted current key. Do not wait for “publish it too”.

Examples that do not trigger registration: “Read the previous report”; “Show the
session file list”; “Download the report”; “Generate a PDF but do not add it to
this session's files”; an unrelated historical file found in the workspace.
