use super::*;

// ---------------------------------------------------------------------------
// Speaking-lease commands (spec §6.3, P3.3): acquire / preempt / release /
// current over the authenticated Workbench connection. The lease key is the
// group; the holder is ALWAYS the bound authenticated actor — never a value
// from the frame body. Plain groups never reach these commands in practice,
// and an unmounted store answers with an explicit error, never a fake lease.
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct LeaseParams {
    #[serde(alias = "groupId")]
    group_id: String,
    /// Only `lease.release` needs the lease coordinates being given back;
    /// acquire/preempt/current take nothing beyond the authenticated actor.
    #[serde(default, alias = "leaseId")]
    lease_id: Option<String>,
    #[serde(default, alias = "leaseEpoch")]
    lease_epoch: Option<u64>,
}

fn merge_lease_body(body: &mut Value, lease: &bcs_domain::speaking_lease::SessionSpeakingLease) {
    body["leaseId"] = serde_json::json!(lease.lease_id);
    body["epoch"] = serde_json::json!(lease.epoch);
    body["expiresAt_ms"] = serde_json::json!(lease.expires_at_ms);
    body["holderActorId"] = serde_json::json!(lease.holder_actor_id);
}

fn lease_now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or_default()
}

// §15.99: the idle window is composition-supplied config
// ([speaking_lease] idle_timeout_ms, default 60s frozen legacy value),
// read from WebDispatchState instead of a file-local constant.

pub(super) async fn handle_lease_command(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<String>,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let Some(store) = state.speaking_leases.as_ref() else {
        return send_error(
            tx,
            &req.id,
            "lease_unavailable",
            "Speaking lease service is not configured",
        )
        .await;
    };
    // The holder is the authenticated business actor — frame bodies never
    // name a holder.
    let Some(holder) = auth.actor_id() else {
        return send_error(
            tx,
            &req.id,
            "unauthorized",
            "An authenticated Human is required for speaking lease commands",
        )
        .await;
    };
    let holder = holder.to_string();
    let params: LeaseParams = match serde_json::from_value(req.params.clone().unwrap_or(Value::Null)) {
        Ok(params) => params,
        Err(error) => {
            return send_error(tx, &req.id, "invalid_request", &format!("Invalid lease params: {error}")).await;
        }
    };
    let group = params.group_id.clone();
    let session_id = match auth {
        WorkbenchConnectionAuth::SessionBound { group_id, session_id, .. } => {
            if group_id != &group {
                return send_error(tx, &req.id, "token_scope_mismatch",
                    "Request scope does not match the connection token").await;
            }
            Some(session_id.clone())
        }
        WorkbenchConnectionAuth::UserBound { .. } => None,
    };
    // Recheck persisted membership on every command, not just connect:
    // an authenticated principal (including a mock principal) is not a member.
    // The sender is the bound Human, never an owned Bot or a payload identity.
    if let Err(error) = state.workbench_sessions.authorize_chat_send(
        WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some(holder.clone()),
            from_actor_id: holder.clone(),
            group_id: group.clone(),
            session_id,
        },
    ).await {
        return send_error(tx, &req.id, error.code(), &error.message()).await;
    }
    let now = lease_now_ms().max(1);
    use bcs_service_api::port::repo::session_speaking_lease as lease_port;

    match req.method.as_str() {
        "lease.acquire" => match store
            .acquire(lease_port::AcquireLease {
                session_id: &group,
                requester_actor_id: &holder,
                now_ms: now,
                idle_timeout_ms: state.lease_idle_timeout_ms,
            })
            .await
        {
            Ok(lease_port::LeaseAcquire::Granted(lease)) => {
                let mut body = serde_json::json!({"status": "granted"});
                merge_lease_body(&mut body, &lease);
                send_ok(tx, &req.id, body).await
            }
            Ok(lease_port::LeaseAcquire::SelfHeld(lease)) => {
                let mut body = serde_json::json!({"status": "self_held"});
                merge_lease_body(&mut body, &lease);
                send_ok(tx, &req.id, body).await
            }
            Ok(lease_port::LeaseAcquire::Occupied { holder_actor_id, epoch, expires_at_ms }) => {
                // The competing holder's epoch/expiry are business state,
                // not identity material (holder is an existing business
                // actor id, spec §6.3.1).
                send_ok(tx, &req.id, serde_json::json!({
                    "status": "occupied",
                    "holderActorId": holder_actor_id,
                    "epoch": epoch,
                    "expiresAt_ms": expires_at_ms,
                }))
                .await
            }
            Err(error) => send_error(tx, &req.id, "lease_error", &error.to_string()).await,
        },
        "lease.preempt" => match store
            .preempt(lease_port::PreemptLease {
                session_id: &group,
                requester_actor_id: &holder,
                now_ms: now,
                idle_timeout_ms: state.lease_idle_timeout_ms,
            })
            .await
        {
            Ok(lease) => {
                let mut body = serde_json::json!({"status": "granted"});
                merge_lease_body(&mut body, &lease);
                send_ok(tx, &req.id, body).await
            }
            Err(error) => send_error(tx, &req.id, "lease_error", &error.to_string()).await,
        },
        "lease.release" => {
            // The triple must all come from the frame: holder comes from
            // authentication, the coordinates from the client's own lease.
            let (Some(lease_id), Some(epoch)) = (params.lease_id.as_deref(), params.lease_epoch) else {
                return send_error(tx, &req.id, "invalid_request", "lease.release requires leaseId and leaseEpoch").await;
            };
            match store
                .release(lease_port::ReleaseLease {
                    session_id: &group,
                    holder_actor_id: &holder,
                    lease_id,
                    epoch,
                    now_ms: now,
                })
                .await
            {
                Ok(lease_port::LeaseRelease::Released) => {
                    send_ok(tx, &req.id, serde_json::json!({"status": "released"})).await
                }
                Ok(lease_port::LeaseRelease::NotHeld) => {
                    send_error(tx, &req.id, "lease_not_held", "No matching lease to release").await
                }
                Err(error) => send_error(tx, &req.id, "lease_error", &error.to_string()).await,
            }
        }
        "lease.current" => match store.current(&group).await {
            Ok(Some(lease)) if !lease.is_expired(now) => {
                let mut body = serde_json::json!({"status": "held"});
                merge_lease_body(&mut body, &lease);
                send_ok(tx, &req.id, body).await
            }
            Ok(_) => send_ok(tx, &req.id, serde_json::json!({"status": "none"})).await,
            Err(error) => send_error(tx, &req.id, "lease_error", &error.to_string()).await,
        },
        _ => unreachable!("routed only for the four lease methods"),
    }
}
