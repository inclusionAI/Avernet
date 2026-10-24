//! Real one-shot identity handoff implementation (spec §7.5.2).
//!
//! A bounded, in-memory, per-epoch slot map. It is deliberately NOT durable
//! and holds no DB handles: when the owning composition goes away the slots
//! die with it, and identity is never recoverable from anywhere else. All
//! decisions use the caller-provided clock.
//!
//! Slot lifecycle: `Reserved` (bounded, within capacity) → `Published`
//! (identity parked once, expiry = the ingress's own trusted window) →
//! `Claimed` (tombstone, identity gone, still counted against capacity until
//! swept). Sweep runs on every reserve and on publish/claim of a different
//! slot. `close` seals the whole handoff irreversibly.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use bcs_service_api::application::caller_execution::{
    HandoffClaim, HandoffError, HandoffScope, HumanCallerDeliveryHandoffService,
    QueuedAdmissionReceipt,
};
use bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress;

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
    slots: Mutex<Slots>,
}

struct Slots {
    closed: bool,
    map: HashMap<HandoffScope, Slot>,
}

impl HumanCallerDeliveryHandoff {
    /// Capacity must be positive; a zero-capacity handoff is a misassembly.
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "handoff capacity must be positive");
        Self {
            capacity,
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
        // Publishing with an already-expired identity is a hard loss: every
        // slot becomes a tombstone and the identity is dropped, never handed
        // out later.
        let expiry = ingress.expires_at_ms();
        let ingress = std::sync::Arc::new(ingress);
        for (scope, receipt) in targets {
            slot_replace(&mut slots, scope, expiry, ingress.clone(), receipt.clone());
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
        // No global sweep on claim: an expired slot of THIS scope must be
        // observable as a precise Expired hard loss (and then memory-sealed
        // right below), not swallowed into NotReserved by the sweeper.
        // Consume in place: a claimed slot stays as a tombstone so later
        // claims observe AlreadyClaimed and capacity accounting stays honest
        // until the sweep expires it.
        match slots.map.get_mut(scope) {
            None => Err(HandoffError::NotReserved),
            Some(Slot::Reserved { .. }) => Err(HandoffError::NotReserved),
            Some(Slot::Claimed { .. }) => Err(HandoffError::AlreadyClaimed),
            Some(slot) => {
                let expires_at_ms = slot.expires_at_ms();
                if claim.now_ms >= expires_at_ms {
                    // Identity expired while parked: hard loss. The ingress (if
                    // any) is dropped with the take below and never handed out.
                    let _ = std::mem::replace(
                        slot,
                        Slot::Claimed { expires_at_ms },
                    );
                    return Err(HandoffError::Expired);
                }
                match std::mem::replace(slot, Slot::Claimed { expires_at_ms }) {
                    Slot::Published { ingress, .. } if !ingress.is_expired(claim.now_ms) => {
                        Ok(ingress)
                    }
                    Slot::Published { .. } => Err(HandoffError::Expired),
                    other => {
                        // The replace above always wrote Claimed back; the
                        // observed slot was Published (matched by expiry check
                        // semantics); keep the map consistent regardless.
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
    expiry: i64,
    ingress: std::sync::Arc<AuthenticatedHumanCallerIngress>,
    _receipt: QueuedAdmissionReceipt,
) {
    // Snapshot semantics:
    // - expired-at-publish ingress is discarded (tombstone) — never claimable
    // - live ingress is parked with its own trusted window as the slot expiry
    if expiry <= _receipt.admitted_at_ms {
        slots.map.insert(scope.clone(), Slot::Claimed { expires_at_ms: expiry });
    } else {
        slots
            .map
            .insert(scope.clone(), Slot::Published { ingress, expires_at_ms: expiry });
    }
}