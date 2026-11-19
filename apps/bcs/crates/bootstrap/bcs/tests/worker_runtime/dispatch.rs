//! Test-host commands call public ports; no direct Store writes construct facts.
use super::{assembly::Host, workload::*};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*, dispatch::*, inbox::*, ownership::*, task_feed::*,
};
use futures::StreamExt;
use serde_json::{Value, json};
use std::{num::NonZeroU32, time::Duration};

fn field<'a>(v: &'a Value, key: &str) -> anyhow::Result<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("missing string {key}"))
}
fn record(record: DispatchRecord) -> Value {
    json!({"view":record.view,"revision":record.revision.0,"terminal_evidence":record.terminal_evidence})
}
impl Host {
    pub async fn dispatch_command(&mut self, request: &Value) -> anyhow::Result<Value> {
        match field(request, "action")? {
            "get" => Ok(self
                .dispatch
                .get(&Did(field(request, "did")?.into()))
                .await?
                .map(record)
                .unwrap_or(Value::Null)),
            "reply" => {
                let outcome = self
                    .inbox
                    .admit(AdmitRequest {
                        scope: scope(),
                        input: InputEnvelope {
                            schema_version: 1,
                            sid: Sid(field(request, "sid")?.into()),
                            tid: Tid(field(request, "tid")?.into()),
                            input_id: InputId(field(request, "id")?.into()),
                            business_digest: Digest(String::new()),
                            body: InputBody::HumanReply {
                                interaction: InteractionId(
                                    request["interaction"].as_str().unwrap_or("confirm").into(),
                                ),
                                payload: Payload::Inline(request["payload"].clone()),
                            },
                        },
                        create_session: None,
                        create_task: None,
                    })
                    .await?;
                Ok(json!(format!("{outcome:?}")))
            }
            "agent" => {
                let tid = Tid(field(request, "tid")?.into());
                let did = dispatch_id(&tid);
                let current = self
                    .dispatch
                    .get(&did)
                    .await?
                    .ok_or_else(|| anyhow::anyhow!("dispatch missing"))?;
                let event = AgentEvent {
                    did,
                    holder_epoch: current
                        .view
                        .execution
                        .holder
                        .map_or(HolderEpoch(1), |holder| holder.epoch),
                    seq: InputSeq(
                        request["seq"]
                            .as_u64()
                            .ok_or_else(|| anyhow::anyhow!("missing seq"))?,
                    ),
                    kind: AgentEventKind::Delta,
                    remote_event_id: None,
                    payload: Payload::Inline(request["payload"].clone()),
                };
                Ok(serde_json::to_value(
                    self.inbox
                        .append_stream(AppendAgentEvent {
                            sid: current.view.spec.sid,
                            tid,
                            event,
                        })
                        .await?,
                )?)
            }
            "claim_send" => {
                let did = Did(field(request, "did")?.into());
                let me = Instance {
                    node: "story-connector".into(),
                    incarnation: did.0.clone(),
                };
                self.coordination
                    .register(
                        me.clone(),
                        RuntimeRole::Connector,
                        "http://story-connector.example.test".into(),
                        Duration::from_secs(60),
                    )
                    .await?;
                let ClaimOutcome::Acquired(mut held) = self
                    .dispatch
                    .claim(DispatchClaimRequest {
                        did: did.clone(),
                        me,
                        request_id: RequestId(format!("{}.claim", did.0)),
                    })
                    .await?
                else {
                    anyhow::bail!("dispatch was not claimable")
                };
                let operation = SendOperation::Start {
                    request_id: RequestId(format!("{}.send", did.0)),
                };
                held.begin_send(operation.clone()).await?;
                if request["accepted"].as_bool().unwrap_or(false) {
                    held.record_acceptance(AcceptanceEvidence {
                        operation,
                        remote: RemoteTaskRef {
                            task_id: format!("remote-{}", did.0),
                            context_id: None,
                        },
                    })
                    .await?;
                }
                Ok(json!(held.refresh().await?))
            }
            "holder" => {
                let did = Did(field(request, "did")?.into());
                let current = self
                    .dispatch
                    .get(&did)
                    .await?
                    .ok_or_else(|| anyhow::anyhow!("dispatch missing"))?;
                let previous = current
                    .view
                    .execution
                    .holder
                    .ok_or_else(|| anyhow::anyhow!("holder missing"))?;
                // The preceding command dropped its actor before returning.
                // Resume through the public generation fence; never reconstruct
                // a holder by writing the Dispatch document directly.
                let ClaimOutcome::Acquired(mut held) = self
                    .dispatch
                    .resume_owned(ResumeOwnedDispatch {
                        did: did.clone(),
                        previous,
                        request_id: RequestId(field(request, "id")?.into()),
                    })
                    .await?
                else {
                    anyhow::bail!("owned dispatch could not resume")
                };
                match field(request, "operation")? {
                    "confirm_input" => {
                        let seq = InputSeq(request["seq"].as_u64().unwrap_or(1));
                        let operation = SendOperation::Input {
                            seq,
                            request_id: RequestId(format!("{}.input-{}", did.0, seq.0)),
                        };
                        let remote = held
                            .view()
                            .execution
                            .remote
                            .clone()
                            .ok_or_else(|| anyhow::anyhow!("remote missing"))?;
                        held.begin_send(operation.clone()).await?;
                        held.confirm_input(seq, AcceptanceEvidence { operation, remote })
                            .await?;
                    }
                    "cancel_unknown" => {
                        held.record_cancel(CancelObservation::OutcomeUnknown)
                            .await?;
                    }
                    "finish" => {
                        held.finish(
                            FinalDispatchOutcome::Failed,
                            field(request, "evidence")?.into(),
                        )
                        .await?;
                    }
                    other => anyhow::bail!("unknown holder operation {other}"),
                }
                Ok(json!(held.refresh().await?))
            }
            "supervisor" => {
                let mut returned = 0;
                let mut settled = 0;
                let mut notified = 0;
                for _ in 0..request["ticks"].as_u64().unwrap_or(20).min(100) {
                    let report = self.supervisor.tick().await;
                    anyhow::ensure!(report.failures == 0, "supervisor failed: {report:?}");
                    returned += report.returned;
                    settled += report.settled;
                    notified += report.notified;
                    tokio::time::sleep(Duration::from_millis(2)).await;
                }
                Ok(json!({"returned":returned,"settled":settled,"notified":notified}))
            }
            "due" => {
                let target = Did(field(request, "did")?.into());
                let mut after = None;
                let mut crossed_boundary = false;
                for _ in 0..64 {
                    let page = self
                        .dispatch
                        .scan_due(
                            ShardId(1),
                            TimestampMillis(i64::MAX),
                            PageRequest {
                                after,
                                limit: NonZeroU32::new(16).unwrap(),
                            },
                        )
                        .await?;
                    if page.items.contains(&target) {
                        return Ok(json!(true));
                    }
                    if page.next.is_none() {
                        // The supervisor shares the durable scan cursor. This
                        // first boundary may finish only its remaining suffix;
                        // absence requires one complete subsequent round.
                        if crossed_boundary {
                            return Ok(json!(false));
                        }
                        crossed_boundary = true;
                    }
                    after = page.next;
                    tokio::time::sleep(Duration::from_millis(2)).await;
                }
                anyhow::bail!("bounded dispatch scan did not finish")
            }
            "follow" => {
                let cursor: TaskCursor = serde_json::from_value(request["cursor"].clone())?;
                let mut stream = self.feed.follow(scope(), cursor);
                match tokio::time::timeout(Duration::from_millis(250), stream.next()).await {
                    Ok(Some(Ok(FollowItem::Events { items, next }))) => {
                        Ok(json!({"kind":"events","items":items,"next":next}))
                    }
                    Ok(Some(Ok(FollowItem::Gap))) => Ok(json!({"kind":"gap"})),
                    Ok(Some(Err(error))) => Err(error.into()),
                    Ok(None) => Ok(json!({"kind":"ended"})),
                    Err(_) => Ok(json!({"kind":"pending"})),
                }
            }
            "validate_cursor" => {
                let cursor: TaskCursor = serde_json::from_value(request["cursor"].clone())?;
                let mut actor = scope();
                if request["wrong_scope"].as_bool().unwrap_or(false) {
                    actor.principal = PrincipalId("other".into());
                }
                Ok(json!(format!(
                    "{:?}",
                    self.feed.validate_resume(&actor, &cursor).await?
                )))
            }
            "trim" => Ok(json!(format!(
                "{:?}",
                self.feed
                    .trim(RetentionRequest {
                        sid: Sid(field(request, "sid")?.into()),
                        keep_from: serde_json::from_value(request["keep_from"].clone())?
                    })
                    .await?
            ))),
            "list" => {
                let page = self
                    .feed
                    .list(
                        &scope(),
                        TaskFilter {
                            sid: Some(Sid(field(request, "sid")?.into())),
                            statuses: vec![],
                        },
                        PageRequest {
                            after: None,
                            limit: NonZeroU32::new(8).unwrap(),
                        },
                    )
                    .await?;
                Ok(json!({"items":page.items,"next":page.next}))
            }
            other => anyhow::bail!("unknown dispatch action {other}"),
        }
    }
}
