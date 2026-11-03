use bcs_domain::message_delivery::{
    DeliveryFlowKind, MessageDeliveryStatus, PersistedMessageDelivery,
};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_service_api::application::message_delivery::*;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};

/// Consumer-facing System queue contract: Public/Skip share one source and
/// acceptance never claims downstream delivery. Caller supplies a managed
/// fixture with at least two Bot participants and a canonical Session.
pub async fn system_message_queue_service_contract_tests<T: bcs_service_api::application::system_message::SystemMessageQueueService + ?Sized>(
    service: &T, group: &bcs_domain::Group, session_id: &str,
) -> bcs_service_api::ServiceResult<()> {
    use bcs_domain::{PersistMode, SystemGroupMessage, SystemMessageEventKind};
    let bots: Vec<_> = group.participants.iter().filter(|p| p.is_bot()).collect();
    assert!(bots.len() >= 2);
    let messages = [
        SystemGroupMessage { recipients: vec![bots[0].bot_uuid.clone()], message: "shared notice".into(),
            delivery_type: DeliveryType::Send, persist: PersistMode::Public },
        SystemGroupMessage { recipients: vec![bots[1].bot_uuid.clone()], message: "shared notice".into(),
            delivery_type: DeliveryType::Inject, persist: PersistMode::Skip },
    ];
    let outcome = service.admit(group, session_id, &group.participants, SystemMessageEventKind::BotHiddenNotice, &messages).await?.expect("managed fixture");
    assert_eq!(outcome.recipients.len(), 2);
    assert!(outcome.recipients.iter().all(|(_, r)| r.accepted() && !r.delivered && r.delivery_id.is_some()));
    let invalid = [SystemGroupMessage { recipients: vec![bots[1].bot_uuid.clone()], message: "orphan".into(),
        delivery_type: DeliveryType::Inject, persist: PersistMode::Skip }];
    assert!(service.admit(group, session_id, &group.participants, SystemMessageEventKind::BotHiddenNotice, &invalid).await.is_err());
    Ok(())
}

pub async fn managed_message_delivery_service_contract_tests<
    T: ManagedMessageDeliveryService + ?Sized,
>(
    service: &T,
) -> Result<(), ManagedDeliveryError> {
    let admitted = service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "application-contract".into(),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: 1,
            expire_at_ms: None,
            event: None,
            targets: vec![DeliveryAdmissionTarget { rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: serde_json::json!({"version":1}),
             run_id: None,
}],
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group".into(),
                session_id: "session".into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: serde_json::json!({"text":"contract"}),
                client_msg_id: Some("contract".into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
        })
        .await?;
    let row = &admitted.deliveries[0];
    assert_eq!(row.state.status, MessageDeliveryStatus::Queued);
    let command = DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        caller_cancel: None,
        caller_terminal: None,
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event: DeliveryLifecycleEvent::CancelRequested,
        now_ms: 2,
        request_id: None,
        actor_id: Some("human".into()),
        reply: None,
        transport_context_json: None,
        deadline_at_ms: None,
    };
    let cancelled = service.transition(command.clone()).await?;
    assert_eq!(cancelled.state.status, MessageDeliveryStatus::Cancelled);
    assert!(cancelled.abort_request_id.is_none());
    assert!(service.transition(command).await.is_err());
    service.recover(3).await?;
    assert_eq!(
        service.snapshot(Some("session")).await?[0].state.status,
        MessageDeliveryStatus::Cancelled
    );
    Ok(())
}

pub async fn managed_delivery_preparation_service_contract_tests<
    T: ManagedDeliveryPreparationService + ?Sized,
>(
    service: &T,
    row: &PersistedMessageDelivery,
) -> bcs_service_api::ServiceResult<()> {
    let prepared = service.prepare(row).await?;
    assert_eq!(prepared.command.target_bot_id(), row.target_bot_id);
    assert_eq!(Some(&prepared.command.run_id), row.run_id.as_ref());
    assert!(prepared.command.provider_bypass_headers.is_empty());
    assert!(prepared.transport_context_json.is_object());
    Ok(())
}

/// Driver must provide a fresh managed-delivery service backed by the store under test.
pub async fn managed_run_deadline_contract<T:ManagedMessageDeliveryService + ?Sized>(service:&T) -> Result<(),ManagedDeliveryError> {
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    let admitted=service.admit(AdmitMessageDeliveries {display_message:None,message_id:"deadline-message".into(),flow_kind:DeliveryFlowKind::Group,
        now_ms:100,expire_at_ms:None,event:None,targets:vec![DeliveryAdmissionTarget {rejection:None,target_bot_id:"deadline-bot".into(),kind:DeliveryType::Send,
            max_queued:10,semantic_projection_json:serde_json::json!({"version":1}),run_id:None}],
        message:NewMessage {visibility_domain:bcs_domain::MessageVisibilityDomain::Chat,audience:None,group_id:"deadline-group".into(),session_id:"deadline-session".into(),
            sender_id:"human".into(),sender_type:SenderType::Human,message_type:"chat".into(),content:serde_json::json!({"text":"budget"}),
            client_msg_id:None,owner_bot_id:None,created_at:100,run_id:String::new()}}).await?;
    let queued=&admitted.deliveries[0];let scope=bcs_service_api::BotRunScope {bot_id:queued.target_bot_id.clone(),group_id:queued.group_id.clone(),session_id:queued.session_id.clone()};
    let run=queued.run_id.as_ref().unwrap();
    assert!(service.mark_run_transport_unknown(&scope,run,150).await.is_err(),"unsent work cannot become uncertain");
    assert!(service.reschedule_run_deadline(&scope,run,50_000,200).await.is_err(),"cannot start an unsent Run");
    let started=service.transition(DeliveryTransitionCommand {delivery_id:queued.delivery_id.clone(),expected_state_version:queued.state.state_version,
        event:Event::StartSend,now_ms:200,request_id:None,actor_id:None,reply:None,transport_context_json:Some(serde_json::json!({"version":1})),deadline_at_ms:Some(10_000), caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None}).await?;
    let wrong=bcs_service_api::BotRunScope {session_id:"other".into(),..scope.clone()};
    assert!(service.reschedule_run_deadline(&wrong,run,50_000,300).await.is_err());
    assert!(service.reschedule_run_deadline(&scope,started.request_id.as_deref().unwrap(),50_000,300).await.is_err(),"request alias is not canonical Run");
    service.reschedule_run_deadline(&scope,run,50_000,300).await?;
    let row=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::CanonicalRun {bot:scope.bot_id.clone(),session:scope.session_id.clone(),run:run.clone()}).await?.remove(0);
    assert_eq!(row.state.status,Status::Dispatching);assert_eq!(row.run_deadline_at_ms,Some(50_000));assert_eq!(row.request_id,started.request_id);
    assert_eq!(row.attempt_no,started.attempt_no);assert_eq!(row.transport_context_json,started.transport_context_json);
    assert_eq!(service.active_count(&scope.bot_id).await?,1,"human wait retains execution slot");
    service.reschedule_run_deadline(&scope,run,50_000,400).await?;
    let same=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone())).await?.remove(0);
    assert_eq!(same.state.state_version,row.state.state_version,"identical updates do not publish another version");
    service.reschedule_run_deadline(&scope,run,20_000,500).await?;
    let current=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone())).await?.remove(0);
    assert_eq!(current.run_deadline_at_ms,Some(20_000));
    assert!(service.mark_run_transport_unknown(&wrong,run,550).await.is_err());
    assert!(service.mark_run_transport_unknown(&scope,started.request_id.as_deref().unwrap(),550).await.is_err());
    service.mark_run_transport_unknown(&scope,run,550).await?;
    let current=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone())).await?.remove(0);
    assert_eq!(current.state.status,Status::Unknown);
    assert_eq!(service.active_count(&scope.bot_id).await?,1);
    assert_eq!(current.request_id,started.request_id);assert_eq!(current.attempt_no,started.attempt_no);
    let cancelled=service.transition(DeliveryTransitionCommand {delivery_id:current.delivery_id.clone(),expected_state_version:current.state.state_version,
        event:Event::CancelRequested,now_ms:600,request_id:current.request_id.clone(),actor_id:None,reply:None,transport_context_json:None,deadline_at_ms:None, caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None}).await?;
    assert_eq!(cancelled.state.status,Status::Cancelling);
    assert!(service.reschedule_run_deadline(&scope,run,60_000,700).await.is_err(),"cannot erase cancellation intent");
    service.mark_run_transport_unknown(&scope,run,700).await?;
    let uncertain=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone())).await?.remove(0);
    assert_eq!(uncertain.state.status,Status::CancelUnknown);
    service.mark_run_transport_unknown(&scope,run,710).await?;
    assert_eq!(service.active_count(&scope.bot_id).await?,1);
    let terminal=service.transition(DeliveryTransitionCommand {delivery_id:uncertain.delivery_id.clone(),expected_state_version:uncertain.state.state_version,
        event:Event::Aborted,now_ms:800,request_id:uncertain.request_id.clone(),actor_id:None,reply:None,transport_context_json:None,deadline_at_ms:None, caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None}).await?;
    service.mark_run_transport_unknown(&scope,run,900).await?;
    let final_row=service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Id(row.delivery_id.clone())).await?.remove(0);
    assert_eq!(final_row.state,terminal.state,"late loss observation never revives a terminal");
    assert_eq!(service.active_count(&scope.bot_id).await?,0);
    Ok(())
}
