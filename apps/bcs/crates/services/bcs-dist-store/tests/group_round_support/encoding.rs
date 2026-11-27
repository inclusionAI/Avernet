//! Actual-encoding budget fixtures for the T1 protocol prototype (S1.3).
//! "实际编码" here means: construct the real serialized envelopes — shells,
//! JSON escaping, history, digest references and dereferenced content — and
//! measure their encoded byte length; boundaries are exercised at exactly
//! B−1/B/B+1 total encoded bytes, never by raw-blob reads alone. Encoding runs
//! into a counting sink, so a whole envelope is never materialized just to
//! measure it. Budgets used by tests are explicit small values; production
//! magnitudes come from `RoundBudgetProfile::v1()` and the merged #4245
//! constants. None of this is production IO (T2/T4/T6/T7/T8).
use super::*;
use super::lifecycle::digest;
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;

/// Publication wire budgets for one round: internal feed event/batch/snapshot
/// (#4245 shape: 128 KiB events, 256 KiB batch/snapshot) plus the public
/// Get/SSE frame candidate. Explicit small values are test-only boundary
/// forcing, NOT production defaults.
pub struct WireBudgets {
    pub event_bytes: usize,
    pub batch_bytes: usize,
    pub snapshot_bytes: usize,
    pub frame_bytes: usize,
}
impl WireBudgets {
    pub fn from_profile(profile: &RoundBudgetProfile) -> Self {
        Self {
            event_bytes: profile.feed_event_bytes,
            batch_bytes: profile.feed_batch_bytes,
            snapshot_bytes: profile.feed_batch_bytes,
            frame_bytes: profile.public_frame_bytes,
        }
    }
}

/// A restored public frame plus its measured encoded length.
pub struct ResolvedFrame {
    pub value: Value,
    pub encoded_bytes: usize,
}

/// Measured encoded length of `value`: a real serde_json serialization into
/// a counting sink; the whole envelope is never materialized for measurement.
pub fn encoded_len(value: &impl serde::Serialize) -> Result<usize, StoreError> {
    struct Sink(usize);
    impl std::io::Write for Sink {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0 += buf.len();
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
    }
    let mut sink = Sink(0);
    serde_json::to_writer(&mut sink, value).map_err(|_| error("encode"))?;
    Ok(sink.0)
}

/// `build(n)` constructs a value whose only n-dependence is a repeated
/// character with a fixed encoded cost per char ('x' costs 1 byte inside a
/// JSON string; the escape sample '"' costs 2). Returns the n that reaches
/// `target` total encoded bytes exactly, so boundaries are computed positions.
pub fn solve_filler(target: usize, build: impl Fn(usize) -> Value) -> Result<usize, StoreError> {
    let base = encoded_len(&build(0))?;
    if target < base { return Err(error("filler_unreachable")); }
    if target == base { return Ok(0); }
    let unit = encoded_len(&build(1))? - base;
    if unit == 0 || (target - base) % unit != 0 { return Err(error("filler_unreachable")); }
    Ok((target - base) / unit)
}

/// Simulated downstream response stream: yields up to `avail` chunk bytes and
/// counts what it delivered. A >8 MiB result is never materialized before the
/// bounded read stops at the caller's limit.
pub struct ChunkSource {
    avail: usize,
    pub read: usize,
}
impl ChunkSource {
    pub fn new(avail: usize) -> Self { Self { avail, read: 0 } }
}
impl std::io::Read for ChunkSource {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = buf.len().min(self.avail);
        if n > 0 {
            buf[..n].fill(b'x');
            self.avail -= n;
            self.read += n;
        }
        Ok(n)
    }
}

/// One filler text part whose repeated `ch` costs a fixed number of encoded
/// bytes per char ('x' = 1; '"' = 2 under JSON escaping).
pub fn filler_part(fill: usize, ch: char) -> RoundPart {
    RoundPart::Text { text: std::iter::repeat(ch).take(fill).collect() }
}

/// Ordered parts for the chain fixture: an escaping sample (quotes, backslash,
/// newline, tab, CJK), a data part, and the filler part. The SAME ordered set
/// flows through inbound, internal feed and public Get/SSE encodings.
pub fn chain_parts(fill: usize, ch: char) -> Vec<RoundPart> {
    vec![
        RoundPart::Text { text: "\"\\ \n \t \"引号样本\"".into() },
        RoundPart::Data { data: json!({"seq": 1, "text": "\"quoted\" \\ tail", "中": "文"}) },
        filler_part(fill, ch),
    ]
}

/// serde-encoded part values, preserving order.
pub fn part_values(parts: &[RoundPart]) -> Result<Vec<Value>, StoreError> {
    parts.iter().map(|p| serde_json::to_value(p).map_err(|_| error("encode"))).collect()
}

/// The round result payload: the same ordered artifacts, carried through the
/// internal feed and restored by the public frame.
pub fn artifacts_result(parts: &[RoundPart]) -> Result<Value, StoreError> {
    Ok(json!({"artifacts": part_values(parts)?}))
}

/// Fully resolved inbound envelope: shells, JSON-escaped parts, digest
/// references (workflow + history cut) and the DEREFERENCED history content.
/// Its total encoded length — not any raw blob length — is what the inbound
/// budget governs.
pub fn inbound_envelope(input: &RoundInput, history_entries: &Value) -> Result<Value, StoreError> {
    Ok(json!({
        "schema_version": RoundSchemaVersion::V1,
        "source": input.source,
        "key": input.key,
        "ordered_parts": input.ordered_parts,
        "workflow_ref": input.workflow_ref,
        "history_cut": input.history_cut,
        "history_entries": history_entries["entries"],
        "execution_deadline": input.execution_deadline,
    }))
}

/// Complete public wire frame for Get/SSE: shell, status/commit identity,
/// JSON-escaped parts, the history projection rows and the dereferenced
/// result. `history_rows == 0` is the default projection (history stays a
/// digest reference in the shell); non-default rows ≤ 100.
pub fn frame_value(parts: &Value, history_rows: &Value, result: &Value,
    key: &RoundKey, status: &str, commit_seq: u64) -> Result<Value, StoreError> {
    Ok(json!({
        "schema_version": 1,
        "kind": "group_round_public_frame",
        "key": key,
        "status": status,
        "commit_seq": commit_seq,
        "parts": parts,
        "history": history_rows,
        "result": result,
    }))
}

// Event/snapshot wire shapes shared by the feed permission builder and the
// tests' boundary calibration: the measured object IS the published object.
pub fn artifact_event(key: &RoundKey, seq: u64, result: &Value) -> Value {
    json!({"key": key, "id": [seq, 1], "kind": "artifact", "result": result})
}
pub fn artifact_ref_event(key: &RoundKey, seq: u64, store: &BlobRef) -> Value {
    json!({"key": key, "id": [seq, 1], "kind": "artifact_ref", "ref": store})
}
pub fn terminal_event(key: &RoundKey, seq: u64, status: &str, reason: Option<&str>) -> Value {
    match reason {
        Some(reason) => json!({"key": key, "id": [seq, 2], "kind": "terminal",
            "status": status, "reason": reason}),
        None => json!({"key": key, "id": [seq, 2], "kind": "terminal", "status": status}),
    }
}

/// Does this publication permission carry digest-referenced artifacts? Those
/// stay pinned (physically retained) until append+publish complete; a large
/// artifact over the inline event budget is exactly such a reference.
pub fn permission_has_digest_refs(permit: &Value) -> bool {
    permit["events"].as_array().is_some_and(|events|
        events.iter().any(|e| e["kind"] == "artifact_ref"))
        || permit["snapshot"].get("result").and_then(Value::as_object)
            .is_some_and(|o| o.contains_key("digest") && o.contains_key("key"))
}

impl Prototype {
    /// Pin an immutable, content-addressed history blob and return its digest
    /// reference (create-if-absent; a different document at the same content
    /// address is an error, never a silent overwrite).
    pub async fn pin_history(&self, entries: &[Value]) -> Result<BlobRef, StoreError> {
        let value = json!({"entries": entries});
        let pinned = digest(&value)?;
        let key = format!("t1-history-{}", &pinned.0[..16]);
        match self.document(&key).await? {
            Some((_, old)) if old == value => (),
            Some(_) => return Err(error("history_conflict")),
            None => if !self.replace(&key, None, value).await? {
                return Err(dependency("history_race"));
            },
        }
        Ok(BlobRef { key, digest: pinned })
    }

    /// Resolve the fully dereferenced inbound envelope (history through its
    /// digest reference) and enforce the complete-envelope inbound budget.
    pub async fn resolve_inbound(&self, input: &RoundInput,
        inbound_bytes: usize) -> Result<Value, StoreError> {
        let (_, history) = self.document(&input.history_cut.key).await?
            .ok_or_else(|| dependency("history_missing"))?;
        if digest(&history)? != input.history_cut.digest { return Err(error("history_digest")); }
        let envelope = inbound_envelope(input, &history)?;
        if encoded_len(&envelope)? > inbound_bytes {
            return Err(dependency("inbound_budget"));
        }
        Ok(envelope)
    }

    /// Assemble and count the complete public Get frame from the committed
    /// view: result through digest-verified refs, history projection rows
    /// through the pinned history blob. Default projection is 0 rows; a
    /// projection that cannot be fully encoded within the frame budget is
    /// rejected as a whole, never truncated.
    pub async fn round_frame(&self, key: &RoundKey, history_rows: usize,
        profile: &RoundBudgetProfile) -> Result<ResolvedFrame, StoreError> {
        if history_rows > 100 { return Err(error("projection_limits")); }
        let view = self.get(key).await?.ok_or_else(|| error("round_missing"))?;
        let Some(terminal) = &view.terminal else { return Err(error("round_not_terminal")); };
        let (_, history) = self.document(&view.accepted.input.history_cut.key).await?
            .ok_or_else(|| dependency("history_missing"))?;
        if digest(&history)? != view.accepted.input.history_cut.digest {
            return Err(error("history_digest"));
        }
        let rows: Vec<Value> = history["entries"].as_array().cloned().unwrap_or_default()
            .into_iter().take(history_rows).collect();
        let parts = serde_json::to_value(&view.accepted.input.ordered_parts)
            .map_err(|_| error("encode"))?;
        let result = match view.result {
            Some(Payload::Inline(value)) => value,
            Some(Payload::Ref(reference)) => {
                let (_, archived) = self.document(&reference.key).await?
                    .ok_or_else(|| dependency("archive_missing"))?;
                if digest(&archived["result"])? != reference.digest {
                    return Err(error("archive_mismatch"));
                }
                archived["result"].clone()
            }
            None => Value::Null,
        };
        let status = match terminal.status {
            TerminalStatus::Completed => "completed",
            TerminalStatus::Failed => "failed",
            TerminalStatus::Canceled => "canceled",
            TerminalStatus::Rejected => "rejected",
        };
        let frame = frame_value(&parts, &json!(rows), &result, key, status, terminal.commit_seq.0)?;
        let encoded = encoded_len(&frame)?;
        if encoded > profile.public_frame_bytes {
            return Err(dependency("projection_frame_budget"));
        }
        Ok(ResolvedFrame { value: frame, encoded_bytes: encoded })
    }

    /// Restore the SSE channel frame from the published snapshot: the digest
    /// reference is dereferenced and verified before the same complete wire
    /// frame is rebuilt. Writing the internal event never implies this
    /// restores OK.
    pub async fn sse_frame(&self, key: &RoundKey,
        profile: &RoundBudgetProfile) -> Result<ResolvedFrame, StoreError> {
        let snapshot = self.feed_snapshot(key).await?;
        if snapshot["key"] != encode(key)? { return Err(error("snapshot_identity")); }
        let result = match snapshot["result"].get("digest").and_then(Value::as_str) {
            Some(recorded) => {
                let path = snapshot["result"]["key"].as_str().ok_or_else(|| error("snapshot_ref"))?;
                let (_, archived) = self.document(path).await?
                    .ok_or_else(|| dependency("archive_missing"))?;
                if digest(&archived["result"])? != Digest(recorded.into()) {
                    return Err(error("archive_mismatch"));
                }
                archived["result"].clone()
            }
            None => snapshot["result"].clone(),
        };
        let view = self.get(key).await?.ok_or_else(|| error("round_missing"))?;
        let Some(terminal) = &view.terminal else { return Err(error("round_not_terminal")); };
        let parts = serde_json::to_value(&view.accepted.input.ordered_parts)
            .map_err(|_| error("encode"))?;
        let status = snapshot["status"].as_str().ok_or_else(|| error("snapshot_status"))?;
        let frame = frame_value(&parts, &json!([]), &result, key, status, terminal.commit_seq.0)?;
        let encoded = encoded_len(&frame)?;
        if encoded > profile.public_frame_bytes { return Err(dependency("frame_budget")); }
        Ok(ResolvedFrame { value: frame, encoded_bytes: encoded })
    }

    /// Test-calibration probe: the checkpoint commit sequence a subsequent
    /// finish/fail commit will observe (single-threaded test flows only).
    pub async fn commit_head(&self, sid: &Sid) -> Result<u64, StoreError> {
        let e = self.store.load(sid).await?.ok_or_else(|| error("checkpoint_missing"))?;
        Ok(e.checkpoint.commit_seq.0)
    }
    /// 引用 pin 判定: append+publish 均已完成才允许物理删除带摘要引用的产物。
    pub(super) async fn publication_complete(&self, key: &RoundKey,
        permit: &Value) -> Result<bool, StoreError> {
        let append_done = match self.document(&format!("feed:{}", key.sid.0)).await? {
            Some((_, feed)) => feed["batches"].as_array()
                .is_some_and(|b| b.iter().any(|x| x == permit)),
            None => false,
        };
        let publish_done = match self.document(&format!("feed-snapshot:{}", key.tid.0)).await? {
            Some((_, snapshot)) => snapshot == permit["snapshot"],
            None => false,
        };
        Ok(append_done && publish_done)
    }
}
