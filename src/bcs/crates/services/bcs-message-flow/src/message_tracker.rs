use std::collections::{HashMap, HashSet};
use serde_json::Value;
use tokio::sync::Mutex;

pub struct ToolCallStartInfo {
    pub run_id: String,
    pub session_id: String,
    pub args: Value,
}

/// In-memory tracker coordinating tool call lifecycle and streaming chat
/// segmentation.
///
/// Streaming chat deltas are **buffered in memory** (not written to DB per
/// token). Only at segment boundaries (tool_call, final) is the buffered text
/// flushed as a single INSERT — drastically reducing DB write pressure for
/// high-frequency per-token delta streams.
pub struct MessageTracker {
    /// tool_call_id → ToolCallStartInfo (cached on ToolCallStart, consumed on ToolCallEnd)
    tool_call_starts: Mutex<HashMap<String, ToolCallStartInfo>>,
    /// run_id:tool_call_id → first-seen timestamp for coordination echoes.
    coordination_echoes: Mutex<HashMap<String, u64>>,
    /// run_id → text buffered for the CURRENT open chat segment.
    ///
    /// Two producers write here, depending on what the upstream frame carries:
    /// - **Plugin (WS) path**: frames carry already-sliced per-segment text in
    ///   `message.content`. [`buffer_chat_text`] REPLACES the buffer (one frame
    ///   per segment). This is the legacy behavior, kept byte-for-byte.
    /// - **SSE (raw engine) path**: frames carry an incremental `delta_text`.
    ///   [`append_chat_delta`] APPENDS each delta so BCS stitches the segment
    ///   itself instead of trusting the engine's cumulative `message.content`.
    ///
    /// Flushed (INSERT) when a segment boundary arrives (tool_call / thinking /
    /// approval / final), then cleared. The next chat delta starts a new buffer.
    streaming_chat_buf: Mutex<HashMap<String, String>>,
    /// run_ids that have received at least one `delta_text` frame, i.e. runs in
    /// self-accumulating (SSE) mode. Used at `final` to decide whether to flush
    /// the accumulated buffer (delta mode) or override with the final frame's
    /// full text (legacy plugin mode). Cleared on run cleanup.
    chat_delta_mode: Mutex<HashSet<String>>,
    /// run_id → text buffered for the CURRENT open thinking segment.
    ///
    /// Raw BCN SSE thinking frames may carry run-cumulative `data.text` across
    /// tool blocks. BCS rebuilds segment-local thinking text from `data.delta`
    /// and clears this buffer whenever a non-thinking stream event is observed.
    streaming_thinking_buf: Mutex<HashMap<String, String>>,
    /// run_id → channel sender display name resolved from the bot registry.
    ///
    /// Provider bots do not heartbeat like WebSocket bots, so their registry
    /// memory entry can expire while the persisted record remains available.
    /// Cache the display-only lookup for the run instead of querying storage on
    /// every streaming delta.
    channel_sender_labels: Mutex<HashMap<String, String>>,
}

impl MessageTracker {
    pub fn new() -> Self {
        Self {
            tool_call_starts: Mutex::new(HashMap::new()),
            coordination_echoes: Mutex::new(HashMap::new()),
            streaming_chat_buf: Mutex::new(HashMap::new()),
            chat_delta_mode: Mutex::new(HashSet::new()),
            streaming_thinking_buf: Mutex::new(HashMap::new()),
            channel_sender_labels: Mutex::new(HashMap::new()),
        }
    }

    // -- Tool call lifecycle --

    pub async fn cache_tool_call_start(&self, tool_call_id: String, info: ToolCallStartInfo) {
        let mut pending = self.tool_call_starts.lock().await;
        pending.insert(tool_call_id, info);
    }

    pub async fn take_tool_call_start(&self, tool_call_id: &str) -> Option<ToolCallStartInfo> {
        let mut pending = self.tool_call_starts.lock().await;
        pending.remove(tool_call_id)
    }

    pub async fn mark_coordination_echo_seen(&self, key: String, now_ms: u64, ttl_ms: u64) -> bool {
        let mut seen = self.coordination_echoes.lock().await;
        seen.retain(|_, seen_at| now_ms.saturating_sub(*seen_at) <= ttl_ms);
        if seen.contains_key(&key) {
            return false;
        }
        seen.insert(key, now_ms);
        true
    }

    // -- Streaming chat segmentation (memory buffer, flush-at-boundary) --

    /// Buffer already-sliced per-segment text for the run (legacy plugin path).
    /// REPLACES the buffer — the plugin sends one sliced frame per segment.
    /// Called on every chat delta — only touches memory, never DB.
    pub async fn buffer_chat_text(&self, run_id: &str, text: String) {
        let mut map = self.streaming_chat_buf.lock().await;
        map.insert(run_id.to_string(), text);
    }

    /// Append an incremental `delta_text` to the run's current segment (SSE
    /// path) and mark the run as delta mode. Unlike [`buffer_chat_text`], this
    /// STITCHES successive deltas so BCS owns segment assembly rather than
    /// relying on the engine's cumulative `message.content`. Memory only.
    pub async fn append_chat_delta(&self, run_id: &str, delta: &str) {
        {
            let mut modes = self.chat_delta_mode.lock().await;
            modes.insert(run_id.to_string());
        }
        let mut map = self.streaming_chat_buf.lock().await;
        map.entry(run_id.to_string()).or_default().push_str(delta);
    }

    /// Append an incremental thinking `delta` to the run's current thinking
    /// segment and return the segment-accumulated text. Memory only.
    pub async fn append_thinking_delta(&self, run_id: &str, delta: &str) -> String {
        let mut map = self.streaming_thinking_buf.lock().await;
        if let Some(buf) = map.get_mut(run_id) {
            buf.push_str(delta);
            return buf.clone();
        }
        let mut buf = String::new();
        buf.push_str(delta);
        map.insert(run_id.to_string(), buf.clone());
        buf
    }

    /// Clear the current thinking segment buffer for the run.
    pub async fn clear_thinking_buf(&self, run_id: &str) {
        self.streaming_thinking_buf.lock().await.remove(run_id);
    }

    pub async fn channel_sender_label(&self, run_id: &str) -> Option<String> {
        self.channel_sender_labels.lock().await.get(run_id).cloned()
    }

    pub async fn cache_channel_sender_label(&self, run_id: &str, label: String) {
        self.channel_sender_labels
            .lock()
            .await
            .insert(run_id.to_string(), label);
    }

    /// Whether the run has received any `delta_text` frame (SSE self-accumulate
    /// mode). At `final`, delta-mode runs flush their accumulated buffer instead
    /// of overriding with the final frame's cumulative full text.
    pub async fn is_chat_delta_mode(&self, run_id: &str) -> bool {
        self.chat_delta_mode.lock().await.contains(run_id)
    }

    /// Take (drain) the buffered chat text for the run, clearing it. Returns
    /// `None` if no delta was buffered in this segment. Called at segment
    /// boundaries (tool_call / thinking / approval / final) to flush to DB.
    pub async fn take_chat_buf(&self, run_id: &str) -> Option<String> {
        let mut map = self.streaming_chat_buf.lock().await;
        map.remove(run_id)
    }

    /// Check whether the run has a pending chat buffer (for deciding whether
    /// the final can just INSERT fresh or should use the buffered text).
    pub async fn has_chat_buf(&self, run_id: &str) -> bool {
        let map = self.streaming_chat_buf.lock().await;
        map.contains_key(run_id)
    }

    /// Read (without draining) the run's current segment-accumulated chat text.
    /// Used to synthesize `message.content` for the frontend relay on each delta
    /// (the frontend SDK renders segment-cumulative `message.content`, not the
    /// raw `delta_text`). Returns `None` if nothing is buffered yet.
    pub async fn peek_chat_buf(&self, run_id: &str) -> Option<String> {
        let map = self.streaming_chat_buf.lock().await;
        map.get(run_id).cloned()
    }

    /// Clean up all per-run tracking when a run reaches a terminal state.
    /// Returns any pending chat buffer that was not yet flushed.
    pub async fn cleanup_run(&self, run_id: &str) -> Option<String> {
        self.chat_delta_mode.lock().await.remove(run_id);
        self.streaming_thinking_buf.lock().await.remove(run_id);
        self.channel_sender_labels.lock().await.remove(run_id);
        self.streaming_chat_buf.lock().await.remove(run_id)
    }
}
