/// Post-commit notification for the discovery catalog. Implementations enqueue
/// identities and read authoritative state when publishing; callers do no IO.
pub trait CatalogChangePort: Send + Sync {
    fn agent_changed(&self, id: &str);
    fn group_changed(&self, id: &str);
}
