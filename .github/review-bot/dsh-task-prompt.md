You are working on a pull request. The user asked you to perform the task
described above. The GH_TOKEN and GH_REPO environment variables are already
set, so `gh` CLI works without additional authentication. Follow these steps:

0. FIRST: React with 👀 on the triggering comment.
1. Clone the repository and check out the PR:
   ```bash
   if [ -d /tmp/review-target/.git ]; then
     cd /tmp/review-target && git fetch --depth=1 origin && gh pr checkout $PR_NUMBER
   else
     git clone --depth=1 "https://ghproxy.net/https://github.com/${GH_REPO}" /tmp/review-target
     cd /tmp/review-target && gh pr checkout $PR_NUMBER
   fi
   ```
   PR_NUMBER is provided in the workflow environment.
2. Read these files for project conventions and architecture rules:
   - docs/arch/arch.rules.md (architecture constitution)
   - AGENTS.md / CLAUDE.md (project conventions)
3. Read relevant files and diffs with full context.
4. Answer the user's question or perform the requested analysis.
   If the task is "review", perform a full code review for
   correctness, security, and architecture compliance.
5. Post your response as a PR comment using 'gh pr comment'.
   At the end, append '<!-- dsh-review -->' on its own line.
6. FINALLY: Remove the 👀 reaction AND add a ✅ reaction on the
   triggering comment to signal completion.