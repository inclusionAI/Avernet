//! The root is a bounded reservation ledger, not an ever-growing request array.
//! Reservation stores the full request atomically with quota consumption and its
//! sequence; materialization moves that request to a retained immutable key.
use super::{StoreInboxProducer, state::*, storage::*};
use crate::layout::KeyLayout;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};

impl StoreInboxProducer {
    pub(super) async fn reserve_intent(
        &self,
        request: AdmitRequest,
        generation: u64,
    ) -> Result<Intent, StoreError> {
        let shard = KeyLayout::shard_of(&request.input.sid)?;
        let key = self.inner.layout.admission_root(shard);
        let op = &request.input.input_id.0;
        let encoded = serde_json::to_vec(&request)
            .map_err(|_| invalid("intent_encoding"))?
            .len() as u64;
        // Full request copies, create-once documents and bounded metadata all
        // count. Inbox physical append attempts reserve additional budget.
        let charge = encoded
            .checked_mul(8)
            .and_then(|n| n.checked_add(4096))
            .ok_or_else(pressure)?;
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(op).await?;
            let root = budget
                .io(op, self.load::<JournalRoot>(&key))
                .await
                .map_err(|_| unknown(op))?;
            let (version, mut root) = match root {
                Some((v, r)) => (Some(v), r),
                None => (None, JournalRoot::default()),
            };
            // Same pending identity assists its original reservation after a lost
            // materialization response; conflicting content gets its own intent.
            if let Some(i) = root.pending.values().find(|i| {
                (i.generation == generation || !i.start())
                    && i.request.scope == request.scope
                    && i.request.input.input_id == request.input.input_id
                    && i.request.input.business_digest == request.input.business_digest
            }) {
                return Ok(i.clone());
            }
            let start = request.create_task.is_some();
            let prior_identity = root.pending.values().any(|i| {
                i.request.scope == request.scope
                    && i.request.input.input_id == request.input.input_id
            });
            let refusal = |error| if prior_identity { unknown(op) } else { error };
            if start && let Err(error) = self.check_health(shard, &root, version.is_none()) {
                return Err(refusal(error));
            }
            self.charge(
                &mut root,
                charge,
                start,
                request.create_session.is_some(),
                false,
            )
            .map_err(refusal)?;
            // Reserve the first physical Inbox append before exposing the intent.
            let physical_bytes = self.reliable_bytes(&request)?;
            self.charge_inbox(&mut root, &request.input.sid, physical_bytes, start, false)
                .map_err(refusal)?;
            self.charge(&mut root, 0, start, false, false)
                .map_err(refusal)?;
            if root.pending.len() >= 16
                || (start && root.pending.values().filter(|i| i.start()).count() >= 8)
            {
                return Err(refusal(pressure()));
            }
            let sequence = root.next.checked_add(1).ok_or_else(pressure)?;
            let intent = Intent {
                schema_version: 1,
                sequence,
                generation,
                request: request.clone(),
                created_at: self.inner.clock.now(),
            };
            root.next = sequence;
            root.pending.insert(sequence, intent.clone());
            let bytes = serde_json::to_vec(&root)
                .map_err(|_| invalid("root_encoding"))?
                .len();
            let limit = self.inner.config.max_document_bytes - if start { 64 * 1024 } else { 0 };
            if bytes > limit {
                return Err(refusal(pressure()));
            }
            if budget.io(op, self.save(&key, version, &root, op)).await? {
                self.root_confirmed(shard)?;
                return Ok(intent);
            }
        }
    }
    fn charge(
        &self,
        r: &mut JournalRoot,
        bytes: u64,
        normal: bool,
        new_session: bool,
        repair: bool,
    ) -> Result<(), StoreError> {
        let c = &self.inner.config;
        let entries = r.entries.checked_add(1).ok_or_else(pressure)?;
        let total = r.bytes.checked_add(bytes).ok_or_else(pressure)?;
        if entries > c.max_history_entries || total > c.max_history_bytes {
            return Err(pressure());
        }
        if normal
            && (r.normal_entries
                >= c.max_history_entries - c.reserved_entries - if repair { 0 } else { 64 }
                || r.normal_bytes.saturating_add(bytes)
                    > c.max_history_bytes - c.reserved_bytes - if repair { 0 } else { 128 * 1024 })
        {
            return Err(pressure());
        }
        if new_session && r.sessions >= c.max_sessions {
            return Err(pressure());
        }
        r.entries = entries;
        r.bytes = total;
        if normal {
            r.normal_entries += 1;
            r.normal_bytes += bytes;
        }
        if new_session {
            r.sessions += 1;
        }
        Ok(())
    }
    pub(super) async fn reserve_append(
        &self,
        sid: &Sid,
        bytes: u64,
        normal: bool,
        repair: bool,
        op: &str,
    ) -> Result<(), StoreError> {
        let key = self.inner.layout.admission_root(KeyLayout::shard_of(sid)?);
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(op).await?;
            let (version, mut root) = budget
                .io(op, self.load::<JournalRoot>(&key))
                .await?
                .ok_or_else(|| invalid("discovery_root_missing"))?;
            self.charge(&mut root, bytes, normal, false, repair)?;
            self.charge_inbox(&mut root, sid, bytes, normal, repair)?;
            if budget
                .io(op, self.save(&key, Some(version), &root, op))
                .await?
            {
                return Ok(());
            }
        }
    }
    pub(super) async fn materialize(&self, intent: &Intent) -> Result<(), StoreError> {
        self.validate_intent(intent)?;
        let shard = KeyLayout::shard_of(intent.sid())?;
        let op = &intent.request.input.input_id.0;
        let key = self.inner.layout.admission_intent(shard, intent.sequence);
        if !self.save(&key, None, intent, op).await? {
            let (_, stored) = self
                .load::<Intent>(&key)
                .await?
                .ok_or_else(|| unknown(op))?;
            if stored.request != intent.request || stored.generation != intent.generation {
                return Err(invalid("intent_content_conflict"));
            }
        }
        Ok(())
    }
    pub(super) async fn release_precanonical(&self, intent: &Intent) -> Result<(), StoreError> {
        let shard = KeyLayout::shard_of(intent.sid())?;
        let op = &intent.request.input.input_id.0;
        let rootkey = self.inner.layout.admission_root(shard);
        let mut budget = EqBudget::new();
        loop {
            budget.attempt(op).await?;
            let (v, mut root) = budget
                .io(op, self.load::<JournalRoot>(&rootkey))
                .await?
                .ok_or_else(|| invalid("journal_root_missing"))?;
            if root.pending.remove(&intent.sequence).is_none() {
                return Ok(());
            }
            if budget
                .io(op, self.save(&rootkey, Some(v), &root, op))
                .await?
            {
                return Ok(());
            }
        }
    }
    pub(super) async fn intent(&self, shard: ShardId, seq: u64) -> Result<Intent, StoreError> {
        if let Some((_, i)) = self
            .load(&self.inner.layout.admission_intent(shard, seq))
            .await?
        {
            self.validate_intent(&i)?;
            return Ok(i);
        }
        let (_, root) = self
            .load::<JournalRoot>(&self.inner.layout.admission_root(shard))
            .await?
            .ok_or_else(|| invalid("journal_root_missing"))?;
        let i = root
            .pending
            .get(&seq)
            .ok_or_else(|| invalid("journal_intent_missing"))?
            .clone();
        self.materialize(&i).await?;
        Ok(i)
    }
    pub(super) async fn select(&self, intent: &Intent) -> Result<(), StoreError> {
        self.validate_intent(intent)?;
        let key = self
            .inner
            .layout
            .canonical_admission(&intent.request.scope, &intent.request.input.input_id)?;
        let c = self.initial_canonical(intent);
        self.save(&key, None, &c, &intent.request.input.input_id.0)
            .await?;
        self.canonical(&key)
            .await?
            .ok_or_else(|| unknown(&intent.request.input.input_id.0))?;
        self.release_precanonical(intent).await
    }
    pub(super) fn initial_canonical(&self, intent: &Intent) -> Canonical {
        Canonical {
            schema_version: 1,
            intent: intent.clone(),
            position: None,
            decision: None,
            registered: false,
            append_attempts: 0,
            append_credits: 1,
            phase: Phase::Selected,
        }
    }
}
