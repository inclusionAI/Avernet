//! Per-instance Run lifecycle serialization; never a distributed lease.
use std::{collections::{HashMap, VecDeque}, sync::{Arc, Weak}};
use tokio::sync::{Mutex, OwnedMutexGuard};

#[derive(Debug, Default)]
struct Directory { by_run: HashMap<String, Weak<Mutex<()>>>, sweep: VecDeque<String> }

#[derive(Debug, Default)]
pub(crate) struct RunLifecycleLocks { directory: Mutex<Directory> }

impl RunLifecycleLocks {
    pub(crate) async fn acquire(&self, run: &str) -> OwnedMutexGuard<()> {
        let lock = {
            let mut directory = self.directory.lock().await;
            // Rotate a bounded cleanup batch; live entries cannot hide expired
            // entries forever and no sweep scans an unbounded active set.
            for _ in 0..directory.sweep.len().min(32) {
                let Some(key) = directory.sweep.pop_front() else { break; };
                if directory.by_run.get(&key).is_some_and(|value|value.strong_count() > 0) {
                    directory.sweep.push_back(key);
                } else { directory.by_run.remove(&key); }
            }
            match directory.by_run.get(run).and_then(Weak::upgrade) {
                Some(lock) => lock,
                None => {
                    let lock = Arc::new(Mutex::new(()));
                    if !directory.by_run.contains_key(run) { directory.sweep.push_back(run.to_string()); }
                    directory.by_run.insert(run.to_string(), Arc::downgrade(&lock));
                    lock
                }
            }
        };
        lock.lock_owned().await
    }
}
