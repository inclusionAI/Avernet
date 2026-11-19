//! JSON-lines driver; assertions live in the external process story.
use super::{assembly::Host, workload::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_runtime::DistWorkerIsolationService,
    port::{
        dist_runtime::*,
        repo::distributed::{control::*, inbox::*, result::*, session::*, task_feed::*},
    },
    types::dist_runtime::*,
};
use serde_json::{Value, json};
use std::{
    sync::{Arc, atomic::Ordering},
    time::Duration,
};
struct Authorizer;
#[async_trait]
impl DistWorkerControlAuthorizerPort for Authorizer {
    async fn authorize(
        &self,
        actor: &TaskScope,
        target: &TaskScope,
        _: WorkerControlAction,
    ) -> Result<bool, RuntimeError> {
        Ok(actor == target)
    }
}
fn field<'a>(v: &'a Value, key: &str) -> anyhow::Result<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("missing string {key}"))
}
impl Host {
    pub async fn command(&mut self, request: Value) -> anyhow::Result<Value> {
        match field(&request, "op")? {
            "gateway_listen" => Ok(json!(self.gateway_listen().await?)),
            "admit" => {
                let sid = Sid(field(&request, "sid")?.into());
                let tid = Tid(field(&request, "tid")?.into());
                let spec = specification(sid.clone());
                let task = metadata(&spec, tid.clone());
                let result = self
                    .inbox
                    .admit(AdmitRequest {
                        scope: scope(),
                        input: InputEnvelope {
                            schema_version: 1,
                            sid,
                            tid,
                            input_id: InputId(field(&request, "id")?.into()),
                            business_digest: Digest(String::new()),
                            body: InputBody::User {
                                payload: Payload::Inline(request["payload"].clone()),
                            },
                        },
                        create_session: Some(spec),
                        create_task: Some(task),
                    })
                    .await?;
                Ok(json!(format!("{result:?}")))
            }
            "cancel" => {
                let result = self
                    .inbox
                    .admit(AdmitRequest {
                        scope: scope(),
                        input: InputEnvelope {
                            schema_version: 1,
                            sid: Sid(field(&request, "sid")?.into()),
                            tid: Tid(field(&request, "tid")?.into()),
                            input_id: InputId(field(&request, "id")?.into()),
                            business_digest: Digest(String::new()),
                            body: InputBody::Cancel { reason: None },
                        },
                        create_session: None,
                        create_task: None,
                    })
                    .await?;
                Ok(json!(format!("{result:?}")))
            }
            "tick" => {
                let errors = self.tick(request["count"].as_u64().unwrap_or(1)).await?;
                Ok(
                    json!({"runner_calls":self.runner.calls.load(Ordering::SeqCst),"processor_errors":errors}),
                )
            }
            "snapshot" => Ok(serde_json::to_value(
                self.feed
                    .load_snapshot(&scope(), &Tid(field(&request, "tid")?.into()))
                    .await?,
            )?),
            "restore" => Ok(
                match self
                    .feed
                    .restore(&scope(), &Tid(field(&request, "tid")?.into()))
                    .await?
                {
                    Some(RestoredTask::Final(view)) => json!({"kind":"final","view":view}),
                    Some(RestoredTask::Live { view, cursor }) => {
                        json!({"kind":"live","view":view,"cursor":cursor})
                    }
                    None => Value::Null,
                },
            ),
            "archive" => Ok(serde_json::to_value(
                self.archive
                    .get_final(&scope(), &Tid(field(&request, "tid")?.into()))
                    .await?,
            )?),
            "checkpoint" => Ok(serde_json::to_value(
                self.coordination
                    .load(&Sid(field(&request, "sid")?.into()))
                    .await?,
            )?),
            "handoff" => self.handoff_cycle().await,
            "add_worker" => {
                self.add_worker(field(&request, "name")?).await;
                Ok(json!(self.workers.len()))
            }
            "stop_worker" => {
                let worker = self
                    .workers
                    .get_mut(request["index"].as_u64().unwrap_or(0) as usize)
                    .ok_or_else(|| anyhow::anyhow!("worker missing"))?;
                worker.stopped = true;
                Ok(json!(true))
            }
            "advance_store_clock" => {
                self.model.advance(Duration::from_millis(
                    request["millis"].as_u64().unwrap_or(0).min(60_000),
                ));
                Ok(json!(true))
            }
            "lose_next_reply" => {
                self.model.lose_next_response();
                Ok(json!(true))
            }
            "dispatch" => self.dispatch_command(&request).await,
            "storage" => super::storage::command(self.model.as_ref(), &request).await,
            "runtime" => {
                let w = self.workers.last().unwrap().runtime.snapshot().await?;
                Ok(
                    json!({"instance":w.instance,"ready":w.ready,"draining":w.draining,"active_work":w.active_work,"pending_effects":w.pending_effects}),
                )
            }
            "drain" => Ok(json!(format!(
                "{:?}",
                self.workers
                    .last()
                    .unwrap()
                    .runtime
                    .drain(Duration::from_millis(20))
                    .await?
            ))),
            "isolation" | "resume" => {
                let facade = bcs_app_dist_runtime::WorkerIsolationApplication::new(
                    self.workers.last().unwrap().processor.clone(),
                    Arc::new(Authorizer),
                );
                let mut actor = scope();
                if request["wrong_actor"].as_bool().unwrap_or(false) {
                    actor.principal = PrincipalId("unauthorized".into());
                }
                if request["op"] == "isolation" {
                    Ok(serde_json::to_value(
                        facade
                            .isolation(actor, scope(), Sid(field(&request, "sid")?.into()))
                            .await?,
                    )?)
                } else {
                    let authorization: RecoveryAuthorization =
                        serde_json::from_value(request["authorization"].clone())?;
                    Ok(json!(format!(
                        "{:?}",
                        facade.resume(actor, scope(), authorization).await?
                    )))
                }
            }
            other => anyhow::bail!("unknown command {other}"),
        }
    }
}
