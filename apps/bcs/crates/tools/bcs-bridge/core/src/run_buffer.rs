//! In-process retention: full Gateway replay or a checked Plugin delivery prefix.
use std::collections::VecDeque;
use bcs_protocol::stream::StreamEvent;
use crate::run::RunEvent;

pub(crate) const MAX_RETAINED_BYTES: usize = 32 * 1024 * 1024;
pub(crate) const MAX_RETAINED_EVENTS: usize = 16_384;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RetentionMode { GatewayReplay, PluginPending }

impl RetentionMode {
    pub fn label(self) -> &'static str {
        match self { Self::GatewayReplay => "gateway_replay", Self::PluginPending => "plugin_pending" }
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum DeliveryCursorError {
    #[error("delivery operation is not valid for this retention mode")]
    WrongMode,
    #[error("delivery cursor {cursor} precedes reclaimed prefix {released_through}")]
    Reclaimed { cursor: u64, released_through: u64 },
    #[error("delivery cursor {cursor} exceeds last emitted sequence {last_emitted_seq}")]
    Ahead { cursor: u64, last_emitted_seq: u64 },
    #[error("delivery commit {cursor} is not the next retained record after {released_through}")]
    NonContiguous { cursor: u64, released_through: u64 },
    #[error("Plugin read cursor {cursor} differs from committed consumption {committed_through}")]
    Uncommitted { cursor: u64, committed_through: u64 },
    #[error("retained event byte accounting is inconsistent")]
    Accounting,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RetentionStats {
    pub retained_events: usize,
    pub retained_bytes: usize,
    /// Locally consumed Plugin prefix; not a server acknowledgment.
    pub delivery_cursor: u64,
    pub last_emitted_seq: u64,
    pub peak_retained_events: usize,
    pub peak_retained_bytes: usize,
}

struct RetainedRecord { event: RunEvent, accounted_bytes: usize }

#[derive(Default)]
pub(crate) struct RunEventBuffer {
    records: VecDeque<RetainedRecord>,
    stats: RetentionStats,
    failure_retained: bool,
}

impl RunEventBuffer {
    pub(crate) fn stats(&self) -> RetentionStats { self.stats }
    pub(crate) fn failure_retained(&self) -> bool { self.failure_retained }

    pub(crate) fn limit_reason(&self, next_bytes: usize) -> Option<&'static str> {
        match (self.records.len() >= MAX_RETAINED_EVENTS,
            self.stats.retained_bytes.saturating_add(next_bytes) > MAX_RETAINED_BYTES) {
            (true, true) => Some("both"),
            (true, false) => Some("events"),
            (false, true) => Some("bytes"),
            (false, false) => None,
        }
    }

    /// Called under the run lock after checking the normal budget, or once for
    /// its terminal replacement. Sequence ownership remains with the run driver.
    pub(crate) fn append(&mut self, event: RunEvent, accounted_bytes: usize, failed: bool) {
        self.stats.last_emitted_seq = event.seq;
        self.stats.retained_bytes += accounted_bytes;
        self.records.push_back(RetainedRecord { event, accounted_bytes });
        self.stats.retained_events = self.records.len();
        self.stats.peak_retained_events = self.stats.peak_retained_events.max(self.stats.retained_events);
        self.stats.peak_retained_bytes = self.stats.peak_retained_bytes.max(self.stats.retained_bytes);
        self.failure_retained |= failed;
    }

    pub(crate) fn next_after(&self, cursor: u64) -> Result<Option<RunEvent>, DeliveryCursorError> {
        if cursor < self.stats.delivery_cursor {
            return Err(DeliveryCursorError::Reclaimed {cursor, released_through:self.stats.delivery_cursor});
        }
        if cursor > self.stats.last_emitted_seq {
            return Err(DeliveryCursorError::Ahead {cursor, last_emitted_seq:self.stats.last_emitted_seq});
        }
        let Some(first) = self.records.front() else { return Ok(None) };
        let index = if cursor < first.event.seq { 0 } else { cursor - first.event.seq + 1 };
        Ok(usize::try_from(index).ok().and_then(|index| self.records.get(index)).map(|record| record.event.clone()))
    }

    pub(crate) fn snapshot_after(&self, seq: u64) -> Vec<RunEvent> {
        self.records.iter().filter(|record| record.event.seq > seq).map(|record| record.event.clone()).collect()
    }

    pub(crate) fn commit_delivery_through(&mut self, cursor: u64) -> Result<(), DeliveryCursorError> {
        if cursor <= self.stats.delivery_cursor { return Ok(()); }
        let error = || DeliveryCursorError::NonContiguous {cursor, released_through:self.stats.delivery_cursor};
        if self.stats.delivery_cursor.checked_add(1) != Some(cursor) { return Err(error()); }
        let first = self.records.front().filter(|record| record.event.seq == cursor).ok_or_else(error)?;
        let remaining = self.stats.retained_bytes.checked_sub(first.accounted_bytes).ok_or(DeliveryCursorError::Accounting)?;
        self.records.pop_front();
        self.stats.retained_events = self.records.len();
        self.stats.retained_bytes = remaining;
        self.stats.delivery_cursor = cursor;
        Ok(())
    }

    /// Terminal disposal also covers explicit failed-delivery/shutdown paths.
    /// Do not misrepresent discarded records as successfully consumed.
    pub(crate) fn clear(&mut self) {
        self.records.clear();
        self.records.shrink_to_fit();
        self.stats.retained_events = 0;
        self.stats.retained_bytes = 0;
    }
}

/// Count serialized bytes without allocating another copy of a large payload.
fn serialized_size<T: serde::Serialize>(value: &T) -> Result<usize, serde_json::Error> {
    struct Counter(usize);
    impl std::io::Write for Counter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0 = self.0.saturating_add(bytes.len());
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
    }
    let mut counter = Counter(0);
    serde_json::to_writer(&mut counter, value)?;
    Ok(counter.0)
}

/// Account for all typed fields and raw snapshots, independent of any wire format.
pub(crate) fn retained_event_size(event: &StreamEvent) -> Result<usize, serde_json::Error> {
    use bcs_protocol::stream::AgentData;
    let size = match event {
        StreamEvent::Chat(c) => serialized_size(&(&c.run_id, c.seq, c.ts, &c.state, &c.session_id,
            &c.content, &c.delta_text, &c.stop_reason, &c.error_message, &c.error_kind, &c.error_code, &c.message, &c.raw))?,
        StreamEvent::Agent(a) => {
            let metadata = serialized_size(&(&a.run_id, a.seq, a.ts, &a.session_id, &a.raw))?;
            metadata + match &a.data {
                AgentData::Tool(data) => serialized_size(data)?,
                AgentData::Thinking(data) => serialized_size(data)?,
                AgentData::Approval(data) => serialized_size(data)?,
                AgentData::Lifecycle(data) => serialized_size(data)?,
                AgentData::Phase(data) => serialized_size(data)?,
                AgentData::Unknown { stream, raw } => serialized_size(&(stream, raw))?,
                AgentData::Assistant { raw } | AgentData::Error { raw } => serialized_size(raw)?,
            }
        }
        StreamEvent::Interaction(i) => serialized_size(&(&i.run_id, i.seq, i.ts, &i.session_id,
            &i.phase, &i.interaction_id, &i.kind, &i.raw))?,
        StreamEvent::Unknown { event, raw } => serialized_size(&(event, raw))?,
        StreamEvent::Ping { ts } => serialized_size(ts)?,
    };
    Ok(size + size_of::<RetainedRecord>())
}


#[cfg(test)]
#[path = "run_buffer_tests.rs"]
mod tests;
