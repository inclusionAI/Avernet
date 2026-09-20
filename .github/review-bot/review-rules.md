# Avernet PR Review Rules

Copy to `/home/avernet/review-rules.md` on the runner host.

## Must Check
- Code correctness and logic errors
- Security vulnerabilities (injection, auth bypass, secret leaks)
- Architecture compliance (service/core/port layering per AGENTS.md)
- No leaked HTTP concerns in core layer
- No raw environment access outside composition roots

## Nice to Check
- Test coverage for new code paths
- Documentation updates for changed APIs
- Performance implications

## Pull Request Process
- Always check out the PR branch fully before reviewing
- Compare against the merge target, not just read the diff
- Check related files that may be affected by the change
- If a change is too large to review thoroughly, say so explicitly

## Style
- Be concise and actionable
- Cite file paths and line numbers
- Focus on P1 (must-fix) first, then P2 (should-fix)
- Use English for technical comments