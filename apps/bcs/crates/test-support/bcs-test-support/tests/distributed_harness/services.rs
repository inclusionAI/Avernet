use super::*;
use harness::services::*;

impl DistRunnerCoreService for Script {
    fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        self.call(
            "init",
            (
                context.spec,
                serde_json::to_value(context.workflow).unwrap(),
                context.now,
            ),
        )
    }
    fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.call(
            "reduce",
            (
                context.spec,
                serde_json::to_value(context.workflow).unwrap(),
                context.now,
                state,
                inputs,
            ),
        )
    }
}

#[test]
fn runner_harness_detects_nondeterministic_decisions() -> ContractResult {
    let spec = spec();
    let workflow = workflow();
    let context = EvaluationContext {
        spec: &spec,
        workflow: &workflow,
        now: TimestampMillis(500),
    };
    let inputs = vec![RunnerInput::Start {
        tid: input().tid,
        payload: payload(),
    }];
    let decision = Decision {
        next_state: state(),
        actions: vec![ActionIntent::SetStatus {
            tid: input().tid,
            status: TaskStatus::Working,
            interactions: vec![],
        }],
        next_deadlines: vec![],
    };
    for deterministic in [true, false] {
        let mut repeated = decision.clone();
        if !deterministic {
            repeated.next_state.value = serde_json::json!({"random":123});
        }
        let context_args = (&spec, serde_json::to_value(&workflow)?, context.now);
        let reduction_args = (
            &spec,
            serde_json::to_value(&workflow)?,
            context.now,
            state(),
            &inputs,
        );
        let script = Script::default()
            .expect("init", &context_args, Ok::<_, RunnerError>(state()))
            .expect("init", &context_args, Ok::<_, RunnerError>(state()))
            .expect(
                "reduce",
                &reduction_args,
                Ok::<_, RunnerError>(decision.clone()),
            )
            .expect("reduce", &reduction_args, Ok::<_, RunnerError>(repeated));
        let check =
            || dist_runner_core_service_contract_tests(&script, &context, &state(), &inputs);
        if deterministic {
            check()?;
        } else {
            let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(check));
            assert!(panic.is_err(), "nondeterministic reducer was accepted");
        }
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn gateway_core_harness_checks_duplicate_receipt() -> ContractResult {
    let request = AdmitTaskCommand {
        input: input(),
        create_session: Some(FrozenGroupDefinition {
            group: spec().group,
            workflow: spec().workflow,
            participants: spec().participants,
            limits: spec().limits,
        }),
        start_task: true,
    };
    for same_receipt in [true, false] {
        let mut replay = receipt();
        if !same_receipt {
            replay.tid = Tid("another-task".into());
        }
        let script = Script::default()
            .expect(
                "DistGatewayCoreService::admit",
                (scope(), &request),
                Ok::<_, DistGatewayCoreError>(CoreAdmission(receipt())),
            )
            .expect(
                "DistGatewayCoreService::admit",
                (scope(), &request),
                Ok::<_, DistGatewayCoreError>(CoreAdmission(replay)),
            );
        let script = if same_receipt {
            let stranger = TaskScope {
                tenant: scope().tenant,
                principal: PrincipalId(format!("{}-another", scope().principal.0)),
            };
            script
                .expect(
                    "DistGatewayCoreService::session_group",
                    (scope(), receipt().sid),
                    Ok::<_, DistGatewayCoreError>(spec().group),
                )
                .expect(
                    "DistGatewayCoreService::session_group",
                    (stranger, receipt().sid),
                    Err::<GroupId, _>(DistGatewayCoreError::NotFound),
                )
        } else {
            script
        };
        let scope = scope();
        let test = dist_gateway_core_service_contract_tests(&script, &scope, request.clone());
        if same_receipt {
            test.await?;
        } else {
            rejects(test, "assertion").await;
        }
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn gateway_harness_checks_idempotency_task_identity_and_access_denial() -> ContractResult {
    use bcs_service_api::application::principal::{CallerContext, IntegrationClient};
    let caller = GatewayCaller {
        tenant: scope().tenant,
        caller: CallerContext::Integration(IntegrationClient {
            client_id: "caller-a".into(),
            scopes: vec![],
        }),
    };
    let unauthorized = GatewayCaller {
        tenant: TenantId("another-tenant".into()),
        caller: CallerContext::Public,
    };
    let request = SubmitInput {
        group: spec().group,
        message_id: RequestId("request-a".into()),
        session: Some(owner().sid),
        task: Some(input().tid),
        interaction: None,
        content: payload(),
    };
    for denied in [
        Err(GatewayError::AccessDenied),
        Err(GatewayError::NotFound),
        Ok(task_view()),
    ] {
        let leaks = denied.is_ok();
        let script = Script::default()
            .expect(
                "DistGatewayService::submit",
                (&caller, &request),
                Ok::<_, GatewayError>(receipt()),
            )
            .expect(
                "DistGatewayService::submit",
                (&caller, &request),
                Ok::<_, GatewayError>(receipt()),
            )
            .expect(
                "DistGatewayService::get_task",
                (&caller, &request.group, input().tid),
                Ok::<_, GatewayError>(task_view()),
            )
            .expect(
                "DistGatewayService::get_task",
                (&unauthorized, &request.group, input().tid),
                denied,
            );
        let another = GroupId(format!("{}-another", request.group.0));
        let script = if leaks {
            script
        } else {
            script.expect(
                "DistGatewayService::get_task",
                (&caller, &another, input().tid),
                Err::<TaskView, _>(GatewayError::NotFound),
            )
        };
        let test =
            dist_gateway_service_contract_tests(&script, &caller, &unauthorized, request.clone());
        if leaks {
            rejects(test, "assertion").await;
        } else {
            test.await?;
        }
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn runtime_harnesses_require_draining_state_after_drain() -> ContractResult {
    let names = [
        (
            "DistWorkerRuntimeService::snapshot",
            "DistWorkerRuntimeService::drain",
        ),
        (
            "DistConnectorRuntimeService::snapshot",
            "DistConnectorRuntimeService::drain",
        ),
        (
            "DistRuntimeControlPort::snapshot",
            "DistRuntimeControlPort::drain",
        ),
    ];
    for (role, (snapshot_method, drain_method)) in names.into_iter().enumerate() {
        for draining in [true, false] {
            let initial = RuntimeSnapshot {
                instance: instance(),
                ready: true,
                draining: false,
                active_work: 0,
                pending_effects: 0,
            };
            let script = Script::default()
                .expect(snapshot_method, (), Ok::<_, RuntimeError>(initial.clone()))
                .expect(
                    drain_method,
                    (Duration::from_secs(if role == 2 { 5 } else { 1 }),),
                    Ok::<_, RuntimeError>(DrainOutcome::Drained),
                )
                .expect(
                    snapshot_method,
                    (),
                    Ok::<_, RuntimeError>(RuntimeSnapshot {
                        draining,
                        ..initial
                    }),
                );
            let test = async {
                match role {
                    0 => dist_worker_runtime_service_contract_tests(&script).await,
                    1 => dist_connector_runtime_service_contract_tests(&script).await,
                    _ => dist_runtime_control_port_contract_tests(&script).await,
                }
            };
            if draining {
                test.await?;
            } else {
                rejects(test, "assertion").await;
            }
            script.done();
        }
    }
    Ok(())
}

#[tokio::test]
async fn agent_harness_keeps_uncertain_send_and_cancel_distinct_from_failure() -> ContractResult {
    let first = StableSendRequest {
        request_id: RequestId("not-sent".into()),
        node: NodeId("n2".into()),
        attempt: 1,
        frozen_history: None,
        context_id: None,
        payload: payload(),
    };
    let second = StableSendRequest {
        request_id: RequestId("ambiguous".into()),
        ..first.clone()
    };
    let cancellation = RequestId("cancel-a".into());
    for uncertain in [true, false] {
        let response = if uncertain {
            SendOutcome::OutcomeUnknown {
                code: "timeout".into(),
            }
        } else {
            SendOutcome::Rejected {
                code: "timeout".into(),
            }
        };
        // The harness's own contract authority: the port is asked under the
        // round, node, attempt and binding of the dispatch it serves.
        let start = contract_authority(&target());
        let cancel = contract_authority(&target());
        let script = Script::default()
            .expect(
                "DistAgentClientPort::start",
                (&start, &first),
                SendOutcome::NotSent {
                    code: "pre-send".into(),
                },
            )
            .expect("DistAgentClientPort::start", (&start, &second), response)
            .expect(
                "DistAgentClientPort::cancel",
                (&cancel, remote(), &cancellation),
                CancelOutcome::OutcomeUnknown {
                    code: "timeout".into(),
                },
            );
        let target = target();
        let remote = remote();
        let test = dist_agent_client_port_contract_tests(
            &script,
            &target,
            first.clone(),
            second.clone(),
            &remote,
            cancellation.clone(),
        );
        if uncertain {
            test.await?;
            script.done();
        } else {
            rejects(test, "assertion").await;
            script.remaining(1);
        }
    }
    Ok(())
}

#[tokio::test]
async fn directory_harness_checks_frozen_definition_and_workflow_revision() -> ContractResult {
    let spec = spec();
    let frozen = FrozenGroupDefinition {
        group: spec.group.clone(),
        workflow: spec.workflow,
        participants: spec.participants,
        limits: spec.limits,
    };
    for stable in [true, false] {
        let mut repeated = workflow();
        if !stable {
            repeated.version += 1;
        }
        let script = Script::default()
            .expect(
                "DistGroupDirectoryPort::resolve",
                (scope(), &frozen.group),
                Ok::<_, GroupDirectoryError>(frozen.clone()),
            )
            .expect(
                "DistGroupDirectoryPort::resolve",
                (scope(), &frozen.group),
                Ok::<_, GroupDirectoryError>(frozen.clone()),
            )
            .expect(
                "DistGroupDirectoryPort::load_workflow",
                (scope(), &frozen.workflow),
                Ok::<_, GroupDirectoryError>(workflow()),
            )
            .expect(
                "DistGroupDirectoryPort::load_workflow",
                (scope(), &frozen.workflow),
                Ok::<_, GroupDirectoryError>(repeated),
            );
        let scope = scope();
        let test = dist_group_directory_port_contract_tests(&script, &scope, &frozen.group);
        if stable {
            test.await?;
        } else {
            rejects(test, "assertion").await;
        }
        script.done();
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// plan W: the workflow-revision harness self-test. A deliberately violating
// in-memory fixture proves the restart/no-fallback vector is not vacuous:
// when a provider "repairs" a deleted pinned revision by falling back to the
// newest publication, the harness rejects it.
// ---------------------------------------------------------------------------
use harness::workflow_revision::{
    publish_v2_restart_loads_pinned_v1, RevisionDamage, WorkflowPublication,
    WorkflowRevisionFixture,
};

struct InMemoryPublication {
    reference: WorkflowRef,
    publication: WorkflowPublication,
    // Mutated by the Corrupted damage; digest checks then fail honestly.
    definition: bcs_domain::collaboration::StateMachineDefinition,
}

#[derive(Default)]
struct FallbackState {
    rows: Vec<InMemoryPublication>,
    pointer: Option<WorkflowRef>,
    counter: u64,
}

struct FallbackDirectoryFixture {
    state: std::sync::Arc<std::sync::Mutex<FallbackState>>,
    fallback_to_newest: bool,
}

/// Deterministic content identity over the publication's serialized bytes.
fn memory_digest(publication: &WorkflowPublication) -> Digest {
    let definition = serde_json::to_string(&publication.definition)
        .expect("fixture definition serializes");
    let participants = serde_json::to_string(&publication.participants)
        .expect("fixture participants serialize");
    let limits = serde_json::to_string(&publication.limits)
        .expect("fixture limits serialize");
    Digest(format!("sha256:{definition}|{participants}|{limits}"))
}

struct FallbackDirectory {
    state: std::sync::Arc<std::sync::Mutex<FallbackState>>,
    fallback_to_newest: bool,
}

#[async_trait]
impl DistGroupDirectoryPort for FallbackDirectory {
    async fn resolve(
        &self,
        _scope: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        let state = self.state.lock().unwrap();
        let reference = state.pointer.clone().ok_or(GroupDirectoryError::NotFound)?;
        let row = state
            .rows
            .iter()
            .find(|row| row.reference == reference)
            .ok_or(GroupDirectoryError::InvalidRevision)?;
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: reference,
            participants: row.publication.participants.clone(),
            limits: row.publication.limits.clone(),
        })
    }

    async fn load_workflow(
        &self,
        _scope: &TaskScope,
        reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        let state = self.state.lock().unwrap();
        let Some(row) = state.rows.iter().find(|row| row.reference == *reference) else {
            // Deliberate violation under test: a missing pinned revision is
            // "repaired" with the newest publication instead of failing
            // closed.
            if self.fallback_to_newest
                && let Some(newest) = state.rows.last()
            {
                return Ok(newest.definition.clone());
            }
            return Err(GroupDirectoryError::InvalidRevision);
        };
        // Byte integrity: the stored (possibly tampered) definition must
        // still hash to the reference digest, or the load fails closed.
        let mut bytes = row.publication.clone();
        bytes.definition = row.definition.clone();
        if memory_digest(&bytes).0 != reference.digest.0 {
            return Err(GroupDirectoryError::InvalidRevision);
        }
        Ok(row.definition.clone())
    }
}

#[async_trait]
impl WorkflowRevisionFixture for FallbackDirectoryFixture {
    async fn publish(
        &self,
        _scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError> {
        let mut state = self.state.lock().unwrap();
        state.counter += 1;
        let reference = WorkflowRef {
            definition_id: definition_id.to_owned(),
            revision: state.counter.to_string(),
            digest: memory_digest(publication),
        };
        state.rows.push(InMemoryPublication {
            publication: publication.clone(),
            definition: publication.definition.clone(),
            reference: reference.clone(),
        });
        state.pointer = Some(reference.clone());
        let _ = group;
        Ok((reference, IdempotentWrite::Applied))
    }

    async fn publish_with_fault(
        &self,
        _fault: harness::workflow_revision::PublishFault,
        _scope: &TaskScope,
        _group: &GroupId,
        _definition_id: &str,
        _publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError> {
        panic!("the restart vector does not use fault injection")
    }

    async fn directory(&self, _generation: u64) -> std::sync::Arc<dyn DistGroupDirectoryPort> {
        std::sync::Arc::new(FallbackDirectory {
            state: self.state.clone(),
            fallback_to_newest: self.fallback_to_newest,
        })
    }

    async fn damage(
        &self,
        _scope: &TaskScope,
        _group: &GroupId,
        reference: &WorkflowRef,
        damage: RevisionDamage,
    ) {
        let mut state = self.state.lock().unwrap();
        match damage {
            RevisionDamage::Deleted => {
                state.rows.retain(|row| &row.reference != reference);
            }
            RevisionDamage::Corrupted => {
                if let Some(row) = state.rows.iter_mut().find(|row| &row.reference == reference) {
                    row.definition = serde_json::from_value(serde_json::json!({"tampered": true}))
                        .expect("tampered definition parses");
                }
            }
            RevisionDamage::OldFormat => {
                panic!("the restart vector does not use old-format damage")
            }
        }
    }

    async fn try_overwrite_same_revision(
        &self,
        _scope: &TaskScope,
        _group: &GroupId,
        _reference: &WorkflowRef,
        _publication: &WorkflowPublication,
    ) -> harness::workflow_revision::SameRevisionWrite {
        panic!("the restart vector does not use the overwrite hook")
    }
}

#[tokio::test]
async fn workflow_revision_harness_rejects_fallback_after_object_loss() -> ContractResult {
    let fixture = FallbackDirectoryFixture {
        state: std::sync::Arc::new(std::sync::Mutex::new(FallbackState::default())),
        fallback_to_newest: true,
    };
    let violation = publish_v2_restart_loads_pinned_v1(&fixture);
    rejects(violation, "never a fallback").await;
    // The same fixture without the fallback passes: the deleted pinned
    // revision fails closed as InvalidRevision.
    let faithful = FallbackDirectoryFixture {
        state: std::sync::Arc::new(std::sync::Mutex::new(FallbackState::default())),
        fallback_to_newest: false,
    };
    publish_v2_restart_loads_pinned_v1(&faithful).await?;
    Ok(())
}
