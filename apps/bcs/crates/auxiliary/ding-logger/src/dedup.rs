use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

const TTL: Duration = Duration::from_secs(300);
const CAPACITY: usize = 1024;

#[derive(Debug, Clone)]
struct Entry {
    created_at: Instant,
}

#[derive(Debug, Clone)]
pub struct DedupStore {
    inner: Arc<Mutex<HashMap<String, Entry>>>,
}

impl DedupStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(HashMap::with_capacity(CAPACITY))),
        }
    }

    /// Returns true if duplicate (already seen within TTL), false if new.
    /// Marks the message as seen on first call.
    pub fn is_duplicate(&self, message_id: &str) -> bool {
        let now = Instant::now();

        let mut map = self.inner.lock().expect("dedup lock poisoned");

        // Evict expired entries when at capacity
        if map.len() >= CAPACITY {
            map.retain(|_, e| now.duration_since(e.created_at) < TTL);
        }

        if let Some(entry) = map.get(message_id) {
            if now.duration_since(entry.created_at) < TTL {
                return true;
            }
        }

        map.insert(message_id.to_string(), Entry { created_at: now });
        false
    }
}

impl Default for DedupStore {
    fn default() -> Self {
        Self::new()
    }
}
