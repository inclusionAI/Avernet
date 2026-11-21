use super::{StoreInboxProducer, storage::*};
use crate::{codec::encode_json, identity::digest_json, layout::KeyLayout};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};

impl StoreInboxProducer {
    pub(super) async fn prepare(&self, r: &mut AdmitRequest) -> Result<(), StoreError> {
        encoded_size(r, self.inner.config.max_input_bytes)
            .map_err(|_| invalid("input_byte_budget"))?;
        let i = &r.input;
        if i.schema_version != 1 {
            return Err(StoreError::UnsupportedFormat {
                format: i.schema_version,
            });
        }
        self.inner.layout.inbox(&i.sid)?;
        self.inner.layout.task(&i.tid)?;
        self.inner
            .layout
            .canonical_admission(&r.scope, &i.input_id)?;
        if !i.tid.0.starts_with(&format!("{}.", i.sid.0)) {
            return Err(invalid("tid_not_in_session"));
        }
        let start = matches!(i.body, InputBody::User { .. });
        if start != r.create_task.is_some() {
            return Err(invalid("start_requires_initial_task"));
        }
        if !start && r.create_session.is_some() {
            return Err(invalid("control_cannot_create_session"));
        }
        if let InputBody::Agent { event } = &i.body {
            if !critical(&event.kind) {
                return Err(invalid("intermediate_requires_append_stream"));
            }
            if !event.did.0.starts_with(&format!("{}.", i.tid.0)) {
                return Err(invalid("dispatch_task_mismatch"));
            }
            let remote = event
                .remote_event_id
                .as_ref()
                .filter(|id| !id.is_empty() && id.len() <= 256)
                .ok_or_else(|| invalid("critical_event_identity"))?;
            if i.input_id != crate::identity::critical_agent_input_id(&event.did, remote) {
                return Err(invalid("critical_input_id_mismatch"));
            }
        }
        if let Some(s) = &r.create_session
            && (s.sid != i.sid || s.shard != KeyLayout::shard_of(&i.sid)? || s.scope != r.scope)
        {
            return Err(invalid("spec_scope_or_identity"));
        }

        if let Some(t) = &r.create_task {
            self.inner
                .projection
                .validate_task_limits(t, &t.output_limits)
                .map_err(|_| invalid("initial_task_output_limits"))?;
            encode_json(t)?;
            if t.scope != r.scope {
                return Err(invalid("task_scope_mismatch"));
            }
            if t.sid != i.sid || t.tid != i.tid {
                return Err(invalid("task_identity_mismatch"));
            }
        }
        if let Some(spec) = &r.create_session {
            encode_json(spec)?;
        }
        let stored = self
            .load::<SessionSpec>(&self.inner.layout.spec(&i.sid)?)
            .await?;
        let spec = stored
            .as_ref()
            .map(|(_, s)| s)
            .or(r.create_session.as_ref())
            .ok_or_else(|| StoreError::NotApplied {
                code: "session_not_found".into(),
            })?;
        if spec.scope != r.scope {
            return Err(invalid("frozen_spec_conflict"));
        }
        if let (Some((_, stored)), Some(proposed)) = (&stored, &r.create_session)
            && stored.group != proposed.group
        {
            return Err(invalid("frozen_spec_conflict"));
        }
        if let Some((_, stored_task)) = self
            .load::<TaskSnapshot>(&self.inner.layout.task(&i.tid)?)
            .await?
        {
            if stored_task.scope != r.scope || stored_task.sid != i.sid {
                return Err(invalid("frozen_task_conflict"));
            }
        } else if !start {
            return Err(invalid("control_task_missing"));
        }
        if let Some(t) = &r.create_task {
            self.inner
                .projection
                .validate_session_limits(spec, &t.output_limits)
                .map_err(|_| invalid("session_capacity_profile"))?;
            if t.sid != i.sid || t.tid != i.tid || t.scope != r.scope {
                return Err(invalid("task_identity_mismatch"));
            }
            if t.content.status != TaskStatus::Submitted
                || t.source_commit != CommitSeq(0)
                || !t.content.interactions.is_empty()
                || t.content.result_ref.is_some()
                || t.output_usage != OutputUsage::default()
                || t.snapshot_event_id.is_some()
            {
                return Err(invalid("initial_task_state"));
            }
            let empty = serde_json::to_value(DisplaySnapshot {
                format_version: 1,
                units: vec![],
            })
            .map_err(|_| invalid("empty_snapshot_encoding"))?;
            if t.content.snapshot != Payload::Inline(empty) {
                return Err(invalid("initial_snapshot_not_empty"));
            }
        }
        r.input.business_digest = request_digest(r)?;
        let mut fields = crate::codec::encode_inbox_entry(&r.input)?;
        fields.insert(
            "canonical".into(),
            self.inner
                .layout
                .canonical_admission(&r.scope, &r.input.input_id)?
                .into_bytes(),
        );
        if fields.iter().map(|(k, v)| k.len() + v.len()).sum::<usize>() + 512
            > self.inner.config.max_input_bytes
        {
            return Err(invalid("input_byte_budget"));
        }
        encoded_size(r, self.inner.config.max_input_bytes)
            .map_err(|_| invalid("input_byte_budget"))?;
        Ok(())
    }
}
pub(super) fn critical(kind: &AgentEventKind) -> bool {
    matches!(
        kind,
        AgentEventKind::InputRequired { .. }
            | AgentEventKind::AuthRequired { .. }
            | AgentEventKind::Final
            | AgentEventKind::Error
    )
}

pub(super) fn request_digest(r: &AdmitRequest) -> Result<Digest, StoreError> {
    let mut identity = encode_json(r)?;
    identity["input"]
        .as_object_mut()
        .ok_or_else(|| invalid("request_encoding"))?
        .remove("business_digest");
    if let InputBody::Agent { .. } = r.input.body {
        let event = identity["input"]["body"]["event"]
            .as_object_mut()
            .ok_or_else(|| invalid("agent_encoding"))?;
        event.remove("holder_epoch");
        event.remove("seq");
    }
    Ok(digest_json(&identity))
}
