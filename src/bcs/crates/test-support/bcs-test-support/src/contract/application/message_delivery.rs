use bcs_domain::message_delivery::{
    DeliveryFlowKind, MessageDeliveryStatus, PersistedMessageDelivery,
};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_service_api::application::message_delivery::*;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};

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
