"""Consumer contract tests for real output evidence and per-Agent input access."""

from dataclasses import replace
from hashlib import sha256

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    AGENTS,
    OWNER,
)


def start(db, tmp_path):
    injector, driver, worker, validator = deploy(db, tmp_path, automatic=False)
    driver.planner.choose = lambda candidates: candidates[0]
    driver.wake()
    driver.drain()
    report, actor = worker.produce()
    return injector, driver, worker, validator, report, actor


@pytest.mark.parametrize(
    "kind",
    [
        "missing_port",
        "extra_port",
        "duplicate_port",
        "wrong_refs",
        "wrong_type",
        "missing_resource",
        "wrong_content",
        "no_manifest",
        "duplicate_ids",
    ],
)
def test_invalid_reports_do_not_submit_child_work(db, tmp_path, kind):
    injector, driver, worker, validator, report, actor = start(db, tmp_path)
    binding = report.output_bindings[0]
    artifact = binding.artifacts[0]
    resource = artifact.resource_binding.resource_ref
    if kind == "missing_port" or kind == "no_manifest":
        report = replace(report, output_bindings=())
    elif kind == "extra_port":
        report = replace(
            report, output_bindings=(binding, replace(binding, name="extra"))
        )
    elif kind == "duplicate_port":
        report = replace(report, output_bindings=(binding, binding))
    elif kind == "wrong_refs":
        report = replace(report, artifact_refs=("forged",))
    elif kind in {"wrong_type", "missing_resource"}:
        resource = (
            replace(resource, resource_type="image")
            if kind == "wrong_type"
            else replace(resource, resource_id=str(tmp_path / "absent"))
        )
        altered = replace(
            artifact,
            resource_binding=replace(artifact.resource_binding, resource_ref=resource),
        )
        report = replace(
            report, output_bindings=(replace(binding, artifacts=(altered,)),)
        )
    elif kind == "wrong_content":
        from pathlib import Path

        Path(resource.resource_id).write_text("wrong content")
    elif kind == "duplicate_ids":
        report = replace(
            report, output_bindings=(replace(binding, artifacts=(artifact, artifact)),)
        )
    with pytest.raises(ContextCollabValidationError):
        worker.reports.report(report, actor)
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        driver.collet_id
    )
    assert next(t for t in record.tasks if t.parent_task_ref).status == "in_progress"
    assert driver.stages()[0].state is StageState.RUNNING
    assert not any(e.event_kind == "task.submitted" for e in record.events)


@pytest.mark.parametrize("revision", [None, "", "latest", "HEAD", "main"])
def test_mutable_or_missing_revision_cannot_be_handoff_evidence(db, tmp_path, revision):
    *_, report, actor = start(db, tmp_path)
    binding = report.output_bindings[0]
    artifact = binding.artifacts[0]
    with pytest.raises(ContextCollabValidationError):
        replace(
            binding,
            artifacts=(
                replace(
                    artifact,
                    resource_binding=replace(
                        artifact.resource_binding,
                        resource_ref=replace(
                            artifact.resource_binding.resource_ref, revision=revision
                        ),
                    ),
                ),
            ),
        )


def test_direct_complete_cannot_bypass_child_task_and_real_content_checks(
    db, tmp_path, monkeypatch
):
    injector, driver, worker, validator, report, actor = start(db, tmp_path)
    command = SwarmStageOutcomeCommand(
        [report.allocation_ref],
        artifact_refs=report.artifact_refs,
        output_bindings=report.output_bindings,
    )
    with pytest.raises(ContextCollabValidationError, match="done child Task"):
        driver.swarm.complete_stage(
            driver.collet_id, driver.binding_id, report.stage_ref, command, OWNER
        )
    # Inject a failure at the MethodStore port AFTER independent SQL submission/acceptance.
    original = driver.store.commit_workflow_completion

    def fail(*args):
        raise RuntimeError("injected MethodStore failure")

    monkeypatch.setattr(driver.store, "commit_workflow_completion", fail)
    with pytest.raises(RuntimeError, match="MethodStore failure"):
        worker.reports.report(report, actor)
    monkeypatch.setattr(driver.store, "commit_workflow_completion", original)
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        driver.collet_id
    )
    assert next(t for t in record.tasks if t.parent_task_ref).status == "done"
    from pathlib import Path

    path = Path(
        report.output_bindings[0].artifacts[0].resource_binding.resource_ref.resource_id
    )
    content = path.read_text()
    path.write_text("tampered after SQL acceptance")
    with pytest.raises(ContextCollabValidationError, match="version unavailable"):
        driver.swarm.complete_stage(
            driver.collet_id, driver.binding_id, report.stage_ref, command, OWNER
        )
    assert driver.stages()[0].state is StageState.RUNNING
    path.write_text(content)
    worker.reports.report(
        report, actor
    )  # Exact full-manifest reconciliation is allowed.
    assert driver.stages()[0].state is StageState.SUCCEEDED
    assert validator.output_checks


def test_partial_sql_report_cannot_replace_revision_under_same_artifact_id(
    db, tmp_path, monkeypatch
):
    _, driver, worker, _, report, actor = start(db, tmp_path)
    original = driver.store.commit_workflow_completion
    monkeypatch.setattr(
        driver.store,
        "commit_workflow_completion",
        lambda *args: (_ for _ in ()).throw(RuntimeError("partial")),
    )
    with pytest.raises(RuntimeError):
        worker.reports.report(report, actor)
    monkeypatch.setattr(driver.store, "commit_workflow_completion", original)
    binding = report.output_bindings[0]
    artifact = binding.artifacts[0]
    content = driver.stages()[0].objective + "\nnew legitimate-looking result\n"
    path = tmp_path / "replacement.txt"
    path.write_text(content)
    revised = replace(
        artifact,
        resource_binding=replace(
            artifact.resource_binding,
            resource_ref=replace(
                artifact.resource_binding.resource_ref,
                resource_id=str(path),
                revision=sha256(content.encode()).hexdigest(),
            ),
        ),
    )
    changed = replace(report, output_bindings=(replace(binding, artifacts=(revised,)),))
    with pytest.raises(
        ContextCollabValidationError, match="changed committed Task evidence"
    ):
        worker.reports.report(changed, actor)
    assert driver.stages()[0].state is StageState.RUNNING


def test_named_handoff_checks_selected_agent_access_and_does_not_redraw(db, tmp_path):
    _, driver, worker, validator, report, actor = start(db, tmp_path)
    worker.reports.report(report, actor)
    validator.revoked.add((AGENTS[0], report.artifact_refs[0]))
    choices = []

    def choose(candidates):
        choices.append(candidates)
        return candidates[0]

    driver.planner.choose = choose
    driver.wake()
    with pytest.raises(ContextCollabValidationError, match="revoked"):
        driver.drain()
    assert len(choices) == 1 and len(worker.calls) == 1
    assert driver.stages()[1].state is StageState.READY
    assert (
        len(
            driver.store.list_allocations(
                driver.axes, driver.binding_id, driver.collet_id
            )
        )
        == 1
    )
