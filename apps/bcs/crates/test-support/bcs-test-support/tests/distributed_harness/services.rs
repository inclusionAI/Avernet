use super::*;
use harness::services::*;

#[async_trait::async_trait]
impl DistRunnerCoreService for Script {
    async fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        self.call(
            "init",
            (
                context.spec,
                serde_json::to_value(&context.spec.mode_envelope.payload).unwrap(),
                context.now,
            ),
        )
    }
    async fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.call(
            "reduce",
            (
                context.spec,
                serde_json::to_value(&context.spec.mode_envelope.payload).unwrap(),
                context.now,
                state,
                inputs,
            ),
        )
    }
}

#[tokio::test]
async fn runner_harness_detects_nondeterministic_decisions() -> ContractResult {
    let spec = spec();
    let workflow = spec.mode_envelope.payload.clone();
    let context = EvaluationContext {
        spec: &spec,
        config: &workflow,
        tid: &input().tid,
        downstream_generation: 0,
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
        let state = state();
        let check = dist_runner_core_service_contract_tests(&script, &context, &state, &inputs);
        if deterministic {
            check.await?;
        } else {
            rejects(check, "assertion").await;
        }
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn gateway_core_harness_checks_duplicate_receipt() -> ContractResult {
    let request = AdmitTaskCommand {
        input: input(),
        create_session: Some(FrozenSessionProposal {
            target: spec().target,
            mode_envelope: spec().mode_envelope,
            authorized_agents: spec().authorized_agents,
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
                    "DistGatewayCoreService::session_target",
                    (scope(), receipt().sid),
                    Ok::<_, DistGatewayCoreError>(spec().target),
                )
                .expect(
                    "DistGatewayCoreService::session_target",
                    (stranger, receipt().sid),
                    Err::<SessionTarget, _>(DistGatewayCoreError::NotFound),
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
        access: Default::default(),
        tenant: scope().tenant,
        caller: CallerContext::Integration(IntegrationClient {
            client_id: "caller-a".into(),
            scopes: vec![],
        }),
    };
    let unauthorized = GatewayCaller {
        access: Default::default(),
        tenant: TenantId("another-tenant".into()),
        caller: CallerContext::Public,
    };
    let request = SubmitInput {
        target: spec().target,
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
                (&caller, &request.target, input().tid),
                Ok::<_, GatewayError>(task_view()),
            )
            .expect(
                "DistGatewayService::get_task",
                (&unauthorized, &request.target, input().tid),
                denied,
            );
        let another = SessionTarget::Group { group: GroupId(format!("{}-another", request.target.id())) };
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
        let script = Script::default()
            .expect(
                "DistAgentClientPort::start",
                (target(), &first),
                SendOutcome::NotSent {
                    code: "pre-send".into(),
                },
            )
            .expect("DistAgentClientPort::start", (target(), &second), response)
            .expect(
                "DistAgentClientPort::cancel",
                (target(), remote(), &cancellation),
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
    let frozen = FrozenSessionProposal {
        target: spec.target.clone(),
        mode_envelope: spec.mode_envelope,
        authorized_agents: spec.authorized_agents,
        limits: spec.limits,
    };
    for stable in [true, false] {
        let mut repeated = frozen.mode_envelope.payload.clone();
        if !stable {
            repeated = Payload::Inline(serde_json::json!("changed"));
        }
        let script = Script::default()
            .expect(
                "DistSessionDirectoryPort::resolve",
                (scope(), &frozen.target),
                Ok::<_, DirectoryError>(frozen.clone()),
            )
            .expect(
                "DistSessionDirectoryPort::resolve",
                (scope(), &frozen.target),
                Ok::<_, DirectoryError>(frozen.clone()),
            )
            .expect(
                "DistSessionDirectoryPort::load_mode_config",
                (scope(), &frozen.mode_envelope),
                Ok::<_, DirectoryError>(frozen.mode_envelope.payload.clone()),
            )
            .expect(
                "DistSessionDirectoryPort::load_mode_config",
                (scope(), &frozen.mode_envelope),
                Ok::<_, DirectoryError>(repeated),
            );
        let scope = scope();
        let test = dist_session_directory_port_contract_tests(&script, &scope, &frozen.target);
        if stable {
            test.await?;
        } else {
            rejects(test, "assertion").await;
        }
        script.done();
    }
    Ok(())
}
