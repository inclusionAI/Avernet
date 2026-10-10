//! InteractionRegistry: tracks pending HITL interactions per run, mints
//! interaction_ids, and routes BCS `interaction.resolve` decisions to the
//! parked driver task via a oneshot.
//!
//! Per spec §5/§6.3: the cc driver registers a pending interaction when the
//! engine emits a `can_use_tool`/`AskUserQuestion` control request; BCS
//! resolves it over the webhook; on abort/deadline the run loop invalidates
//! all of a run's pending interactions with a safe fallback (deny) so the
//! driver never blocks on a dead receiver.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use bcs_protocol::stream::InteractionKind;
use serde_json::Value;
use tokio::sync::oneshot;

/// Mint a fresh interaction_id: `int-<32hex>` (UUIDv4 simple form).
fn mint_interaction_id() -> String {
    format!("int-{}", uuid::Uuid::new_v4().simple())
}

/// One pending interaction parked in the registry, awaiting a BCS resolve.
pub struct PendingInteraction {
    pub run_id: String,
    pub kind: InteractionKind,
    /// Engine-native request id (cc's `request_id`); never surfaced to BCS.
    pub engine_request_id: String,
    /// Idempotency key of the resolve that delivered (None until delivered).
    pub idempotency_key: Option<String>,
    /// Sender the driver awaits; `None` once delivered or invalidated.
    resolver: Option<oneshot::Sender<Value>>,
}

/// Outcome of [`InteractionRegistry::resolve`].
#[derive(Debug, PartialEq, Eq)]
pub enum ResolveOutcome {
    /// Resolution delivered to the parked driver (first resolve).
    Delivered,
    /// Same interaction already resolved — replay ack, no re-delivery.
    Duplicate,
    /// `interactionId` is not registered.
    Unknown,
}

#[derive(Default)]
struct RegistryInner {
    map: HashMap<String, PendingInteraction>,
}

/// Cloneable (`Arc<Mutex<…>>`) registry of pending HITL interactions, keyed by
/// interaction_id. Cheap to clone so it can live on [`crate::webhook::AppState`]
/// and be passed into each [`crate::engine::TurnRequest`].
#[derive(Clone, Default)]
pub struct InteractionRegistry {
    inner: Arc<Mutex<RegistryInner>>,
}

impl InteractionRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a pending interaction for `run_id`; returns the minted
    /// `interaction_id` and the receiver the driver awaits for the BCS
    /// resolution.
    pub fn register(
        &self,
        run_id: &str,
        kind: InteractionKind,
        engine_request_id: String,
    ) -> (String, oneshot::Receiver<Value>) {
        let interaction_id = mint_interaction_id();
        let (tx, rx) = oneshot::channel::<Value>();
        let entry = PendingInteraction {
            run_id: run_id.to_string(),
            kind,
            engine_request_id,
            idempotency_key: None,
            resolver: Some(tx),
        };
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        inner.map.insert(interaction_id.clone(), entry);
        (interaction_id, rx)
    }

    /// Deliver `resolution` to the parked driver for `interaction_id`. The
    /// first delivery returns [`ResolveOutcome::Delivered`]; any subsequent
    /// resolve of an already-delivered interaction returns
    /// [`ResolveOutcome::Duplicate`] with no re-delivery (idempotent replay,
    /// spec §5.1); an unknown id returns [`ResolveOutcome::Unknown`].
    ///
    /// Mutex poison is recovered (consistent with the rest of the crate) so a
    /// panicking holder never wedges the registry.
    pub fn resolve(
        &self,
        interaction_id: &str,
        idempotency_key: &str,
        resolution: Value,
    ) -> ResolveOutcome {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        let Some(entry) = inner.map.get_mut(interaction_id) else {
            return ResolveOutcome::Unknown;
        };
        let Some(resolver) = entry.resolver.take() else {
            // Already delivered (by a prior resolve or invalidate_run): replay.
            return ResolveOutcome::Duplicate;
        };
        // Send the resolution to the parked driver. The receiver is only
        // dropped if the driver task already exited (e.g. engine crash) — in
        // that case the value is simply dropped and the interaction is closed.
        let _ = resolver.send(resolution);
        entry.idempotency_key = Some(idempotency_key.to_string());
        ResolveOutcome::Delivered
    }

    /// Release every pending interaction of `run_id` with `fallback` (deny on
    /// abort/deadline per spec §6.3) so the driver's `resolution_rx` never
    /// blocks on a dead receiver. Entries are retained (resolver cleared) so
    /// a late BCS resolve resolves to [`ResolveOutcome::Duplicate`] rather
    /// than surfacing as `Unknown`.
    pub fn invalidate_run(&self, run_id: &str, fallback: Value) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        for entry in inner.map.values_mut() {
            if entry.run_id == run_id {
                if let Some(resolver) = entry.resolver.take() {
                    let _ = resolver.send(fallback.clone());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_protocol::stream::InteractionKind;
    use serde_json::json;

    #[tokio::test]
    async fn resolve_delivers_and_duplicate_key_replays() {
        let reg = InteractionRegistry::new();
        let (iid, rx) = reg.register("run-1", InteractionKind::Exec, "engine-req-1".into());
        assert!(matches!(
            reg.resolve(&iid, "key-1", json!({"decision":"allow_once"})),
            ResolveOutcome::Delivered
        ));
        assert_eq!(rx.await.unwrap()["decision"], json!("allow_once"));
        // 同 key 重复 → Duplicate（不再投递）
        assert!(matches!(
            reg.resolve(&iid, "key-1", json!({"decision":"allow_once"})),
            ResolveOutcome::Duplicate
        ));
        // 未知 id → Unknown
        assert!(matches!(
            reg.resolve("int-nope", "key-2", json!({"decision":"deny"})),
            ResolveOutcome::Unknown
        ));
    }

    #[tokio::test]
    async fn invalidate_run_releases_parked_with_fallback_and_late_resolve_is_duplicate() {
        let reg = InteractionRegistry::new();
        let (iid, rx) = reg.register("run-x", InteractionKind::Exec, "e-1".into());
        reg.invalidate_run("run-x", json!({"decision": "deny"}));
        // Driver receives the fallback, not a recv error.
        assert_eq!(rx.await.unwrap()["decision"], json!("deny"));
        // Late BCS resolve → Duplicate (not Unknown) — the run was abandoned,
        // not the interaction id.
        assert!(matches!(
            reg.resolve(&iid, "k", json!({"decision":"allow_once"})),
            ResolveOutcome::Duplicate
        ));
    }

    #[test]
    fn invalidate_run_is_scoped_to_run_id() {
        let reg = InteractionRegistry::new();
        let (_i_a, _r_a) = reg.register("run-a", InteractionKind::Exec, "e-a".into());
        let (_i_b, mut r_b) = reg.register("run-b", InteractionKind::Exec, "e-b".into());
        reg.invalidate_run("run-a", json!({"decision":"deny"}));
        // run-b is untouched: its resolver is still held, so a first resolve
        // delivers normally.
        assert!(matches!(
            reg.resolve(&_i_b, "k", json!({"decision":"allow_once"})),
            ResolveOutcome::Delivered
        ));
        assert_eq!(r_b.try_recv().unwrap()["decision"], json!("allow_once"));
    }

    #[test]
    fn minted_ids_are_prefixed_int_and_unique() {
        let a = mint_interaction_id();
        let b = mint_interaction_id();
        assert!(a.starts_with("int-"), "a = {a}");
        assert!(b.starts_with("int-"), "b = {b}");
        assert_ne!(a, b, "ids must be unique");
        // simple() form is 32 lowercase hex chars, no hyphens.
        assert_eq!(a.len(), "int-".len() + 32);
        assert!(!a["int-".len()..].contains('-'));
    }
}
