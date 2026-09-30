import type { RepairTaskContext } from "./contracts.js";

// Fixed, argument-free probe. Isolated Python skips user site/startup hooks and
// bytecode writes; missing python3 is a normal failed observation, never installed.
const SELF_OBSERVATION = String.raw`import json, os, platform
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def read_bounded(path):
    with open(path, "r", encoding="utf-8") as stream:
        text = stream.read(16385)
    if len(text) > 16384:
        raise OSError("self observation too large")
    return text


def linux_process_identity() -> dict:
    if platform.system() != "Linux":
        return {"status": "unsupported_platform", "startTicks": None,
                "bootId": None, "namespaces": None, "capEff": None}
    result = {"status": "partial", "startTicks": None, "bootId": None,
              "namespaces": {}, "capEff": None}
    # /proc/self always means this probe, never a supplied or reused PID.
    try:
        fields = read_bounded("/proc/self/stat").rsplit(") ", 1)[1].split()
        result["startTicks"] = int(fields[19])  # field 22, suffix starts at 3
    except (OSError, ValueError, IndexError):
        pass
    try:
        result["bootId"] = read_bounded("/proc/sys/kernel/random/boot_id").strip()
    except OSError:
        pass
    try:
        for line in read_bounded("/proc/self/status").splitlines():
            if line.startswith("CapEff:"):
                result["capEff"] = line.split(":", 1)[1].strip()
    except OSError:
        pass
    for name in ("mnt", "user", "pid"):
        try:
            result["namespaces"][name] = os.readlink("/proc/self/ns/" + name)
        except OSError:
            result["namespaces"][name] = None
    if all(result[key] is not None for key in ("startTicks", "bootId", "capEff")) and all(
        value is not None for value in result["namespaces"].values()
    ):
        result["status"] = "observed"
    return result


started = now()
path = os.environ.get("PATH")
path_status = "unset" if path is None else "too_large" if len(path) > 16384 else "observed"
try:
    cwd = os.getcwd()
except OSError:
    cwd = None
groups = sorted(os.getgroups())
print(json.dumps({
    "schemaVersion": "repair-execution-context/v1",
    "scope": "this_probe_process_only",
    "startedAt": started,
    "pid": os.getpid(), "parentPid": os.getppid(),
    "uid": os.getuid(), "effectiveUid": os.geteuid(),
    "gid": os.getgid(), "effectiveGid": os.getegid(),
    "supplementaryGroups": groups if len(groups) <= 2048 else None,
    "groupsStatus": "observed" if len(groups) <= 2048 else "too_large",
    "cwd": cwd if cwd is None or len(cwd) <= 4096 else None,
    "cwdStatus": "unavailable" if cwd is None else "too_large" if len(cwd) > 4096 else "observed",
    "path": None if path_status == "too_large" else path,
    "pathStatus": path_status,
    "processIdentity": linux_process_identity(),
    "finishedAt": now(),
}, ensure_ascii=True))
`;

export function buildRepairExecutionContextCommand(): string {
  return `python3 -I -S -B - <<'REPAIR_EXECUTION_CONTEXT'\n${SELF_OBSERVATION}REPAIR_EXECUTION_CONTEXT`;
}

/** Controller attribution, never inferred from stdout or claimed by the agent. */
export function repairExecutionContextBinding(context: RepairTaskContext, initialRuntimeUserPolicy: string) {
  return {
    schemaVersion: "repair-execution-context-binding/v1",
    entry: "runtime_inspect.execution_context",
    actor: "current_repair_probe",
    taskId: context.taskId,
    stepId: context.stepId,
    attempt: context.attempt,
    runtimeTargetVersion: context.runtimeTargetVersion,
    targetFingerprint: context.targetFingerprint,
    initialRuntimeUserPolicy,
    scope: "this_probe_process_only",
    establishesOriginalExecContext: false,
    establishesFutureActionContext: false,
    establishesWritePermission: false,
  } as const;
}
