use super::*;
use harness::dispatch::*;

struct Held {
    script: Script,
    view: DispatchView,
    holder: Holder,
}

#[async_trait]
impl HeldDispatchPort for Held {
    fn view(&self) -> &DispatchView {
        &self.view
    }
    fn holder(&self) -> &Holder {
        &self.holder
    }
    async fn refresh(&mut self) -> Result<DispatchView, HolderError> {
        self.script.call("refresh", ())
    }
    async fn begin_send(
        &mut self,
        operation: SendOperation,
        proof: SendProof,
    ) -> Result<SendPermit, HolderError> {
        self.script.call("begin_send", (operation, proof))
    }
    async fn record_acceptance(
        &mut self,
        evidence: AcceptanceEvidence,
    ) -> Result<Option<CancelIntent>, HolderError> {
        self.script.call("record_acceptance", (evidence,))
    }
    async fn confirm_input(
        &mut self,
        seq: InputSeq,
        evidence: AcceptanceEvidence,
    ) -> Result<IdempotentWrite, HolderError> {
        self.script.call("confirm_input", (seq, evidence))
    }
    async fn record_cancel(
        &mut self,
        observation: CancelObservation,
    ) -> Result<IdempotentWrite, HolderError> {
        self.script.call("record_cancel", (observation,))
    }
    async fn finish(
        &mut self,
        outcome: FinalDispatchOutcome,
        evidence: String,
    ) -> Result<IdempotentWrite, HolderError> {
        self.script.call("finish", (outcome, evidence))
    }
}

#[tokio::test]
async fn dispatch_creation_and_cancel_keep_the_committed_identity() -> ContractResult {
    let fresh = CreateCommittedDispatch {
        source: source(),
        spec: dispatch_view().spec,
    };
    let cancel = CommittedCancelIntent {
        source: source(),
        did: fresh.spec.did.clone(),
        intent: CancelIntent {
            request_id: RequestId("cancel-a".into()),
            reason: None,
        },
    };
    let script = Script::default()
        .expect(
            "DispatchControlPort::create",
            (&fresh,),
            ok(IdempotentWrite::Applied),
        )
        .expect(
            "DispatchControlPort::create",
            (&fresh,),
            ok(IdempotentWrite::AlreadyApplied),
        )
        .expect(
            "DispatchControlPort::get",
            (&fresh.spec.did,),
            ok(Some(DispatchRecord {
                terminal_evidence: None,
                view: dispatch_view(),
                revision: Revision("dispatch-1".into()),
                verification: None,
                quarantined: None,
            })),
        )
        .expect(
            "DispatchControlPort::request_cancel",
            (&cancel,),
            ok(ControlOutcome::Applied),
        )
        .expect(
            "DispatchControlPort::request_cancel",
            (&cancel,),
            ok(ControlOutcome::AlreadyApplied),
        );
    dispatch_control_port_contract_tests(&script, fresh, cancel).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn repeated_claim_must_not_start_a_second_actor() -> ContractResult {
    for duplicate_actor in [false, true] {
        let held = || {
            Box::new(Held {
                script: Script::default(),
                view: dispatch_view(),
                holder: holder(),
            })
        };
        let request = DispatchClaimRequest {
            did: dispatch_view().spec.did,
            me: instance(),
            request_id: RequestId("claim-dispatch-a".into()),
        };
        let second = if duplicate_actor {
            ClaimOutcome::Acquired(held())
        } else {
            ClaimOutcome::OwnedByMe(holder())
        };
        let script = Script::default()
            .expect(
                "DispatchQueuePort::claim",
                (&request,),
                ok(ClaimOutcome::Acquired(held())),
            )
            .expect("DispatchQueuePort::claim", (&request,), ok(second));
        let test = dispatch_queue_port_contract_tests(&script, request);
        if duplicate_actor {
            rejects(test, "same owner must not receive a second actor").await;
        } else {
            test.await?;
        }
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn command_notification_checks_identity_and_repeated_ack() -> ContractResult {
    let delivery = || CommandDelivery {
        did: dispatch_view().spec.did,
        backend: BackendId("fixture".into()),
        consumer_generation: "2".into(),
        opaque: "notification-a".into(),
    };
    let mut script = Script::default()
        .expect(
            "CommandFeedPort::receive",
            (NonZeroU32::MIN, Duration::from_secs(1)),
            ok(vec![delivery()]),
        )
        .expect("CommandFeedPort::ack", (delivery(),), ok(()))
        .expect("CommandFeedPort::ack", (delivery(),), ok(()));
    command_feed_port_contract_tests(&mut script, &dispatch_view().spec.did).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn confirmed_input_must_leave_pending_queue_but_retain_deduplication() -> ContractResult {
    let input = CommittedDispatchInput {
        source: source(),
        did: dispatch_view().spec.did,
        input: PendingInput {
            seq: InputSeq(1),
            input_id: input().input_id,
            business_digest: input().business_digest,
            payload: payload(),
        },
    };
    let accepted = AcceptanceEvidence {
        operation: SendOperation::Input {
            seq: input.input.seq,
            request_id: RequestId("send-input-a".into()),
        },
        remote: remote(),
    };
    for stale_pending in [false, true] {
        let mut view = dispatch_view();
        if stale_pending {
            view.control.pending_input.push(input.input.clone());
        }
        let mut held = Held {
            script: Script::default()
                .expect(
                    "confirm_input",
                    (input.input.seq, &accepted),
                    Ok::<_, HolderError>(IdempotentWrite::Applied),
                )
                .expect("refresh", (), Ok::<_, HolderError>(view)),
            view: dispatch_view(),
            holder: holder(),
        };
        let control = Script::default().expect(
            "DispatchControlPort::push_input",
            (&input,),
            ok(ControlOutcome::AlreadyApplied),
        );
        let test =
            held_dispatch_port_contract_tests(&mut held, &control, input.clone(), accepted.clone());
        if stale_pending {
            rejects(test, "assertion").await;
        } else {
            test.await?;
        }
        held.script.done();
        control.done();
    }
    Ok(())
}
