use super::*;
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Scan {
    schema_version: u32,
    next: usize,
    cut: usize,
    pending: usize,
    round: u64,
    work: Option<Work>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Work {
    did: Did,
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
        if scan.schema_version != 0 {
            format(scan.schema_version)?;
        }
        scan.schema_version = 1;
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
                        work.due = doc.value.due;
                        work.phase = Phase::Hint;
                    } else {
                        scan.next += 1;
                        scan.work = None;
                        finished = scan.next >= scan.cut;
                    }
                }
                Phase::Hint => {
                    let _ = self.hint_due(shard, &work.did, work.due).await;
                    scan.next += 1;
                    scan.work = None;
                    finished = scan.next >= scan.cut;
                }
            }
        } else {
            let (_, root) = self.journal(shard).await?;
            if root.directory.is_empty() {
                return Ok(Step {
                    item: None,
                    finished: true,
                    round: scan.round,
                });
            }
            if scan.cut == 0 || scan.next >= scan.cut {
                scan.cut = root.directory.len();
                scan.next = 0;
                scan.round = scan
                    .round
                    .checked_add(1)
                    .ok_or_else(|| invalid("dispatch_scan_exhausted"))?;
            }
            let did = root
                .directory
                .get(scan.next)
                .ok_or_else(|| invalid("dispatch_directory_rollback"))?
                .did
                .clone();
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
