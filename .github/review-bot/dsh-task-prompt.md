You are working on a pull request. The user asked you to perform the task
described above. Follow these steps:

0. FIRST: React with 👀 on the triggering comment.
1. Checkout the repository and the PR branch using gh CLI.
2. Read these files for project conventions and architecture rules:
   - docs/arch/arch.rules.md (architecture constitution)
   - AGENTS.md / CLAUDE.md (project conventions)
   - /home/avernet/review-config/review-rules.md (custom review rules)
3. Read relevant files and diffs with full context.
4. Answer the user's question or perform the requested analysis.
   If the task is "review", perform a full code review for
   correctness, security, and architecture compliance.
5. Post your response as a PR comment using 'gh pr comment'.
   At the end, append '<!-- dsh-review -->' on its own line.
6. FINALLY: Remove the 👀 reaction AND add a ✅ reaction on the
   triggering comment to signal completion.