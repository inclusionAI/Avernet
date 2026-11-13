//! Shared pure Provider SSE block framing and sequence classification. No I/O.

#[derive(Debug, Clone)]
pub struct SseFrame {
    pub event: String,
    /// SSE `id:` field. Parsed per the SSE spec and asserted by tests, but the
    /// read loop dedupes off `StreamEvent.seq` instead (review #4), so this is
    /// not read by the ingest path.
    #[allow(dead_code)]
    pub id: Option<u64>,
    pub data: String,
}

/// Parse one SSE frame block (lines separated by `\n`, fields `event:`/`id:`/`data:`).
/// Multiple `data:` lines are joined with `\n` per the SSE spec.
pub fn parse_sse_block(block: &str) -> Option<SseFrame> {
    let mut event = String::new();
    let mut id: Option<u64> = None;
    let mut data_lines: Vec<String> = Vec::new();
    for line in block.lines() {
        let line = line.trim_end_matches('\r');
        if line.is_empty() || line.starts_with(':') {
            continue; // blank or comment
        }
        let (field, value) = match line.split_once(':') {
            Some((f, v)) => (f, v.strip_prefix(' ').unwrap_or(v)),
            None => (line, ""),
        };
        match field {
            "event" => event = value.to_string(),
            "id" => id = value.trim().parse::<u64>().ok(),
            "data" => data_lines.push(value.to_string()),
            _ => {}
        }
    }
    if event.is_empty() && data_lines.is_empty() {
        return None;
    }
    Some(SseFrame {
        event: if event.is_empty() { "message".to_string() } else { event },
        id,
        data: data_lines.join("\n"),
    })
}

#[derive(Debug, Default)]
pub struct SeqDedup {
    last_seq: Option<u64>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SeqDecision {
    Accept,
    Duplicate,
    Gap(u64),
}

impl SeqDedup {
    /// Compare against the shared agent/chat/interaction sequence.
    /// Ping and the legacy gated approval path do not participate.
    pub fn accept(&mut self, seq: Option<u64>) -> SeqDecision {
        let Some(seq) = seq else {
            // Missing seq: accept but caller records a metric.
            return SeqDecision::Accept;
        };
        match self.last_seq {
            Some(last) if seq <= last => SeqDecision::Duplicate,
            Some(last) if seq > last + 1 => {
                let gap = seq - last - 1;
                self.last_seq = Some(seq);
                SeqDecision::Gap(gap)
            }
            _ => {
                self.last_seq = Some(seq);
                SeqDecision::Accept
            }
        }
    }
}
