//! Restore synchronous delivery-error feedback after queued Send failures.
use crate::BcsMessageFlow;
use bcs_domain::{
    DeliveryType, GroupStrategy, MessageAudience, MessageVisibilityDomain, SystemMessageEvent,
    message_delivery::{DeliveryFlowKind, MessageDeliveryStatus, PersistedMessageDelivery},
};
use bcs_service_api::{
    BCS_SYSTEM_MESSAGE, FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryTarget,
    ServiceError, ServiceResult,
};

const DELIVERY_FAILED_NOTICE: &str = "消息投递失败，请稍后重试。";

pub(crate) async fn publish(
    flow: &BcsMessageFlow,
    rows: &[&PersistedMessageDelivery],
) -> ServiceResult<()> {
    let failed: Vec<_> = rows.iter().copied().filter(|row| {
        row.state.kind == DeliveryType::Send
            && row.state.status == MessageDeliveryStatus::Failed
            && row.last_error_code.as_deref() != Some(crate::managed_delivery::BOT_TERMINAL_ERROR_CODE)
            && matches!(row.flow_kind, DeliveryFlowKind::Group | DeliveryFlowKind::System)
    }).collect();
    let Some(row) = failed.first().copied() else { return Ok(()); };
    let group = flow.group.try_get(&row.group_id).await?
        .ok_or_else(|| ServiceError::GroupNotFound(row.group_id.clone()))?;
    if row.flow_kind == DeliveryFlowKind::Group {
        let Some(system_message) = &flow.system_message else { return Ok(()); };
        let Some(repository) = &flow.message_repo else { return Ok(()); };
        let Some(source) = repository.get_message_by_id(&row.session_id, &row.source_message_id)
            .await.map_err(|_| ServiceError::InternalError("failure notice source lookup failed".into()))?
        else { return Ok(()); };
        let mut offline_names = Vec::new();
        let mut delivery_failed = false;
        for row in failed {
            let is_online = match flow.registry.resolve_delivery_target(&row.target_bot_id).await {
                Ok(target) => flow.bot_delivery.is_available(&target).await,
                Err(_) => false,
            };
            if is_online {
                delivery_failed = true;
            } else {
                offline_names.push(group.get_participant(&row.target_bot_id)
                    .and_then(|p| p.bot_name.clone()).unwrap_or_else(|| row.target_bot_id.clone()));
            }
        }
        // GenericNotification uses the existing history and visibility policy.
        let mut notices = Vec::new();
        if !offline_names.is_empty() { notices.push(format!("Bot {} 已离线", offline_names.join("、"))); }
        if delivery_failed { notices.push(DELIVERY_FAILED_NOTICE.to_string()); }
        for message in notices {
            system_message.notify(&group.id, SystemMessageEvent::GenericNotification {
                group_id: group.id.clone(), message,
                receivers: group.participants.iter()
                    .filter(|p| p.is_bot() && p.bot_uuid != source.sender_id).cloned().collect(),
            }, &row.session_id, &group.participants).await?;
        }
        return Ok(());
    }
    // System-context failure is a public, content-free notice in the immediate
    // dispatcher too. Do not expose the private context or transport response.
    let visibility_domain = match group.group_strategy {
        GroupStrategy::Chat => MessageVisibilityDomain::Chat,
        GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
        GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
    };
    flow.frontend_delivery.publish(FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::Session { session_id: row.session_id.clone() },
        event_json: bcs_protocol::frontend::build_frontend_system_event_frame(
            &row.group_id, DELIVERY_FAILED_NOTICE, &row.session_id, BCS_SYSTEM_MESSAGE,
            &uuid::Uuid::new_v4().to_string(), bcs_protocol::now_ms(),
        ),
        delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None, exclude_conn_id: None, visibility_domain,
        audience: (visibility_domain != MessageVisibilityDomain::Chat).then_some(MessageAudience::Public),
    }).await?;
    Ok(())
}
