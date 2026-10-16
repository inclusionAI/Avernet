use bcs_domain::{GroupContextEntry, GroupContextEntryMeta, GroupContextPermission};

pub fn actor_can_read(entry: &GroupContextEntry, actor_id: &str) -> bool {
    entry.visible_to.is_visible_to(actor_id)
}

pub fn actor_can_write(entry: &GroupContextEntry, actor_id: &str) -> bool {
    entry.collect_from.contains_user(actor_id)
}

pub fn permission_for(entry: &GroupContextEntryMeta, actor_id: &str) -> Option<GroupContextPermission> {
    GroupContextPermission::from_access(
        entry.visible_to.is_visible_to(actor_id),
        entry.collect_from.contains_user(actor_id),
    )
}
