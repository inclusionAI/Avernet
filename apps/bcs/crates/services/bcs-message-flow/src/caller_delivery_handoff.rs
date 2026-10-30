//! Real one-shot identity handoff implementation (spec §7.5.2).
//!
//! A bounded, in-memory, per-epoch slot map. It is deliberately NOT durable
//! and holds no DB handles: when the owning composition goes away the slots
//! die with it, and identity is never recoverable from anywhere else. All
//! decisions use the caller-provided clock.
//!
//! Slot lifecycle: `Reserved` (bounded, within capacity) → `Published`
//! (identity parked once, reaping bound = the admission receipt's own
//! horizon, NOT the reservation window — purely for memory housekeeping) →
//! `Claimed` (tombstone, identity gone, still counted against capacity until
//! swept). Sweep runs on every reserve. `close` seals the whole handoff
//! irreversibly. Slot age is NEVER a claim refusal: the identity carries no
//! validity verdict from this map; the credential stays enforced downstream
//! and the one-shot claim is the structural bound.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use bcs_service_api::application::caller_handoff::{HandoffClaim, HandoffError, HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt};
use bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress;

/// Default reaping horizon for Published identity slots: twice the default
/// caller JWT TTL (300s → 600s). Housekeeping only — the credential's own
/// expiry stays enforced downstream where the token is actually verified.
pub const DEFAULT_PUBLISHED_REAP_HORIZON_MS: i64 = 600_000;

/// Never derived and never persisted; the slot keeps it only until claimed.
enum Slot {
    Reserved { expires_at_ms: i64 },
    Published { ingress: std::sync::Arc<AuthenticatedHumanCallerIngress>, expires_at_ms: i64 },
    /// Identity was handed out and is gone. The tombstone keeps capacity
    /// accounting honest and blocks double-claim of the same or a stale
    /// attempt until swept.
    Claimed { expires_at_ms: i64 },
}

impl Slot {
    fn expires_at_ms(&self) -> i64 {
        match self {
            Slot::Reserved { expires_at_ms } | Slot::Published { expires_at_ms, .. }
            | Slot::Claimed { expires_at_ms } => *expires_at_ms,
        }
    }
}

/// Bounded per-epoch handoff. `capacity` counts reserved + published +
/// claimed slots; expired slots are swept before answering capacity.
pub struct HumanCallerDeliveryHandoff {
    capacity: usize,
    /// Reaping horizon for Published slots, armed at publish time from the
    /// admission receipt's `admitted_at_ms` (spec §7.5.2 清理行: the reaper
    /// is memory management, never an identity-validity verdict). Deliberately
    /// independent of the admission reserve window — see `slot_replace`.
    published_reap_horizon_ms: i64,
    slots: Mutex<Slots>,
}

struct Slots {
    closed: bool,
    map: HashMap<HandoffScope, Slot>,
}

impl HumanCallerDeliveryHandoff {
    /// Capacity must be positive; a zero-capacity handoff is a misassembly.
    pub fn new(capacity: usize) -> Self {
        Self::with_published_reap_horizon(capacity, DEFAULT_PUBLISHED_REAP_HORIZON_MS)
    }

    /// `with_published_reap_horizon(capacity, horizon_ms)`: same bounded
    /// handoff, with an explicit reaping horizon for Published identity
    /// slots. The horizon must cover the identity credential's own validity
    /// window (e.g. the caller JWT `max_ttl_seconds`) — a claim handed the
    /// identity later still fails closed downstream on the credential, so
    /// the horizon only bounds memory, never grants validity.
    pub fn with_published_reap_horizon(capacity: usize, horizon_ms: i64) -> Self {
        assert!(capacity > 0, "handoff capacity must be positive");
        assert!(horizon_ms > 0, "published reap horizon must be positive");
        Self {
            capacity,
            published_reap_horizon_ms: horizon_ms,
            slots: Mutex::new(Slots { closed: false, map: HashMap::new() }),
        }
    }

    /// Sweep expired entries; the caller never observes them again.
    fn sweep(slots: &mut Slots, now_ms: i64) {
        let before = slots.map.len();
        if now_ms > 0 {
            slots.map.retain(|_, slot| slot.expires_at_ms() > now_ms);
        }
        let _ = before;
    }

    /// Seal the current term and start the next one clean (spec §7.5.2):
    /// the caller master's epoch boundary wipes every parked reservation,
    /// published identity and tombstone — identity admitted under an OLD
    /// term is irrevocably gone and fails closed at claim time
    /// (`NotReserved` — the row converges via the existing caller rescue/
    /// TTL paths), and capacity returns to full for the new term. Distinct
    /// from [`HumanCallerDeliveryHandoffService::close`]: close is the
    /// irreversible end of the whole composition; a sealed handoff keeps
    /// admitting for the new term. Returns how many slots were wiped.
    pub fn seal_term(&self) -> usize {
        let mut slots = self.slots.lock().expect("handoff lock");
        if slots.closed {
            return 0;
        }
        let wiped = slots.map.len();
        slots.map.clear();
        wiped
    }
}

#[async_trait]
impl HumanCallerDeliveryHandoffService for HumanCallerDeliveryHandoff {
    async fn reserve(
        &self,
        scope: &HandoffScope,
        reserve_ttl_ms: i64,
        now_ms: i64,
    ) -> Result<(), HandoffError> {
        let mut slots = self.slots.lock().expect("handoff lock");
        if slots.closed {
            return Err(HandoffError::Closed);
        }
        if now_ms <= 0 || reserve_ttl_ms <= 0 {
            return Err(HandoffError::InvalidInput);
        }
        Self::sweep(&mut slots, now_ms);
        match slots.map.get(scope) {
            // Re-reserve of a live reservation is idempotent for the same
            // scope; identity stays absent until a fresh publish.
            Some(Slot::Reserved { .. }) => return Ok(()),
            Some(_) => return Err(HandoffError::AlreadyPublished),
            None => {}
        }
        if slots.map.len() >= self.capacity {
            return Err(HandoffError::CapacityExhausted);
        }
        slots
            .map
            .insert(scope.clone(), Slot::Reserved { expires_at_ms: now_ms + reserve_ttl_ms });
        Ok(())
    }

    async fn publish(
        &self,
        scope: &HandoffScope,
        ingress: AuthenticatedHumanCallerIngress,
        receipt: QueuedAdmissionReceipt,
    ) -> Result<(), HandoffError> {
        self.publish_for_targets(ingress, &[(scope.clone(), receipt)]).await
    }

    async fn identity_readiness(&self, scope: &HandoffScope) -> HandoffIdentityReadiness {
        // Pure read: no sweep, no mutation, no close check beyond the empty
        // map it leaves (close/seal wipe everything, and a wiped scope is
        // Gone exactly like a never-reserved one).
        let slots = self.slots.lock().expect("handoff lock");
        match slots.map.get(scope) {
            Some(Slot::Published { .. }) => HandoffIdentityReadiness::Published,
            Some(Slot::Reserved { .. }) => HandoffIdentityReadiness::Reserved,
            // Never published with the reservation gone (crash window,
            // sweep, term seal, close), or already consumed by an earlier
            // claim: either way no later attempt of this scope can claim.
            None | Some(Slot::Claimed { .. }) => HandoffIdentityReadiness::Gone,
        }
    }

    async fn publish_for_targets(
        &self,
        ingress: AuthenticatedHumanCallerIngress,
        targets: &[(HandoffScope, QueuedAdmissionReceipt)],
    ) -> Result<(), HandoffError> {
        let mut slots = self.slots.lock().expect("handoff lock");
        if slots.closed {
            return Err(HandoffError::Closed);
        }
        if targets.is_empty() {
            return Err(HandoffError::InvalidInput);
        }
        // One lock, one decision: validate EVERY target's slot first, then
        // land all of them — partial publication never happens.
        for (scope, receipt) in targets {
            if receipt.delivery_id.is_empty() {
                return Err(HandoffError::InvalidInput);
            }
            match slots.map.get(scope) {
                None => return Err(HandoffError::NotReserved),
                Some(Slot::Published { .. }) => return Err(HandoffError::AlreadyPublished),
                Some(Slot::Claimed { .. }) => return Err(HandoffError::AlreadyClaimed),
                Some(Slot::Reserved { .. }) => {}
            }
        }
        // The Published slot's reaping bound lives on the admission receipt's
        // own horizon (housekeeping only); the identity carries no validity
        // verdict from this map and a late claim is not a loss — the bound is
        // memory, the credential is enforced downstream.
        let horizon_ms = self.published_reap_horizon_ms;
        let ingress = std::sync::Arc::new(ingress);
        for (scope, receipt) in targets {
            slot_replace(&mut slots, scope, ingress.clone(), receipt.clone(), horizon_ms);
        }
        Ok(())
    }

    async fn release(&self, scope: &HandoffScope) -> Result<(), HandoffError> {
        let mut slots = self.slots.lock().expect("handoff lock");
        if slots.closed {
            return Err(HandoffError::Closed);
        }
        match slots.map.get(scope) {
            None => return Ok(()),
            Some(Slot::Reserved { .. }) => {
                let _ = slots.map.remove(scope);
                Ok(())
            }
            Some(Slot::Published { .. }) => Err(HandoffError::AlreadyPublished),
            Some(Slot::Claimed { .. }) => Err(HandoffError::AlreadyClaimed),
        }
    }

    async fn claim(
        &self,
        scope: &HandoffScope,
        claim: HandoffClaim,
    ) -> Result<std::sync::Arc<AuthenticatedHumanCallerIngress>, HandoffError> {
        let mut slots = self.slots.lock().expect("handoff lock");
        if slots.closed {
            return Err(HandoffError::Closed);
        }
        if claim.delivery_id.is_empty() || claim.attempt == 0 || claim.now_ms <= 0 {
            return Err(HandoffError::InvalidInput);
        }
        // No global sweep on claim (the sweeper owns reaping; stale entries of
        // THIS scope must surface as NotReserved/AlreadyClaimed precisely).
        // Consume in place: a claimed slot stays as a tombstone so later
        // claims observe AlreadyClaimed and capacity accounting stays honest
        // until the sweep expires it. Slot age is never a claim refusal.
        match slots.map.get_mut(scope) {
            None => Err(HandoffError::NotReserved),
            Some(Slot::Reserved { .. }) => Err(HandoffError::NotReserved),
            Some(Slot::Claimed { .. }) => Err(HandoffError::AlreadyClaimed),
            Some(slot) => {
                let expires_at_ms = slot.expires_at_ms();
                match std::mem::replace(slot, Slot::Claimed { expires_at_ms }) {
                    Slot::Published { ingress, .. } => Ok(ingress),
                    other => {
                        // The replace above always wrote Claimed back; keep
                        // the map consistent regardless.
                        *slot = other;
                        Err(HandoffError::AlreadyClaimed)
                    }
                }
            }
        }
    }

    async fn close(&self) {
        let mut slots = self.slots.lock().expect("handoff lock");
        slots.closed = true;
        // Drop parked identity immediately; tombstones are pointless once no
        // result can ever be handed out again.
        slots.map.clear();
    }
}

fn slot_replace(
    slots: &mut Slots,
    scope: &HandoffScope,
    ingress: std::sync::Arc<AuthenticatedHumanCallerIngress>,
    receipt: QueuedAdmissionReceipt,
    horizon_ms: i64,
) {
    // Snapshot semantics for the identity, NOT for the expiry: parking arms
    // the Published slot's reaping bound on the admission receipt's own
    // horizon, never on the reservation window. This decouples reaping from
    // the admission reserve TTL (spec §7.5.2: 排队不延长身份有效期 — but the
    // reserve window bounds only the PENDING reservation; a message still
    // queued past it keeps a claimable identity until its credential or its
    // own row budget says otherwise). Pure memory reaping either way: the
    // claim path never refuses on age and the credential is verified where
    // the token actually travels.
    match slots.map.remove(scope) {
        Some(Slot::Reserved { .. }) => {
            slots.map.insert(
                scope.clone(),
                Slot::Published { ingress, expires_at_ms: receipt.admitted_at_ms + horizon_ms },
            );
        }
        _ => {
            // Callers validated Reserved above; keep the map consistent.
            slots.map.insert(
                scope.clone(),
                Slot::Claimed { expires_at_ms: receipt.admitted_at_ms },
            );
        }
    }
}