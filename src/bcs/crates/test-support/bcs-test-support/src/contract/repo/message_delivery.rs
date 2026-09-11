use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::port::repo::message_delivery::*;

fn command(id: &str, targets: &[(&str, DeliveryType)]) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 100,
        expire_at_ms: Some(10000),
        event: None,
        message: NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "contract-group".into(),
            session_id: "contract-group:abcd1234".into(),
            sender_id: "human_contract".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 100,
            run_id: String::new(),
            content: serde_json::json!({"text":"中文正文", "mentions":["A"], "attachments":[{
                "attachment_id":"image", "type":"image", "file_name":"图片.png",
                "url":"https://example.invalid/file?signature=retained", "expires_at":1234
            }]}),
        },
        targets: targets
            .iter()
            .map(|(bot, kind)| DeliveryAdmissionTarget {
                target_bot_id: (*bot).into(),
                kind: *kind,
                max_queued: 1,
                semantic_projection_json: serde_json::json!({"version":1}),
            })
            .collect(),
    }
}

pub async fn message_delivery_repo_port_contract_tests<
    T: MessageDeliveryRepoPort + MessageRepoPort,
>(
    repo: &T,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut policy = repo.load_policy().await?;
    assert_eq!(policy.version, 0);
    assert!(!policy.policy.needs_scheduler());
    policy.version = 1;
    policy.updated_by = "contract-admin".into();
    policy.updated_at_ms = 100;
    repo.replace_policy(0, policy.clone()).await?;
    assert_eq!(repo.load_policy().await?, policy);
    assert!(matches!(repo.replace_policy(0, policy.clone()).await, Err(MessageDeliveryRepoError::Conflict)));
    policy.version = 2;
    policy.policy.pause_dispatch = true;
    repo.replace_policy(1, policy.clone()).await?;
    assert_eq!(repo.load_policy().await?, policy);
    let mut invalid = policy.clone();
    invalid.version = 3;
    invalid.policy.defaults.max_running = 0;
    assert!(repo.replace_policy(2, invalid).await.is_err());
    assert_eq!(repo.load_policy().await?, policy);
    let context = repo
        .admit(command("context", &[("A", DeliveryType::Inject)]))
        .await?;
    assert_eq!(context.deliveries[0].state.status, Status::PendingContext);
    assert!(context.deliveries[0].run_id.is_none());
    let first = repo
        .admit(command(
            "first",
            &[("A", DeliveryType::Send), ("B", DeliveryType::Send)],
        ))
        .await?;
    assert_eq!(first.deliveries.len(), 2);
    assert!(
        first
            .deliveries
            .iter()
            .all(|d| d.source_message_id == first.message.message_id)
    );
    assert_eq!(first.message.session_seq, context.message.session_seq + 1);
    assert_eq!(repo.queued_bots("", 1).await?, vec!["A"]);
    assert_eq!(repo.queued_bots("A", 32).await?, vec!["B"]);
    assert!(repo.queued_bots("B", 32).await?.is_empty());
    assert_eq!(repo.active_count("A").await?, 0);
    let heads = repo.queued_heads("A", "", 8).await?;
    assert_eq!(heads.len(), 1);
    assert_eq!(heads[0].delivery_id, first.deliveries[0].delivery_id);
    assert!(repo.queued_heads("A", &heads[0].session_id, 8).await?.is_empty());
    assert_eq!(repo.lookup(DeliveryLookup::Bound(first.deliveries[0].delivery_id.clone())).await?.len(), 1);
    assert_eq!(repo.lookup(DeliveryLookup::Message(first.message.message_id.clone())).await?.len(), 2);
    let stats = repo.queue_statistics().await?;
    assert_eq!(stats.iter().filter(|s| s.status == "queued").map(|s| s.count).sum::<u64>(), 2);
    assert_eq!(stats.iter().filter(|s| s.status == "bound").map(|s| s.count).sum::<u64>(), 1);
    assert_eq!(repo.work_batch(DeliveryWorkBatch::Expired, 10_000, "", 1).await?.len(), 1);
    assert!(repo.work_batch(DeliveryWorkBatch::Recovery, 10_000, "", 200).await?.is_empty());
    assert_eq!(repo.get_messages_by_ids(&first.message.session_id, &[first.message.message_id.clone(), "context".into()]).await?.len(), 2);
    let rows = repo.list_deliveries(None).await?;
    let bound = rows
        .iter()
        .find(|d| d.source_message_id == "context")
        .ok_or("missing context")?;
    assert_eq!(bound.state.status, Status::Bound);
    let bounded = repo.bounded_contexts(&first.deliveries[0].delivery_id, 1).await?;
    assert_eq!(bounded.total, 1);
    assert_eq!(bounded.rows.len(), 1);
    assert_eq!(bounded.rows[0].delivery_id, bound.delivery_id);
    assert_eq!(repo.bounded_contexts(&first.deliveries[0].delivery_id, 0).await?.total, 1);
    assert!(repo.bounded_contexts(&first.deliveries[0].delivery_id, 0).await?.rows.is_empty());
    assert_eq!(
        bound.bound_to_delivery_id.as_ref(),
        Some(&first.deliveries[0].delivery_id)
    );
    let fetched = repo
        .get_message_by_id(&first.message.session_id, &first.message.message_id)
        .await?
        .ok_or("missing canonical message")?;
    assert_eq!(fetched.content, first.message.content);
    assert!(
        fetched.content["attachments"][0]["url"]
            .as_str()
            .ok_or("missing URL")?
            .contains("signature=retained")
    );

    let duplicate = repo
        .admit(command("first", &[("C", DeliveryType::Send)]))
        .await?;
    assert!(duplicate.duplicate);
    assert_eq!(duplicate.message.session_seq, first.message.session_seq);
    assert_eq!(duplicate.deliveries.len(), 2);
    assert!(duplicate.deliveries.iter().all(|d| d.target_bot_id != "C"));

    let overflow = repo
        .admit(command(
            "overflow",
            &[
                ("A", DeliveryType::Send),
                ("C", DeliveryType::Send),
                ("D", DeliveryType::Inject),
            ],
        ))
        .await?;
    assert_eq!(
        overflow.deliveries[0].state.status,
        Status::RejectedCapacity
    );
    assert_eq!(overflow.deliveries[1].state.status, Status::Queued);
    assert_eq!(overflow.deliveries[2].state.status, Status::PendingContext);

    let mut cancelled = first.deliveries[0].clone();
    cancelled.state.status = Status::Cancelled;
    cancelled.state.state_version += 1;
    cancelled.context_selection_json = Some(serde_json::json!({"version":1,"max_messages":24,"max_bytes":131072,"bound_count":1,"history_bytes":100,
        "selected":[{"delivery_id":bound.delivery_id,"state_version":bound.state.state_version,"body_start":0}]}));
    let good = DeliveryCompareAndSet {
        expected_state_version: 1,
        delivery: cancelled.clone(),
    };
    let mut stale = first.deliveries[1].clone();
    stale.state.state_version = 99;
    let failure = repo
        .commit_transition(
            vec![
                good.clone(),
                DeliveryCompareAndSet {
                    expected_state_version: 98,
                    delivery: stale,
                },
            ],
            Some(command("rollback-reply", &[("E", DeliveryType::Send)])),
        )
        .await;
    assert!(matches!(failure, Err(MessageDeliveryRepoError::Conflict)));
    assert!(
        repo.get_message_by_id(&first.message.session_id, "rollback-reply")
            .await?
            .is_none()
    );
    let after_failure = repo.list_deliveries(None).await?;
    assert_eq!(
        after_failure
            .iter()
            .find(|d| d.delivery_id == cancelled.delivery_id)
            .ok_or("missing delivery")?
            .state
            .status,
        Status::Queued
    );

    let mut released = bound.clone();
    released.state.status = Status::PendingContext;
    released.state.state_version += 1;
    released.bound_to_delivery_id = None;
    let reply = repo
        .commit_transition(
            vec![
                good,
                DeliveryCompareAndSet {
                    expected_state_version: bound.state.state_version,
                    delivery: released,
                },
            ],
            Some(command("committed-reply", &[("A", DeliveryType::Send)])),
        )
        .await?
        .ok_or("missing reply")?;
    assert_eq!(reply.deliveries[0].state.status, Status::Queued);
    assert_eq!(repo.get_delivery(&cancelled.delivery_id).await?.ok_or("missing selected carrier")?.context_selection_json, cancelled.context_selection_json);
    let rows = repo.list_deliveries(None).await?;
    let rebound = rows
        .iter()
        .find(|d| d.delivery_id == bound.delivery_id)
        .ok_or("missing rebound")?;
    assert_eq!(
        rebound.bound_to_delivery_id.as_ref(),
        Some(&reply.deliveries[0].delivery_id)
    );
    assert_eq!(rebound.state.state_version, bound.state.state_version + 2);
    let mut control_ids = Vec::new();
    for category in 0..4 {
        let bot = format!("control-bot-{category}");
        let id = format!("control-message-{category}");
        let mut row = repo.admit(command(&id, &[(&bot, DeliveryType::Send)])).await?.deliveries.remove(0);
        let expected_state_version = row.state.state_version;
        row.state.state_version += 1;
        row.state.may_have_been_sent = true;
        row.state.status = match category { 0 => Status::Dispatching, 1 => Status::Running, _ => Status::Cancelling };
        row.run_deadline_at_ms = Some(500);
        row.cancel_deadline_at_ms = Some(500);
        if category == 3 { row.abort_request_id = Some("abort-control".into()); }
        control_ids.push(row.delivery_id.clone());
        repo.commit_transition(vec![DeliveryCompareAndSet { expected_state_version, delivery: row }], None).await?;
    }
    let due = repo.work_batch(DeliveryWorkBatch::Control, 500, "ignored-cursor", 4).await?;
    assert_eq!(due.iter().map(|d| d.delivery_id.clone()).collect::<Vec<_>>(), control_ids);
    let not_due = repo.work_batch(DeliveryWorkBatch::Control, 499, "", 4).await?;
    assert_eq!(not_due.len(), 1); // Only the pending abort needs no deadline.
    assert_eq!(not_due[0].delivery_id, control_ids[2]);
    assert_eq!(repo.work_batch(DeliveryWorkBatch::Control, 499, "", 1).await?.len(), 1);
    assert!(repo.work_batch(DeliveryWorkBatch::Control, 500, "", 0).await?.is_empty());
    run_reply_contract(repo).await?;
    Ok(())
}

async fn run_reply_contract<T: MessageDeliveryRepoPort + MessageRepoPort>(repo: &T) -> Result<(), Box<dyn std::error::Error>> {
    let mut summary = command("run-summary", &[("reply-target", DeliveryType::Inject)]);
    summary.message.message_type = "run_reply".into();
    summary.message.sender_id = "reply-bot".into();
    summary.message.sender_type = SenderType::Bot;
    summary.message.run_id = "shared-run".into();
    summary.message.content = serde_json::json!({"text":"before\nafter"});
    summary.message.created_at = 200;
    summary.message.visibility_domain = bcs_domain::MessageVisibilityDomain::ManagerWorker;
    summary.message.audience = Some(bcs_domain::MessageAudience::directed(["reply-bot", "viewer"]).unwrap());
    let mut prefix = summary.message.clone();
    prefix.message_type = "chat".into(); prefix.content = serde_json::json!("before");
    prefix.client_msg_id = Some("prefix".into());
    let prefix = repo.append_message(prefix).await?;
    let mut display = summary.message.clone();
    display.message_type = "chat".into(); display.content = serde_json::json!("after");
    display.client_msg_id = Some("display".into());
    summary.display_message = Some(DeliveryDisplayMessage { message_id: "run-display".into(), message: display, event: None });
    let source = repo.admit(command("run-input", &[("reply-bot", DeliveryType::Send)])).await?;
    let mut finished = source.deliveries[0].clone();
    finished.state.status = Status::Completed; finished.state.state_version += 1;
    let stale = DeliveryCompareAndSet { expected_state_version: 99, delivery: finished.clone() };
    let before = repo.get_current_seq(&summary.message.session_id).await?;
    // SQL primary-key failure occurs after sequence allocation and the first
    // (display) INSERT. The whole transaction must still roll back.
    let mut collision = summary.clone();
    collision.message_id = source.message.message_id.clone();
    collision.display_message.as_mut().unwrap().message_id = "failed-display".into();
    assert!(repo.admit(collision).await.is_err());
    assert!(repo.get_message_by_id(&summary.message.session_id, "failed-display").await?.is_none());
    assert_eq!(repo.get_current_seq(&summary.message.session_id).await?, before);
    assert!(repo.commit_transition(vec![stale], Some(summary.clone())).await.is_err());
    assert!(repo.get_message_by_id(&summary.message.session_id, "run-display").await?.is_none());
    assert!(repo.get_message_by_id(&summary.message.session_id, "run-summary").await?.is_none());
    assert_eq!(repo.get_current_seq(&summary.message.session_id).await?, before);
    let committed = repo.commit_transition(vec![DeliveryCompareAndSet { expected_state_version: 1, delivery: finished }], Some(summary.clone())).await?.unwrap();
    assert_eq!(committed.message.session_seq, before + 2);
    assert_eq!(committed.deliveries.len(), 1);
    assert_eq!(committed.deliveries[0].source_message_id, "run-summary");
    for id in ["run-summary", "run-display"] {
        let stored = repo.get_message_by_id(&summary.message.session_id, id).await?.unwrap();
        assert_eq!(stored.visibility_domain, Some(summary.message.visibility_domain));
        assert_eq!(stored.audience, summary.message.audience);
    }
    for actor in ["viewer", "unrelated"] {
        let page = repo.list_session_history(&summary.message.session_id, bcs_domain::MessageOwnerFilter::Any, None,
            Some(bcs_domain::HumanMessageView { actor_id: actor.into(), scope: bcs_domain::MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false }), None, 100).await?;
        assert_eq!(page.messages.iter().any(|m| m.message_id == "run-display"), actor == "viewer");
        assert!(page.messages.iter().all(|m| m.message_type != "run_reply"));
    }
    assert_eq!(repo.get_message_by_id(&summary.message.session_id, "run-display").await?.unwrap().session_seq, before + 1);
    assert!(repo.admit(summary.clone()).await?.duplicate);
    assert_eq!(repo.get_current_seq(&summary.message.session_id).await?, before + 2);
    let history = repo.list_session_history(&summary.message.session_id, bcs_domain::MessageOwnerFilter::Any, None, None, None, 1).await?;
    assert_eq!(history.messages.len(), 1); assert_eq!(history.messages[0].message_id, "run-display"); assert!(history.has_more);
    let next = repo.list_session_history(&summary.message.session_id, bcs_domain::MessageOwnerFilter::Any, None, None,
        Some((history.messages[0].created_at, history.messages[0].session_seq)), 1).await?;
    assert_eq!(next.messages[0].message_id, prefix.message_id);
    for kind in [None, Some("run_reply".to_owned())] {
        let page = repo.query_messages(bcs_domain::MessageQuery {
            group_id: summary.message.group_id.clone(), session_id: summary.message.session_id.clone(), cursor: None,
            limit: 1, keyword: None, sender_id: None, message_type: kind.clone(), owner_filter: bcs_domain::MessageOwnerFilter::Any,
            time_range: None, visible_from_seq: None, human_view: None,
        }).await?;
        if kind.is_some() { assert!(page.messages.is_empty()); } else { assert_eq!(page.messages[0].message_id, "run-display"); }
    }
    let mut other = summary.message.clone(); other.sender_id = "another-bot".into(); other.message_type = "chat".into();
    other.client_msg_id = Some("other".into()); repo.append_message(other).await?;
    let mut tool = summary.message.clone(); tool.message_type = "tool_call".into(); tool.client_msg_id = Some("tool".into()); repo.append_message(tool).await?;
    let segments = repo.run_chat_segments(&summary.message.session_id, "reply-bot", "shared-run").await?;
    assert_eq!(segments.iter().map(|m| m.content.as_str().unwrap()).collect::<Vec<_>>(), vec!["before", "after"]);
    assert!(repo.run_chat_segments("other-session", "reply-bot", "shared-run").await?.is_empty());
    Ok(())
}
