use std::collections::BTreeSet;
use std::sync::{RwLock, RwLockReadGuard, RwLockWriteGuard};

#[derive(Debug)]
pub struct ProviderStreamGrayList {
    state: RwLock<ProviderStreamGrayState>,
}

#[derive(Debug)]
struct ProviderStreamGrayState {
    enabled: bool,
    created_by: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderStreamGraySnapshot {
    pub enabled: bool,
    pub created_by: Vec<String>,
}

impl ProviderStreamGrayList {
    pub fn new(entries: Vec<String>) -> Self {
        Self::new_with_enabled(true, entries)
    }

    pub fn new_disabled(entries: Vec<String>) -> Self {
        Self::new_with_enabled(false, entries)
    }

    fn new_with_enabled(enabled: bool, entries: Vec<String>) -> Self {
        Self {
            state: RwLock::new(ProviderStreamGrayState {
                enabled,
                created_by: normalize_entries(entries),
            }),
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.read_state().enabled
    }

    pub fn set_enabled(&self, enabled: bool) {
        self.write_state().enabled = enabled;
    }

    pub fn list(&self) -> Vec<String> {
        snapshot_from_state(&self.read_state()).created_by
    }

    pub fn replace(&self, entries: Vec<String>) {
        self.write_state().created_by = normalize_entries(entries);
    }

    pub fn snapshot(&self) -> ProviderStreamGraySnapshot {
        snapshot_from_state(&self.read_state())
    }

    pub fn update(
        &self,
        enabled: Option<bool>,
        entries: Option<Vec<String>>,
    ) -> ProviderStreamGraySnapshot {
        let mut state = self.write_state();
        if let Some(enabled) = enabled {
            state.enabled = enabled;
        }
        if let Some(entries) = entries {
            state.created_by = normalize_entries(entries);
        }
        snapshot_from_state(&state)
    }

    pub fn contains(&self, created_by: Option<&str>) -> bool {
        let state = self.read_state();
        if !state.enabled {
            // Disabled gray-list mode means full rollout for Provider 2.0 downlink.
            return true;
        }
        let Some(created_by) = created_by.map(str::trim).filter(|value| !value.is_empty()) else {
            return false;
        };
        state.created_by.contains(created_by)
    }

    fn read_state(&self) -> RwLockReadGuard<'_, ProviderStreamGrayState> {
        match self.state.read() {
            Ok(guard) => guard,
            Err(poisoned) => {
                self.state.clear_poison();
                poisoned.into_inner()
            }
        }
    }

    fn write_state(&self) -> RwLockWriteGuard<'_, ProviderStreamGrayState> {
        match self.state.write() {
            Ok(guard) => guard,
            Err(poisoned) => {
                self.state.clear_poison();
                poisoned.into_inner()
            }
        }
    }
}

impl Default for ProviderStreamGrayList {
    fn default() -> Self {
        Self::new_disabled(Vec::new())
    }
}

fn normalize_entries(entries: Vec<String>) -> BTreeSet<String> {
    entries
        .into_iter()
        .map(|entry| entry.trim().to_string())
        .filter(|entry| !entry.is_empty())
        .collect()
}

fn snapshot_from_state(state: &ProviderStreamGrayState) -> ProviderStreamGraySnapshot {
    ProviderStreamGraySnapshot {
        enabled: state.enabled,
        created_by: state.created_by.iter().cloned().collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::ProviderStreamGrayList;

    #[test]
    fn normalizes_initial_entries_and_ignores_empty_values() {
        let gray = ProviderStreamGrayList::new(vec![
            " 197262 ".to_string(),
            "".to_string(),
            "alice".to_string(),
            "197262".to_string(),
        ]);

        assert_eq!(gray.list(), vec!["197262".to_string(), "alice".to_string()]);
        assert!(gray.contains(Some("197262")));
        assert!(gray.contains(Some(" alice ")));
        assert!(!gray.contains(None));
        assert!(!gray.contains(Some("")));
        assert!(!gray.contains(Some("bob")));
    }

    #[test]
    fn replace_updates_the_set_atomically() {
        let gray = ProviderStreamGrayList::new(vec!["old".to_string()]);

        gray.replace(vec![" new ".to_string(), "old".to_string()]);

        assert_eq!(gray.list(), vec!["new".to_string(), "old".to_string()]);
        assert!(gray.contains(Some("new")));
        assert!(!gray.contains(Some("missing")));
    }

    #[test]
    fn update_can_disable_gray_mode_and_keep_entries_in_one_snapshot() {
        let gray = ProviderStreamGrayList::new(vec!["old".to_string()]);

        let snapshot = gray.update(
            Some(false),
            Some(vec![" new ".to_string(), "old".to_string()]),
        );

        assert!(!snapshot.enabled);
        assert_eq!(snapshot.created_by, vec!["new".to_string(), "old".to_string()]);
        assert!(!gray.is_enabled());
        assert_eq!(gray.list(), vec!["new".to_string(), "old".to_string()]);
        assert!(gray.contains(Some("new")));
        assert!(gray.contains(Some("missing")));
        assert!(gray.contains(None));
    }

    #[test]
    fn new_list_enables_gray_mode_by_default() {
        let gray = ProviderStreamGrayList::new(vec!["alice".to_string()]);

        assert!(gray.is_enabled());
        assert!(gray.contains(Some("alice")));
        assert!(!gray.contains(Some("bob")));
    }

    #[test]
    fn disabled_list_preserves_entries_but_allows_all_until_reenabled() {
        let gray = ProviderStreamGrayList::new_disabled(vec!["alice".to_string()]);

        assert!(!gray.is_enabled());
        assert_eq!(gray.list(), vec!["alice".to_string()]);
        assert!(gray.contains(Some("alice")));
        assert!(gray.contains(Some("bob")));
        assert!(gray.contains(None));

        gray.set_enabled(true);

        assert!(gray.is_enabled());
        assert!(gray.contains(Some("alice")));
        assert!(!gray.contains(Some("bob")));
        assert!(!gray.contains(None));
    }

    #[test]
    fn poisoned_lock_does_not_panic_and_can_be_replaced() {
        let gray = ProviderStreamGrayList::new(vec!["old".to_string()]);
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = gray.state.write().expect("lock before poison");
            panic!("poison provider stream gray list lock");
        }));

        assert!(gray.contains(Some("old")));

        gray.replace(vec!["new".to_string()]);

        assert_eq!(gray.list(), vec!["new".to_string()]);
        assert!(!gray.contains(Some("old")));
        assert!(gray.contains(Some("new")));
    }
}
