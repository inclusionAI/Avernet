//! Chat abort manager for handling cancellation of running chats.

use std::collections::HashMap;
use std::sync::Arc;

use tokio::sync::RwLock;
use tokio_util::sync::CancellationToken;

/// Entry for a running chat task.
#[derive(Debug, Clone)]
pub struct ChatRunEntry {
    /// Run ID (unique identifier for this execution).
    pub run_id: String,

    /// Session key.
    pub session_key: String,

    /// Cancellation token for abort support.
    pub token: CancellationToken,

    /// Timestamp when the run started (milliseconds since epoch).
    pub started_at_ms: u64,

    /// Timestamp when the run expires (milliseconds since epoch).
    pub expires_at_ms: u64,

    /// Connection ID that owns this run (optional).
    pub owner_conn_id: Option<String>,

    /// Buffer for accumulating delta text (for aborted state).
    pub buffer: Arc<RwLock<String>>,
}

/// Manager for chat run lifecycle and abort support.
#[derive(Debug, Default)]
pub struct ChatAbortManager {
    /// Active runs by run_id.
    runs: RwLock<HashMap<String, ChatRunEntry>>,

    /// Sequence numbers per run.
    seqs: RwLock<HashMap<String, u64>>,

    /// Completed buffers (for late queries).
    buffers: RwLock<HashMap<String, String>>,
}

impl ChatAbortManager {
    /// Create a new abort manager.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a new chat run.
    pub async fn register(&self, entry: ChatRunEntry) {
        let mut runs = self.runs.write().await;
        runs.insert(entry.run_id.clone(), entry);
    }

    /// Get a chat run entry.
    pub async fn get(&self, run_id: &str) -> Option<ChatRunEntry> {
        let runs = self.runs.read().await;
        runs.get(run_id).cloned()
    }

    /// Check if a run exists.
    pub async fn exists(&self, run_id: &str) -> bool {
        let runs = self.runs.read().await;
        runs.contains_key(run_id)
    }

    /// Remove a completed run.
    pub async fn remove(&self, run_id: &str) -> Option<ChatRunEntry> {
        let mut runs = self.runs.write().await;
        runs.remove(run_id)
    }

    /// Abort a specific run.
    ///
    /// Returns the buffered text (if any) for the aborted run.
    pub async fn abort(&self, run_id: &str) -> Option<String> {
        let mut runs = self.runs.write().await;

        if let Some(entry) = runs.remove(run_id) {
            entry.token.cancel();

            // Save buffer for late queries
            let buffer = entry.buffer.read().await.clone();
            let mut buffers = self.buffers.write().await;
            buffers.insert(run_id.to_string(), buffer.clone());

            Some(buffer)
        } else {
            None
        }
    }

    /// Abort all runs for a session.
    ///
    /// Returns list of (run_id, buffer) pairs for all aborted runs.
    pub async fn abort_session(&self, session_key: &str) -> Vec<(String, String)> {
        let mut runs = self.runs.write().await;
        let mut results = Vec::new();

        // Find all run IDs for this session
        let run_ids: Vec<String> = runs
            .iter()
            .filter(|(_, entry)| entry.session_key == session_key)
            .map(|(id, _)| id.clone())
            .collect();

        // Abort each run
        for run_id in run_ids {
            if let Some(entry) = runs.remove(&run_id) {
                entry.token.cancel();
                let buffer = entry.buffer.read().await.clone();

                // Save buffer
                let mut buffers = self.buffers.write().await;
                buffers.insert(run_id.clone(), buffer.clone());

                results.push((run_id, buffer));
            }
        }

        results
    }

    /// Get the next sequence number for a run.
    pub async fn next_seq(&self, run_id: &str) -> u64 {
        let mut seqs = self.seqs.write().await;
        let seq = seqs.entry(run_id.to_string()).or_insert(0);
        *seq += 1;
        *seq
    }

    /// Get a saved buffer for a completed/aborted run.
    pub async fn get_buffer(&self, run_id: &str) -> Option<String> {
        let buffers = self.buffers.read().await;
        buffers.get(run_id).cloned()
    }

    /// Clean up expired runs.
    pub async fn cleanup_expired(&self, now_ms: u64) -> Vec<String> {
        let mut runs = self.runs.write().await;

        let expired: Vec<String> = runs
            .iter()
            .filter(|(_, entry)| entry.expires_at_ms < now_ms)
            .map(|(id, _)| id.clone())
            .collect();

        for run_id in &expired {
            if let Some(entry) = runs.remove(run_id) {
                entry.token.cancel();
            }
        }

        expired
    }

    /// Get count of active runs.
    pub async fn active_count(&self) -> usize {
        let runs = self.runs.read().await;
        runs.len()
    }

    /// List all active run IDs.
    pub async fn list_active(&self) -> Vec<String> {
        let runs = self.runs.read().await;
        runs.keys().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_register_and_abort() {
        let manager = ChatAbortManager::new();

        let entry = ChatRunEntry {
            run_id: "run-1".to_string(),
            session_key: "session-1".to_string(),
            token: CancellationToken::new(),
            started_at_ms: 1000,
            expires_at_ms: 60000,
            owner_conn_id: None,
            buffer: Arc::new(RwLock::new("partial text".to_string())),
        };

        manager.register(entry).await;
        assert!(manager.exists("run-1").await);

        let buffer = manager.abort("run-1").await;
        assert_eq!(buffer, Some("partial text".to_string()));
        assert!(!manager.exists("run-1").await);
    }

    #[tokio::test]
    async fn test_abort_session() {
        let manager = ChatAbortManager::new();

        // Register multiple runs for same session
        for i in 0..3 {
            let entry = ChatRunEntry {
                run_id: format!("run-{}", i),
                session_key: "session-1".to_string(),
                token: CancellationToken::new(),
                started_at_ms: 1000,
                expires_at_ms: 60000,
                owner_conn_id: None,
                buffer: Arc::new(RwLock::new(String::new())),
            };
            manager.register(entry).await;
        }

        // Register one run for different session
        let entry = ChatRunEntry {
            run_id: "run-other".to_string(),
            session_key: "session-2".to_string(),
            token: CancellationToken::new(),
            started_at_ms: 1000,
            expires_at_ms: 60000,
            owner_conn_id: None,
            buffer: Arc::new(RwLock::new(String::new())),
        };
        manager.register(entry).await;

        // Abort session-1
        let aborted = manager.abort_session("session-1").await;
        assert_eq!(aborted.len(), 3);

        // session-2 should still exist
        assert!(manager.exists("run-other").await);
    }

    #[tokio::test]
    async fn test_next_seq() {
        let manager = ChatAbortManager::new();

        assert_eq!(manager.next_seq("run-1").await, 1);
        assert_eq!(manager.next_seq("run-1").await, 2);
        assert_eq!(manager.next_seq("run-1").await, 3);
        assert_eq!(manager.next_seq("run-2").await, 1);
    }
}