//! Shard-batched reclaim of ended dispatch directory rows (plan T7 slice 3).
//!
//! Reclaim only removes a did's *discovery row* from the shard journal; the
//! dispatch document itself stays durable, so it keeps fencing late writes
//! from its retained receipts/terminal evidence and same-did rebuilds stay
//! conflicts. Every precondition below is proven per entry — never by TTL or
//! by raising the 1,024-entry cap:
//!
//! * ended with a *definitive* outcome (Completed/Failed/Canceled, never
//!   `Unknown`) and the terminal proof durably recorded with the document
//!   ("已终结、证明已转存" — the retained evidence stays readable via `get`
//!   after the row is gone);
//! * no recheck supervision (`due` settled), no queued unconfirmed input;
//! * no unobserved cancellation request in flight;
//! * no replayable store effect: the journal's pending list holds no intent
//!   for the did. (A live claim cannot exist: claims against an Ended
//!   dispatch answer `ClaimOutcome::Ended`.)
//!
//! Two entry points share the proofs:
//!
//! * the scan round hands the arm a bounded candidate list collected during
//!   its own walk (`splice_reclaim_candidates`): the candidate was proven
//!   reclaimable by the walk's document read, and the arm re-validates each
//!   candidate row before splicing it in one journal CAS; and
//! * the explicit `reclaim_dispatch_directory` batch, which walks its own
//!   bounded window after the journal's durable `reclaim_next` cursor.
//!
//! Neither path rescans history: the scan adds no document read beyond its
//! walk, the arm re-validates at most [`RECLAIM_REMOVE_BOUND`] rows, and the
//! explicit batch reads at most [`RECLAIM_EXAMINE_BOUND`] rows per call.
use super::*;

/// Upper bound of directory rows one explicit batch may read.
pub(crate) const RECLAIM_EXAMINE_BOUND: usize = 32;
/// Upper bound of rows one journal CAS may remove — also the bound of the
/// scan's candidate list and of the arm's re-validating reads.
pub(crate) const RECLAIM_REMOVE_BOUND: usize = 16;

/// The arm's splice result: the journal to arm this round over (spliced, or
/// the one read when the CAS is deferred by contention) and the candidates
/// to keep for a later arm.
pub(super) struct Splice {
    pub journal: Journal,
    pub leftover: Vec<u64>,
}
impl StoreDispatch {
    /// Reclaim one bounded batch of ended-and-proof-transferred directory
    /// rows of `shard`. Call again to make further progress; each call
    /// reads at most [`RECLAIM_EXAMINE_BOUND`] documents after the journal's
    /// durable reclaim cursor and removes at most [`RECLAIM_REMOVE_BOUND`]
    /// rows in one journal CAS — never a full-history rescan.
    pub async fn reclaim_dispatch_directory(&self, shard: ShardId) -> Result<usize, StoreError> {
        let _io = self
            .stores
            .scan_budget
            .try_io()
            .ok_or_else(|| unavailable("scan_io_busy"))?;
        let key = self.layout.dispatch_journal(shard);
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await?;
            let (version, mut root) = budget.io(&key, self.journal(shard)).await?;
            let batch = self.examined_window(&root).await?;
            if batch.removed.is_empty() {
                return Ok(0);
            }
            root.directory
                .retain(|entry| !batch.removed.contains(&entry.seq));
            root.reclaim_next = batch.cursor;
            if budget.io(&key, self.write(&key, version, &root)).await? {
                return Ok(batch.removed.len());
            }
            // Lost the journal CAS to a concurrent writer: re-read and retry
            // within the reclaim budget.
        }
    }

    /// The arm-time splice for the scan round: re-validate every candidate
    /// the previous walk proved reclaimable (one fresh document read, then
    /// the same durable preconditions), re-check the journal's pending
    /// list for replay effects on the did, and remove all confirmed rows in
    /// ONE journal CAS. On contention the splice defers: the arm proceeds
    /// over the journal it read and the candidates stay for the next arm.
    pub(super) async fn splice_reclaim_candidates(
        &self,
        shard: ShardId,
        version: Option<Version>,
        root: Journal,
        candidates: &[u64],
    ) -> Result<Splice, StoreError> {
        let journal_key = self.layout.dispatch_journal(shard);
        let mut confirmed = vec![];
        let mut leftover = vec![];
        for seq in candidates {
            let Some(entry) = root.directory.iter().find(|entry| entry.seq == *seq) else {
                // The row is already gone (an explicit batch reclaimed it).
                continue;
            };
            if root
                .pending
                .iter()
                .any(|p| matches!(p.did(), Ok(did) if *did == entry.did))
            {
                // A replayable effect for the did keeps its discovery row.
                leftover.push(*seq);
                continue;
            }
            match self.document(&entry.did).await {
                Ok(Some(doc)) => {
                    if Self::reclaimable(&doc.value) {
                        confirmed.push(*seq);
                    }
                }
                // No row (already handled by the lookup above), an
                // unreadable document, or a settled dispatch that took on new
                // obligations since its walk: not provable now, so never
                // removed now. The next round's walk re-examines it.
                Ok(None) | Err(_) => {}
            }
        }
        if confirmed.is_empty() {
            return Ok(Splice {
                journal: root,
                leftover,
            });
        }
        let mut spliced = root.clone();
        spliced
            .directory
            .retain(|entry| !confirmed.contains(&entry.seq));
        if self.write(&journal_key, version, &spliced).await? {
            Ok(Splice {
                journal: spliced,
                leftover,
            })
        } else {
            // Contention: defer the whole splice; stable keys keep the arm
            // safe over the journal it just read.
            let mut keep = leftover;
            keep.extend(confirmed);
            keep.sort_unstable();
            keep.dedup();
            Ok(Splice {
                journal: root,
                leftover: keep,
            })
        }
    }

    /// The per-entry reclaim preconditions (proof by durable document facts).
    pub(super) fn reclaimable(doc: &Document) -> bool {
        let ended = doc.ended() && doc.final_outcome().is_some();
        let proof_transferred = doc
            .connector
            .as_ref()
            .and_then(|connector| connector.finish_evidence.as_deref())
            .is_some();
        let no_supervision = doc.due.is_none();
        let no_queued_input = doc.view.control.pending_input.is_empty();
        let no_unobserved_cancel = doc.view.control.cancel.is_none()
            || !matches!(
                doc.view.execution.cancellation,
                CancelObservation::NotSent
                    | CancelObservation::Sending
                    | CancelObservation::RequestAccepted
                    | CancelObservation::OutcomeUnknown
            );
        ended && proof_transferred && no_supervision && no_queued_input && no_unobserved_cancel
    }
}

/// One bounded examination window of the explicit batch, after the journal's
/// `reclaim_next` cursor. Rows removed and the next cursor value travel
/// together; a window that cleanly exhausts the directory wraps the cursor
/// to 0 so rows which became reclaimable behind the cursor are found on a
/// later batch without ever scanning more than one window per call.
impl StoreDispatch {
    async fn examined_window(&self, root: &Journal) -> Result<Batch, StoreError> {
        let mut batch = Batch {
            removed: vec![],
            cursor: root.reclaim_next,
        };
        let mut examined = 0usize;
        let mut exhausted = true;
        for entry in root
            .directory
            .iter()
            .filter(|entry| entry.seq > root.reclaim_next)
        {
            if examined == RECLAIM_EXAMINE_BOUND || batch.removed.len() == RECLAIM_REMOVE_BOUND {
                exhausted = false;
                break;
            }
            examined += 1;
            batch.cursor = entry.seq;
            if root
                .pending
                .iter()
                .any(|p| matches!(p.did(), Ok(did) if *did == entry.did))
            {
                continue;
            }
            let Some(doc) = self.document(&entry.did).await? else {
                continue;
            };
            if Self::reclaimable(&doc.value) {
                batch.removed.push(entry.seq);
            }
        }
        if exhausted {
            batch.cursor = 0;
        }
        Ok(batch)
    }
}
struct Batch {
    removed: Vec<u64>,
    cursor: u64,
}
