//! Direct A2A queue admission and ChatRun projection. No transport I/O at admission.
use super::*;
use bcs_domain::{DeliveryType, MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery, MessageDeliveryStatus as Status};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget, DeliveryLookup};
use bcs_service_api::{DeliveryTransitionCommand};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct DirectProjection {
    pub version: u32,
    pub from_bot_id: String,
    pub from_actor_id: String,
    pub sender_name: String,
    pub target_tags: Vec<String>,
    pub caller_wait_mode: Option<String>,
    pub organization_code: Option<String>,
    pub client: Option<String>,
    pub expires_at_ms: u64,
    pub provider_route_headers: Vec<(String, String)>,
    pub policy_version: u64,
}

pub(crate) fn invalid(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }

impl A2aChat {
    pub fn with_session_management(mut self, service: Arc<dyn bcs_service_api::SessionManagementService>) -> Self {
        self.session_management = Some(service); self
    }

    pub(crate) fn bind_queue(&self, flow: &Arc<crate::BcsMessageFlow>) {
        let _ = self.queue_owner.set(Arc::downgrade(flow));
    }

    pub(crate) fn queue_flow(&self) -> Option<Arc<crate::BcsMessageFlow>> {
        self.queue_owner.get().and_then(std::sync::Weak::upgrade)
    }

    pub(crate) async fn queue_enabled(&self, bot: &str) -> bool {
        let Some(flow) = self.queue_flow() else { return false; };
        let Some(live) = &flow.delivery_policy else { return false; };
        live.snapshot.read().await.policy.manages_direct_a2a(bot)
    }

    pub(crate) async fn guard_legacy(&self, bot: &str, session: &str) -> ServiceResult<()> {
        let Some(flow) = self.queue_flow() else { return Ok(()); };
        let Some(service) = &flow.managed_deliveries else { return Ok(()); };
        let rows = service.lookup(DeliveryLookup::LanePending { bot: bot.into(), session: session.into() }).await
            .map_err(|_| invalid("queue drain lookup failed"))?;
        if !rows.is_empty() { return Err(ServiceError::Conflict("queue_draining".into())); }
        Ok(())
    }

    pub(crate) async fn admit_direct(&self, cmd: &A2aChatCommand, from: &str, run: &str, session: &str, expires: u64) -> ServiceResult<A2aChatOutcome> {
        let flow = self.queue_flow().ok_or_else(|| invalid("direct queue unavailable"))?;
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("direct queue unavailable"))?;
        let live = flow.delivery_policy.as_ref().ok_or_else(|| invalid("direct queue policy unavailable"))?;
        let policy = live.snapshot.read().await.clone();
        if !policy.policy.manages_direct_a2a(&cmd.target_bot_id)
            || !live.scheduler_available.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(invalid("direct queue admission unavailable"));
        }
        let message_id = uuid::Uuid::new_v4().to_string();
        let now = now_ms();
        let target = self.registry.resolve_delivery_target(&cmd.target_bot_id).await?;
        let headers: Vec<_> = cmd.provider_bypass_headers.iter().filter(|(name, _)| target.is_http_provider()
            || flow.queue_persistable_headers.iter().any(|allowed| allowed.eq_ignore_ascii_case(name))).cloned().collect();
        let snapshot = bcs_config_api::queued_provider_headers::snapshot(&headers, &flow.queue_persistable_headers);
        let rejection = snapshot.as_ref().err().map(|_| bcs_service_api::port::repo::message_delivery::DeliveryAdmissionRejection::ProviderHeadersUnsupported);
        let projection = DirectProjection {
            version: 1, from_bot_id: from.into(), from_actor_id: cmd.from_actor_id.clone().unwrap_or_else(|| from.into()),
            sender_name: self.sender_display_name(from).await, target_tags: cmd.tags.clone(),
            caller_wait_mode: cmd.caller_wait_mode.clone(), organization_code: cmd.organization_code.clone(),
            client: cmd.client.clone(), expires_at_ms: expires, provider_route_headers: snapshot.unwrap_or_default(), policy_version: policy.version,
        };
        let mut record = ChatRunRecord::new(run.into(), cmd.target_bot_id.clone(), from.into(), session.into(), now, expires,
            cmd.client.clone(), cmd.response_mode, ChatRunCompletionPolicy::WaitForFinal);
        // Direct has exactly one Send, so its delivery uses the preallocated run ID.
        record.delivery_id = Some(run.into());
        record.source_message_id = Some(message_id.clone());
        self.run_store.create(record.clone()).await.map_err(|_| invalid("cannot persist chat run"))?;
        let expires = i64::try_from(expires).map_err(|_| invalid("invalid direct deadline"))?;
        let command = AdmitMessageDeliveries {
            display_message: None, message_id,
            message: NewMessage {
                group_id: String::new(), session_id: session.into(), sender_id: from.into(), sender_type: SenderType::Bot,
                message_type: "direct_a2a_request".into(), content: serde_json::json!({"text":cmd.message}),
                client_msg_id: Some(format!("direct-a2a:{run}")), owner_bot_id: Some(cmd.target_bot_id.clone()),
                visibility_domain: MessageVisibilityDomain::DirectA2a,
                audience: Some(MessageAudience::directed([from.to_owned(), cmd.target_bot_id.clone()]).map_err(invalid)?),
                created_at: now, run_id: run.into(),
            },
            flow_kind: DeliveryFlowKind::DirectA2a,
            targets: vec![DeliveryAdmissionTarget { rejection, target_bot_id: cmd.target_bot_id.clone(), kind: DeliveryType::Send,
                max_queued: policy.policy.bot(&cmd.target_bot_id).max_queued,
                semantic_projection_json: serde_json::to_value(projection).map_err(|_| invalid("direct projection encoding failed"))? }],
            now_ms: now as i64, expire_at_ms: Some(policy.policy.queue_ttl_ms.map_or(expires, |ttl| expires.min((now as i64).saturating_add(ttl as i64)))), event: None,
        };
        // Independent replicas can lose the session-sequence CAS. Reuse the
        // same message/run identity after rollback; never repeat transport I/O.
        let mut attempt = 0;
        let queued = loop {
            let result = service.admit(command.clone()).await;
            if matches!(&result, Err(bcs_service_api::ManagedDeliveryError::Repository(
                bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Conflict))) && attempt < 3 {
                tokio::time::sleep(Duration::from_millis(10 << attempt)).await;
                attempt += 1;
                continue;
            }
            break result;
        };
        match queued {
            Ok(admitted) => {
                let row = admitted.deliveries.first().ok_or_else(|| invalid("direct admission result missing"))?;
                let record = self.reconcile_direct(row).await?;
                Ok(A2aChatOutcome { run_id: run.into(), status: record.state.as_str().into(), response: None })
            }
            Err(error) => {
                // A transport/storage error may hide a committed admission. Preserve
                // Pending for recovery; only definitive rejection can terminate now.
                if matches!(error, bcs_service_api::ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Invalid(_))) {
                    let mut failed = record.clone(); failed.state = ChatRunState::Failed; failed.error_message = Some("admission_failed".into());
                    self.run_store.save_managed(&record, failed).await?;
                }
                Err(invalid("direct admission failed"))
            }
        }
    }

    pub(crate) async fn managed_row(&self, record: &ChatRunRecord) -> ServiceResult<Option<PersistedMessageDelivery>> {
        let Some(id) = &record.delivery_id else { return Ok(None); };
        let flow = self.queue_flow().ok_or_else(|| invalid("direct queue unavailable"))?;
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("direct queue unavailable"))?;
        let rows = service.lookup(DeliveryLookup::Id(id.clone())).await.map_err(|_| invalid("direct delivery read failed"))?;
        Ok(rows.into_iter().next())
    }

    pub(crate) async fn reconcile_direct(&self, row: &PersistedMessageDelivery) -> ServiceResult<ChatRunRecord> {
        let run = row.run_id.as_deref().ok_or_else(|| invalid("direct run missing"))?;
        let record = self.run_store.try_get(run).await?.ok_or_else(|| invalid("direct chat run missing"))?;
        self.project_record(row, record).await
    }

    async fn project_record(&self, row: &PersistedMessageDelivery, record: ChatRunRecord) -> ServiceResult<ChatRunRecord> {
        if record.delivery_id.as_deref() != Some(&row.delivery_id) || record.session_key != row.session_id || record.bot_uuid != row.target_bot_id {
            return Err(invalid("direct projection scope mismatch"));
        }
        if row.state.status.is_terminal() {
            let flow = self.queue_flow().ok_or_else(|| invalid("direct queue unavailable"))?;
            if let Some(contexts) = &flow.bot_run_context {
                // Use durable scope even after the active entry's deadline or
                // a partial cleanup. Reconciliation must be idempotent.
                contexts.mark_terminal(&record.run_id).await;
                contexts.remove_active_run(&bcs_service_api::BotRunScope {
                    group_id: row.group_id.clone(), session_id: row.session_id.clone(), bot_id: row.target_bot_id.clone(),
                }, &record.run_id).await?;
            }
            self.chat_run_cleanup.unregister(&record.run_id).await;
        }
        // Delivery is already committed. Keep ChatRun recoverable until run
        // context cleanup succeeds, including retry after partial cleanup.
        self.project_state(row, record).await
    }

    async fn project_state(&self, row: &PersistedMessageDelivery, record: ChatRunRecord) -> ServiceResult<ChatRunRecord> {
        if record.state.is_terminal() {
            if !row.state.status.is_terminal() { return Err(invalid("direct projection inconsistent")); }
            return Ok(record);
        }
        let mut next = record.clone();
        next.state = match row.state.status {
            Status::Completed => ChatRunState::Completed,
            Status::Cancelled => ChatRunState::Cancelled,
            Status::Failed | Status::Expired | Status::RejectedCapacity => ChatRunState::Failed,
            Status::Running => ChatRunState::Running,
            Status::Dispatching if row.submitted_at_ms.is_some() && record.state == ChatRunState::Pending => ChatRunState::Submitted,
            _ => record.state,
        };
        if next.state == ChatRunState::Failed {
            next.error_message = Some(match row.state.status {
                Status::RejectedCapacity => "queue_capacity_exceeded",
                Status::Expired if row.expire_at_ms == Some(record.expires_at_ms as i64) => "run_timeout",
                Status::Expired => "queue_expired",
                _ => match row.last_error_code.as_deref() {
                    Some("delivery_provider_headers_unsupported") => "delivery_provider_headers_unsupported",
                    Some("bot_terminal_error") => "downstream_error",
                    _ => "delivery_failed",
                },
            }.into());
        }
        if next.state == record.state && next.error_message == record.error_message { return Ok(record); }
        self.run_store.save_managed(&record, next).await
    }

    pub(crate) async fn managed_status(&self, record: ChatRunRecord, cancelled: Option<bool>) -> ServiceResult<A2aRunStatus> {
        let row = self.managed_row(&record).await?;
        self.status_with_delivery(record, row, cancelled).await
    }

    async fn status_with_delivery(&self, record: ChatRunRecord, row: Option<PersistedMessageDelivery>, cancelled: Option<bool>) -> ServiceResult<A2aRunStatus> {
        let Some(row) = row else {
            // Admission can die between ChatRun creation and the atomic queue write.
            if record.delivery_id.is_some() && !record.state.is_terminal() && now_ms() > record.created_at_ms.saturating_add(30_000) {
                let mut failed = record.clone(); failed.state = ChatRunState::Failed; failed.error_message = Some("admission_incomplete".into());
                return Ok(run_status(&self.run_store.save_managed(&record, failed).await?, cancelled));
            }
            return Ok(run_status(&record, cancelled));
        };
        let record = self.project_record(&row, record).await?;
        let mut status = run_status(&record, cancelled);
        if let Some(value) = &mut status.response {
            // Public revision advances for both response checkpoints and queue control transitions.
            value["version"] = serde_json::json!(record.version.saturating_add(row.state.state_version));
            value["delivery"] = serde_json::json!({"delivery_id":row.delivery_id, "message_id":row.source_message_id, "status":row.state.status, "wait_reason":row.wait_reason, "state_version":row.state.state_version}); }
        Ok(status)
    }

    pub(crate) async fn cancel_managed(&self, record: ChatRunRecord, actor: &str) -> ServiceResult<A2aRunStatus> {
        let mut row = self.managed_row(&record).await?.ok_or_else(|| invalid("direct admission incomplete"))?;
        let was_terminal = row.state.status.is_terminal();
        let flow = self.queue_flow().ok_or_else(|| invalid("direct queue unavailable"))?;
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("direct queue unavailable"))?;
        for attempt in 0..4 {
            if row.state.status.is_terminal() || matches!(row.state.status, Status::Cancelling | Status::CancelUnknown) { break; }
            match service.transition(transition(&row, Event::CancelRequested, Some(actor.into()))).await {
                Ok(_) => break,
                Err(error) if transition_conflict(&error) && attempt < 3 => {
                    row = self.managed_row(&record).await?.ok_or_else(|| invalid("direct delivery missing"))?;
                }
                Err(_) => return Err(invalid("direct cancel persistence failed")),
            }
        }
        let fresh = self.managed_row(&record).await?.ok_or_else(|| invalid("direct delivery missing"))?;
        let cancelled = !was_terminal && fresh.state.status == Status::Cancelled;
        self.status_with_delivery(record, Some(fresh), Some(cancelled)).await
    }
}

pub(crate) fn transition_conflict(error: &bcs_service_api::ManagedDeliveryError) -> bool {
    matches!(error, bcs_service_api::ManagedDeliveryError::Conflict
        | bcs_service_api::ManagedDeliveryError::Repository(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Conflict)
        | bcs_service_api::ManagedDeliveryError::Lifecycle(bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion { .. }))
}

pub(crate) fn transition(row: &PersistedMessageDelivery, event: Event, actor_id: Option<String>) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event, now_ms: now_ms() as i64, request_id: None, actor_id, reply: None, transport_context_json: None, deadline_at_ms: None }
}


impl A2aChat {
    pub(super) async fn recover_managed_runs(&self) -> ServiceResult<()> {
        if self.queue_flow().is_none() { return Ok(()); }
        let records = self.run_store.recovery_page().await?;
        let ids = records.iter().filter_map(|r| r.delivery_id.clone()).collect();
        let flow = self.queue_flow().ok_or_else(|| invalid("direct queue unavailable"))?;
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("direct queue unavailable"))?;
        let mut deliveries = service.lookup(DeliveryLookup::Ids(ids)).await.map_err(|_| invalid("direct recovery delivery lookup failed"))?;
        for record in records {
            let row = record.delivery_id.as_ref().and_then(|id| deliveries.iter().position(|d| &d.delivery_id == id)).map(|i| deliveries.swap_remove(i));
            self.status_with_delivery(record, row, None).await?;
        }
        Ok(())
    }
}
