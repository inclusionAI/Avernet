---
name: clawbench-bcn
description: Run a local Singlebox five-member BCN benchmark with a predefined Case and Profile, collect OpenClaw evidence, and grade the group delivery. Not for online groups, deployment, or autonomous optimization.
---

# Local BCN group benchmark (P1)

Use `scripts/run.sh`; the user invokes the script directly, not a controller Agent.
Read `README.md` for setup and the known verification limits. Run `--check` first.

The Case Prompt is the collaboration goal (`create-group --context`). Create a
Manager-worker group once: CEO is Manager, the remaining members are Workers.
Do not send a second chat message or manufacture a CEO utterance.

The repository's Singlebox base services and a real model configuration must
already exist. The runner initializes five disposable Bot profiles, stops only
those Bots and terminates the owned group, then collects local evidence and grades.
Do not modify shared Singlebox/BCS code or stop shared services to bypass failures.

Full Cases contain reference answers and executable grading code. Only reviewed
Cases are supported. Never give the full Case or its path to the evaluated Bots.
Local directory separation is not a filesystem security sandbox. Keep runtime
files private: no upload, commit, or publication of logs or credentials.

On uncertain creation results, do not automatically retry. Inspect BCS for the
run topic before creating another group. A timeout is not successful completion.
If the live contracts differ from the verified source, stop and ask rather than
falling back to idle detection, guessed identity, or unrelated session logs.

Default invocation needs only `--case` and `--profile-dir`. Reuse prepared Singlebox model configuration; `--runtime` is an optional override. Do not generate temporary launchers or automatically start shared base services.
