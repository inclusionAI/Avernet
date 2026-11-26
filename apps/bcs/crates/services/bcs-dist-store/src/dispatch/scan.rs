use super::reclaim::RECLAIM_REMOVE_BOUND;
use super::*;
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Scan {
    /// The cursor's explicit durable format marker. It is deliberately NOT
    /// the JSON key `schema_version`: the store codec only ever reads and
    /// writes `schema_version: 1` documents, while the scan cursor has TWO
    /// durable formats — legacy index cursors (`next`/`cut`) and the stable
    /// key cursor written since this slice. 0 decodes from both a fresh
    /// never-written shard and any legacy cursor document (unknown JSON
    /// fields are ignored); 2 is the current stable-key format.
    #[serde(default, rename = "cursor_version")]
    schema_version: u32,
    /// Stable-key cursor: the highest directory key whose work finished in
    /// the current round. The next step works the first row with a strictly
    /// greater key inside the round bound.
    #[serde(default)]
    lower: u64,
    /// Per-round upper bound: the highest directory key existing at arm
    /// time (the key counter minus one). Rows appended later carry greater
    /// keys and are only discovered by subsequent rounds.
    #[serde(default)]
    upper: u64,
    /// Directory keys the walk of the previous round proved ended with
    /// transferred proof and no obligations. Re-validated and spliced out by
    /// the next round's arm (bounded by [`RECLAIM_REMOVE_BOUND`]).
    #[serde(default)]
    candidates: Vec<u64>,
    pending: usize,
    round: u64,
    work: Option<Work>,
}
/// Cursor version 1 held `cut`/`next` array indexes into a shrinkable
/// directory (serialized under `schema_version`); version 2 is the stable
/// key cursor (serialized as `cursor_version`).
const CURSOR_VERSION: u32 = 2;
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Work {
    did: Did,
    /// Stable key of the directory row this work item was taken from.
    #[serde(default)]
    seq: u64,
    intent: Option<Intent>,
    phase: Phase,
    due: Option<TimestampMillis>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
enum Phase {
    Apply,
    Clear,
    Read,
    Hint,
}
#[derive(Serialize, Deserialize)]
struct Token {
    namespace: String,
    shard: ShardId,
    round: u64,
}
pub(super) struct Step {
    pub item: Option<Did>,
    pub finished: bool,
    pub round: u64,
}
impl StoreDispatch {
    pub(super) async fn scan(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        if let Some(token) = &page.after {
            if token.0.len() > 1024 {
                return Err(invalid("dispatch_scan_token"));
            }
            let token: Token =
                serde_json::from_str(&token.0).map_err(|_| invalid("dispatch_scan_token"))?;
            if token.namespace != self.layout.namespace_identity() || token.shard != shard {
                return Err(invalid("dispatch_scan_scope"));
            }
        }
        let scheduler = self.scans.scheduler()?;
        let Some(job) = scheduler.reserve(shard)? else {
            return self.scan_page(shard, vec![], false, 0);
        };
        scheduler
            .poll(self, shard, now, page.limit.get().min(128), &job)
            .await
    }
    pub(super) fn scan_page(
        &self,
        shard: ShardId,
        items: Vec<Did>,
        finished: bool,
        round: u64,
    ) -> Result<Page<Did>, StoreError> {
        Ok(Page {
            items,
            next: if finished {
                None
            } else {
                Some(PageToken(
                    serde_json::to_string(&Token {
                        namespace: self.layout.namespace_identity(),
                        shard,
                        round,
                    })
                    .map_err(|_| invalid("dispatch_scan_token"))?,
                ))
            },
        })
    }
    pub(super) async fn scan_step(
        &self,
        shard: ShardId,
        now: TimestampMillis,
    ) -> Result<Step, StoreError> {
        let key = self.layout.dispatch_scan(shard);
        // This permit belongs to the retained phase, not to one caller's poll.
        let _scan_io = self
            .stores
            .scan_budget
            .try_io()
            .ok_or_else(|| unavailable("scan_io_busy"))?;
        let stored = self.read::<Scan>(&key).await?;
        let version = stored.as_ref().map(|s| s.version);
        let mut scan = stored.map(|s| s.value).unwrap_or_default();
        match scan.schema_version {
            // No current cursor format marker (0): a never-written shard
            // (absent default) OR a legacy `cut`/`next` index cursor. The
            // legacy positions were indexes into a directory that reclaim
            // can shrink, so no stable-key equivalence may be guessed: arm a
            // fresh round under the current version and drop any in-flight
            // work item — its durable obligations remain in the journal —
            // making the re-scan idempotent inside this protected write.
            // (Legacy documents decode into this arm: they carry no
            // `cursor_version`, so the renamed field defaults to 0.)
            0 => {
                scan = Scan {
                    schema_version: CURSOR_VERSION,
                    pending: scan.pending,
                    round: scan.round,
                    lower: 0,
                    upper: 0,
                    candidates: vec![],
                    work: None,
                };
            }
            v if v == CURSOR_VERSION => {}
            v => return Err(StoreError::UnsupportedFormat { format: v }),
        }
        let mut item = None;
        let mut finished = false;
        if let Some(work) = scan.work.as_mut() {
            match work.phase {
                Phase::Apply => {
                    let Some(intent) = work.intent.as_ref() else {
                        return Err(invalid("dispatch_scan_intent"));
                    };
                    match self.apply(intent).await {
                        Ok(_) => work.phase = Phase::Clear,
                        Err(_) => work.phase = Phase::Read,
                    }
                }
                Phase::Clear => {
                    let Some(intent) = work.intent.as_ref() else {
                        return Err(invalid("dispatch_scan_intent"));
                    };
                    let _ = self.clear_pending(intent).await;
                    work.phase = Phase::Read;
                }
                Phase::Read => {
                    // Native IO retains its bounded timeout across poll slices.
                    // A truly unavailable item rotates; its durable obligation
                    // remains in the directory/pending journal for another round.
                    let read = self.document(&work.did).await;
                    if let Ok(Some(doc)) = read {
                        if doc.value.due.is_some_and(|due| due.0 <= now.0) {
                            item = Some(work.did.clone());
                        }
                        if Self::reclaimable(&doc.value)
                            && scan.candidates.len() < RECLAIM_REMOVE_BOUND
                            && !scan.candidates.contains(&work.seq)
                        {
                            // The walk's own read proved the row ended with
                            // transferred proof and no obligations: hand it to
                            // the next arm's bounded splice.
                            scan.candidates.push(work.seq);
                        }
                        work.due = doc.value.due;
                        work.phase = Phase::Hint;
                    } else {
                        // The item's row is NOT deleted — stable keys make
                        // rotation index-free, and a reclaim-removed row
                        // simply no longer matches the window.
                        scan.lower = work.seq;
                        scan.work = None;
                        finished = scan.lower >= scan.upper;
                    }
                }
                Phase::Hint => {
                    let _ = self.hint_due(shard, &work.did, work.due).await;
                    scan.lower = work.seq;
                    scan.work = None;
                    finished = scan.lower >= scan.upper;
                }
            }
        } else {
            let (journal_version, journal) = self.journal(shard).await?;
            if journal.directory.is_empty() {
                return Ok(Step {
                    item: None,
                    finished: true,
                    round: scan.round,
                });
            }
            let root = if scan.lower >= scan.upper {
                // A new round first splices out the bounded candidate set the
                // previous walk proved reclaimable (≤ RECLAIM_REMOVE_BOUND
                // re-validating document reads, one journal CAS at most,
                // deferred on contention), then fixes this round's upper
                // bound to rows that exist now.
                scan.round = scan
                    .round
                    .checked_add(1)
                    .ok_or_else(|| invalid("dispatch_scan_exhausted"))?;
                let armed = self
                    .splice_reclaim_candidates(shard, journal_version, journal, &scan.candidates)
                    .await?;
                scan.candidates = armed.leftover;
                scan.lower = 0;
                scan.upper = armed.journal.next_seq.saturating_sub(1);
                armed.journal
            } else {
                journal
            };
            let entry = root
                .directory
                .iter()
                .find(|entry| entry.seq > scan.lower && entry.seq <= scan.upper);
            let Some(entry) = entry else {
                // No row remains in the round window (every unprocessed row
                // was reclaimed, or the arm met a now-emptied directory):
                // mark the round exhausted DURABLY so the NEXT step arms a
                // fresh round — stable keys make re-arming idempotent.
                scan.lower = scan.upper;
                return if self.write(&key, version, &scan).await? {
                    Ok(Step {
                        item: None,
                        finished: true,
                        round: scan.round,
                    })
                } else {
                    Ok(Step {
                        item: None,
                        finished: false,
                        round: scan.round,
                    })
                };
            };
            let did = entry.did.clone();
            let seq = entry.seq;
            let intent = if root.pending.is_empty() {
                None
            } else {
                let p = root.pending[scan.pending % root.pending.len()].clone();
                scan.pending = scan.pending.wrapping_add(1);
                Some(p)
            };
            let phase = if intent.is_some() {
                Phase::Apply
            } else {
                Phase::Read
            };
            scan.work = Some(Work {
                did,
                seq,
                intent,
                phase,
                due: None,
            });
        }
        // The save remains inside the retained phase. Cancellation never restarts
        // its preceding reads; a new process recovers from this durable cursor.
        if self.write(&key, version, &scan).await? {
            Ok(Step {
                item,
                finished,
                round: scan.round,
            })
        } else {
            Ok(Step {
                item: None,
                finished: false,
                round: scan.round,
            })
        }
    }
}
