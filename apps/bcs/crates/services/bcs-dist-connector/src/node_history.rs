use crate::call::{Call, Flow, Stop, held_write};
use crate::report::Admitted;
use bcs_dist_encoding::{blob_reference, digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::StoreError,
    group_round::RoundBudgetProfile,
    node_history::{HistoryManifest, NodeHistoryRepo, PinnedHistory},
    result::BlobStorePort,
};

/// Replay an existing pin (no live history read). Validate the entire handoff
/// against the committed dispatch before permitting data to leave the process.
pub(super) async fn pinned_history(
    spec: &DispatchSpec,
    repo: &dyn NodeHistoryRepo,
    blobs: &dyn BlobStorePort,
) -> Result<PinnedHistory, StoreError> {
    let pin = spec.node_history.as_ref().ok_or_else(|| invalid("node_history_missing"))?;
    let reference = &pin.cut_manifest;
    let bytes = blobs.get(reference).await?.ok_or_else(|| invalid("node_history_manifest_missing"))?;
    if blob_reference(&reference.key, &bytes) != *reference {
        return Err(invalid("node_history_manifest_digest"));
    }
    let manifest: HistoryManifest = serde_json::from_slice(&bytes)
        .map_err(|_| invalid("node_history_manifest_encoding"))?;
    let cut = manifest.dispatch_cut(spec, pin)?;
    let mut history = match repo.load_node_input(&pin.round, &spec.node, cut, &RoundBudgetProfile::v1()).await {
        Err(StoreError::NotApplied { code }) if code == bcs_service_api::port::repo::distributed::node_history::CODE_NODE_INPUT_PIN_RELEASED => return Err(invalid(&code)),
        other => other?,
    };
    // All selected records must reach the adapter; never silently reselect
    // a smaller history than the durable pin. Digest-check referenced bodies
    // before resolving them locally, and refuse an over-budget dispatch.
    let mut remaining = RoundBudgetProfile::v1().inbound_bytes;
    for record in &mut history.entries {
        let content = match &record.entry.content {
            Payload::Inline(value) => value.clone(),
            Payload::Ref(reference) => {
                let bytes = blobs.get(reference).await?.ok_or_else(|| invalid("node_history_entry_missing"))?;
                if blob_reference(&reference.key, &bytes) != *reference {
                    return Err(invalid("node_history_entry_digest"));
                }
                if bytes.len() > remaining {
                    return Err(invalid("node_history_input_budget"));
                }
                serde_json::from_slice(&bytes).map_err(|_| invalid("node_history_entry_encoding"))?
            }
        };
        record.entry.content = Payload::Inline(content);
        let encoded = serde_json::to_vec(&record).map_err(|_| invalid("node_history_entry_encoding"))?;
        remaining = remaining.checked_sub(encoded.len().saturating_add(1))
            .ok_or_else(|| invalid("node_history_input_budget"))?;
    }
    Ok(history)
}

impl Call {
    pub(super) async fn remember_node_response(&mut self, kind: &AgentEventKind, payload: Payload) -> Flow<Payload> {
        if self.spec().node_history.is_none() {
            return Ok(payload);
        }
        let slot = match kind {
            AgentEventKind::Final => b"final".as_slice(),
            AgentEventKind::Error => b"error",
            AgentEventKind::InputRequired { .. } => b"input-required",
            AgentEventKind::AuthRequired { .. } => b"auth-required",
            _ => return Ok(payload),
        };
        let id = InteractionId(format!("node-reply:{}", hex_of(&digest_fields(&[
            b"fsm-node-response:v1", self.did().0.as_bytes(), slot,
        ]))));
        if let Some(content) = self.held.view().execution.node_responses.iter()
            .find(|response| response.id == id).map(|response| response.content.clone())
        {
            return self.load_history_response(&content).await;
        }
        let bytes = match &payload {
            Payload::Inline(value) => serde_json::to_vec(value).map_err(|_| Stop::Invalid)?,
            Payload::Ref(reference) => {
                let value = self.load(&Payload::Ref(reference.clone())).await?.ok_or(Stop::Invalid)?;
                let Payload::Inline(value) = value else { return Err(Stop::Invalid) };
                serde_json::to_vec(&value).map_err(|_| Stop::Invalid)?
            }
        };
        let digest = blob_reference("", &bytes).digest;
        let reference = BlobRef {
            key: format!("fsm-node-response:v1:{}", hex_of(&digest)), digest,
        };
        let mut backoff = self.backoff();
        loop {
            use bcs_service_api::port::repo::distributed::common::IdempotentWrite;
            match self.ports.blobs.put_immutable(reference.clone(), bytes.clone()).await {
                Ok(IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied) => break,
                Ok(IdempotentWrite::ContentConflict) => return Err(Stop::Invalid),
                Err(error) if crate::call::permanent(&error) => return Err(Stop::Invalid),
                Err(_) => {
                    backoff.wait().await;
                    held_write!(self, "dispatch read", self.held.refresh());
                }
            }
        }
        let response = NodeResponseEvidence { id, kind: kind.clone(), content: reference };
        held_write!(self, "node response", self.held.record_node_response(response.clone()));
        let saved = self.held.view().execution.node_responses.iter().find(|entry| entry.id == response.id)
            .ok_or(Stop::Invalid)?.content.clone();
        if saved == response.content { Ok(payload) } else { self.load_history_response(&saved).await }
    }

    async fn load_history_response(&mut self, reference: &BlobRef) -> Flow<Payload> {
        let mut backoff = self.backoff();
        loop {
            match self.ports.blobs.get(reference).await {
                Ok(Some(bytes)) if blob_reference(&reference.key, &bytes) == *reference => {
                    return serde_json::from_slice(&bytes).map(Payload::Inline).map_err(|_| Stop::Invalid);
                }
                Ok(_) => return Err(Stop::Invalid),
                Err(error) if crate::call::permanent(&error) => return Err(Stop::Invalid),
                Err(_) => {
                    backoff.wait().await;
                    held_write!(self, "dispatch read", self.held.refresh());
                }
            }
        }
    }

    pub(super) async fn replay_recorded_node_final(&mut self) -> Flow {
        let reference = self.held.view().execution.node_responses.iter()
            .find(|entry| matches!(entry.kind, AgentEventKind::Final))
            .ok_or(Stop::Invalid)?.content.clone();
        let verified = self.load_history_response(&reference).await?;
        if self.report(AgentEventKind::Final, None, verified).await? == Admitted::Yes {
            self.finish(FinalDispatchOutcome::Completed, "sync_result").await?;
        }
        Ok(())
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
