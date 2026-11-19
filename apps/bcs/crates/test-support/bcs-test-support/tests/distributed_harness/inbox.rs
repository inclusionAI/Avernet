use super::*;
use harness::inbox::*;

fn producer_script() -> (Script, AdmitRequest, AdmitRequest) {
    let fresh = admission();
    let mut changed = fresh.clone();
    changed.input.business_digest = Digest("different-digest".into());
    changed.input.body = InputBody::User {
        payload: Payload::Inline(serde_json::json!("different")),
    };
    let script = Script::default()
        .expect(
            "InboxProducerPort::admit",
            (&fresh,),
            ok(AdmitOutcome::Accepted(receipt())),
        )
        .expect(
            "InboxProducerPort::admit",
            (&fresh,),
            ok(AdmitOutcome::Accepted(receipt())),
        )
        .expect(
            "InboxProducerPort::admit",
            (&changed,),
            ok(AdmitOutcome::ContentConflict),
        );
    (script, fresh, changed)
}

#[tokio::test]
async fn producer_checks_replay_identity_and_content_conflict() -> ContractResult {
    let (script, fresh, changed) = producer_script();
    inbox_producer_port_contract_tests(&script, fresh, changed).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn producer_rejects_a_changed_retry_receipt_and_changed_content_success() {
    for index in [1, 2] {
        let (script, fresh, changed) = producer_script();
        // A retry must answer with the same receipt; changed content must not succeed.
        let mut other = receipt();
        other.tid = Tid("another-task".into());
        script.replace(index, ok(AdmitOutcome::Accepted(other)));
        rejects(
            inbox_producer_port_contract_tests(&script, fresh, changed),
            "assertion",
        )
        .await;
        script.remaining(2 - index);
    }
}

#[tokio::test]
async fn producer_propagates_ambiguous_storage_outcome() {
    let (script, fresh, changed) = producer_script();
    let error = StoreError::OutcomeUnknown {
        operation_id: "input-a".into(),
    };
    script.replace(0, Err::<AdmitOutcome, _>(error.clone()));
    let actual = inbox_producer_port_contract_tests(&script, fresh, changed)
        .await
        .unwrap_err();
    assert_eq!(actual.downcast_ref::<StoreError>(), Some(&error));
    script.remaining(2);
}

#[tokio::test]
async fn consumer_checks_durable_coverage_and_repeat_confirmation() -> ContractResult {
    let delivery = DeliveryReceipt {
        backend: BackendId("fixture".into()),
        consumer_generation: "consumer-2".into(),
        opaque: "delivery-a".into(),
    };
    let proof = proof();
    let script = Script::default()
        .expect(
            "InboxConsumerPort::enumerate_pending",
            (
                owner().shard,
                PageRequest {
                    after: None,
                    limit: NonZeroU32::MAX,
                },
            ),
            ok(Page {
                items: vec![owner().sid],
                next: None,
            }),
        )
        .expect(
            "InboxConsumerPort::confirm_committed",
            (&delivery, &proof),
            ok(()),
        )
        .expect(
            "InboxConsumerPort::confirm_committed",
            (&delivery, &proof),
            ok(()),
        )
        .expect(
            "InboxConsumerPort::has_unconsumed",
            (&proof.sid, proof.consumed.as_ref()),
            ok(false),
        );
    inbox_consumer_port_contract_tests(&script, owner().shard, &delivery, &proof).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn feed_watches_from_retained_start_and_checks_returned_input() -> ContractResult {
    let expected = input();
    let mut script = Script::default()
        .expect(
            "ShardFeedPort::watch",
            (&expected.sid, None::<InboxPos>),
            ok(()),
        )
        .expect(
            "ShardFeedPort::next",
            (
                ReadLimits {
                    max_sessions: NonZeroU32::MIN,
                    max_entries: NonZeroU32::MIN,
                    max_bytes: NonZeroU32::MAX,
                },
                Duration::from_secs(1),
            ),
            ok(FeedBatch {
                sessions: vec![SessionBatch {
                    sid: expected.sid.clone(),
                    entries: vec![expected.clone()],
                    read_through: position(),
                    delivery: DeliveryReceipt {
                        backend: BackendId("fixture".into()),
                        consumer_generation: "2".into(),
                        opaque: "d".into(),
                    },
                }],
                hints: vec![],
            }),
        )
        .expect("ShardFeedPort::unwatch", (&expected.sid,), ok(()));
    shard_feed_port_contract_tests(&mut script, &expected).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn scheduling_detects_a_lost_activation_race() -> ContractResult {
    for response in [SettleOutcome::NeedsRecheck, SettleOutcome::Applied] {
        let observed = ScheduleToken {
            sid: owner().sid,
            owner_epoch: owner().owner.epoch,
            opaque: "schedule-before-arrival".into(),
        };
        let script = Script::default()
            .expect(
                "InboxSchedulingPort::observe",
                (owner(),),
                ok(ScheduleObservation::Observed(observed.clone())),
            )
            .expect(
                "InboxProducerPort::admit",
                (admission(),),
                ok(AdmitOutcome::Accepted(receipt())),
            )
            .expect(
                "InboxSchedulingPort::settle",
                (SettleSession {
                    owner: owner(),
                    observed,
                    next_check: None,
                },),
                ok(response.clone()),
            );
        let owner = owner();
        let test = inbox_scheduling_port_contract_tests(&script, &script, &owner, admission());
        if response == SettleOutcome::NeedsRecheck {
            test.await?;
        } else {
            rejects(test, "assertion").await;
        }
        script.done();
    }
    Ok(())
}
