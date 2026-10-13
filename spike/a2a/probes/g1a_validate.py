# spike/a2a/probes/g1a_validate.py
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
KINDS = ("task", "message", "statusUpdate", "artifactUpdate")


def event_kind(wire_event: dict) -> str | None:
    result = wire_event.get("result", wire_event)
    return next((k for k in KINDS if k in result), None)


def member_count(wire_event: dict) -> int:
    result = wire_event.get("result", wire_event)
    return sum(1 for k in KINDS if k in result)


def validate(events: list[dict]) -> tuple[bool, dict]:
    kinds = [event_kind(e) for e in events]
    seqs, states = [], []
    for e in events:
        result = e.get("result", e)
        for k in ("statusUpdate", "artifactUpdate"):
            if k in result and result[k]:
                seq = (result[k].get("metadata") or {}).get("seq")
                if seq is not None:
                    seqs.append(seq)
        if event_kind(e) == "statusUpdate" and result.get("statusUpdate"):
            states.append(result["statusUpdate"]["status"]["state"])
    last = events[-1].get("result", events[-1]) if events else None
    last_kind = event_kind(events[-1]) if events else None
    checks = {
        "first_event_is_task": bool(events) and kinds[0] == "task",
        "artifact_update_present": "artifactUpdate" in kinds,
        "every_event_exactly_one_member": all(member_count(e) == 1
                                              for e in events),
        "last_event_is_final_status": last_kind == "statusUpdate"
        and (last["statusUpdate"].get("metadata") or {}).get("final") is True,
        "last_final_status_is_terminal": last_kind == "statusUpdate"
        and (last["statusUpdate"].get("metadata") or {}).get("final") is True
        and last["statusUpdate"]["status"]["state"] in TERMINAL,
        "seq_strictly_increasing": seqs == sorted(set(seqs)),
    }
    return all(checks.values()), checks

