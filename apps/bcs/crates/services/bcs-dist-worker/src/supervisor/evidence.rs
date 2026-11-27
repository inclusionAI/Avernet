use super::*;
use bcs_dist_encoding::{critical_agent_input_id, digest_fields, hex_of};
use serde::{Deserialize, Serialize};
/// Supervisor-authored terminal evidence, NOT the downstream Result body.
/// A Runner may interpret the outcome but must not invent missing output data.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TerminalDispatchEvidence {
    pub schema_version: u32,
    pub source: String,
    pub did: Did,
    pub outcome: FinalDispatchOutcome,
    pub evidence: String,
}
impl Facts {
    pub(super) fn from_record(
        did: &Did,
        shard: ShardId,
        record: DispatchRecord,
    ) -> Result<Self, StoreError> {
        let spec = &record.view.spec;
        if spec.schema_version != 1
            || spec.did != *did
            || !identifier(&spec.sid.0)
            || !identifier(&spec.tid.0)
            || !identifier(&spec.pool.0)
            || !spec.tid.0.starts_with(&format!("{}.", spec.sid.0))
            || !did.0.starts_with(&format!("{}.", spec.tid.0))
            || record.revision.0.is_empty()
            || record.revision.0.len() > 256
            || shard_of(&spec.sid) != Some(shard)
        {
            return Err(invalid("supervisor_dispatch_identity"));
        }
        let terminal = if record.view.execution.phase == DispatchPhase::Ended {
            let outcome = match record.view.execution.outcome {
                Some(DispatchOutcome::Completed) => FinalDispatchOutcome::Completed,
                Some(DispatchOutcome::Failed) => FinalDispatchOutcome::Failed,
                Some(DispatchOutcome::Canceled) => FinalDispatchOutcome::Canceled,
                _ => return Err(invalid("supervisor_terminal_outcome")),
            };
            let evidence = record
                .terminal_evidence
                .filter(|e| !e.is_empty())
                .ok_or_else(|| invalid("supervisor_terminal_evidence"))?;
            if serde_json::to_vec(&evidence)
                .map_err(|_| invalid("supervisor_evidence_encoding"))?
                .len()
                > 4096
            {
                return Err(invalid("supervisor_evidence_size"));
            }
            Some(TerminalDispatchEvidence {
                schema_version: 1,
                source: "dispatch_supervisor".into(),
                did: did.clone(),
                outcome,
                evidence,
            })
        } else {
            None
        };
        let holder = record.view.execution.holder.map(|h| h.instance);
        if holder
            .as_ref()
            .is_some_and(|h| !identifier(&h.node) || !identifier(&h.incarnation))
        {
            return Err(invalid("supervisor_holder_identity"));
        }
        let verification = record.verification;
        Ok(Self {
            sid: spec.sid.clone(),
            tid: spec.tid.clone(),
            pool: spec.pool.clone(),
            revision: record.revision,
            holder,
            deadline: spec.deadline,
            terminal,
            verification,
        })
    }
    pub(super) fn request(
        &self,
        shard: ShardId,
        spec: SessionSpec,
    ) -> Result<AdmitRequest, StoreError> {
        if spec.schema_version != 1
            || spec.sid != self.sid
            || spec.shard != shard
            || shard_of(&spec.sid) != Some(shard)
            || !identifier(&spec.scope.tenant.0)
            || !identifier(&spec.scope.principal.0)
        {
            return Err(invalid("supervisor_session_scope"));
        }
        let terminal = self
            .terminal
            .as_ref()
            .ok_or_else(|| invalid("supervisor_nonterminal_return"))?;
        let payload =
            serde_json::to_value(terminal).map_err(|_| invalid("supervisor_evidence_encoding"))?;
        let outcome = match terminal.outcome {
            FinalDispatchOutcome::Completed => b"completed".as_slice(),
            FinalDispatchOutcome::Failed => b"failed".as_slice(),
            FinalDispatchOutcome::Canceled => b"canceled".as_slice(),
        };
        let digest = digest_fields(&[
            b"dispatch-terminal:v1",
            terminal.did.0.as_bytes(),
            outcome,
            terminal.evidence.as_bytes(),
        ]);
        let remote_event_id = format!("dispatch-terminal-v1:{}", hex_of(&digest));
        let input = InputEnvelope {
            schema_version: 1,
            sid: self.sid.clone(),
            tid: self.tid.clone(),
            input_id: critical_agent_input_id(&terminal.did, &remote_event_id),
            business_digest: Digest(String::new()),
            body: InputBody::Agent {
                event: AgentEvent {
                    did: terminal.did.clone(),
                    holder_epoch: HolderEpoch(0),
                    seq: InputSeq(0),
                    kind: if terminal.outcome == FinalDispatchOutcome::Failed {
                        AgentEventKind::Error
                    } else {
                        AgentEventKind::Final
                    },
                    remote_event_id: Some(remote_event_id),
                    payload: Payload::Inline(payload),
                },
            },
        };
        let request = AdmitRequest {
            scope: spec.scope,
            input,
            create_session: None,
            create_task: None,
        };
        if serde_json::to_vec(&request)
            .map_err(|_| invalid("supervisor_request_encoding"))?
            .len()
            > 16 * 1024
        {
            return Err(invalid("supervisor_request_size"));
        }
        Ok(request)
    }
}
fn shard_of(sid: &Sid) -> Option<ShardId> {
    sid.0
        .split_once('.')?
        .0
        .strip_prefix('s')?
        .parse()
        .ok()
        .map(ShardId)
}
