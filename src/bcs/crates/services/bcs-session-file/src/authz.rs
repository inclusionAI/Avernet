//! Pure authz helpers — no IO, no async. Kept in a dedicated module so the
//! service implementation can unit-test the predicate in isolation, and so the
//! service crate has no `group_repo` dependency (mutate-authz inputs are fed
//! from the HTTP layer).

use bcs_domain::ActorRef;

/// Object-key derivation: `session-files/{env}/{session_id}/{file_id}/{file_name}`.
///
/// `env` is taken from [`crate::SessionFileServiceConfig`] `env` field (set by
/// bootstrap to match the repo's `env` column — see `MySqlSessionFileStore`).
/// Keeping the env segment in the key mirrors the per-row DB `env` column so
/// prod/gray/pre/dev objects remain isolated in the storage backend.
///
/// `file_name` is interpolated as a raw path component, so callers MUST first
/// reject unsafe names with [`validate_file_name`]. That guard is what keeps
/// the derived key — and therefore the local backend's `data_dir.join(key)` —
/// inside the session-files root. See [`validate_file_name`] for the contract.
pub fn derive_key(env: &str, session_id: &str, file_id: &str, file_name: &str) -> String {
    format!("session-files/{env}/{session_id}/{file_id}/{file_name}")
}

/// Validate a user-supplied `file_name` before it becomes a storage-key path
/// component ([`derive_key`]).
///
/// File names are opaque metadata (preserved verbatim in the DB row and the
/// download response), but they MUST NOT carry path metacharacters: a name
/// containing separators (`/`, `\`), NUL, or the `.` / `..` segments could
/// otherwise make the derived key resolve outside the session-files root when
/// the local backend joins it under `data_dir` (path traversal). Any safe name
/// passes — including non-ASCII / Chinese names — so this is a safety check,
/// not a character whitelist.
///
/// Returns `Ok(())` for a safe name, or `Err` holding a short reason. Called
/// by [`crate::SessionFileServiceImpl::prepare_upload`] at the use-case
/// boundary so the policy is enforced uniformly for every backend.
pub fn validate_file_name(name: &str) -> Result<(), &'static str> {
    if name.is_empty() {
        return Err("file_name must not be empty");
    }
    if name == "." || name == ".." {
        return Err("file_name must not be a path segment (. or ..)");
    }
    if name.contains('/') || name.contains('\\') || name.contains('\0') {
        return Err("file_name must not contain path separators or NUL");
    }
    Ok(())
}

/// Test whether `caller` may mutate (delete / share) a file owned by `owner`.
///
/// Mutate-authz lives entirely in the service layer; the inputs the predicate
/// needs (`caller_identities`, `session_creator`, `driver_bot`) are *resolved*
/// by the HTTP layer and passed via [`bcs_service_api::application::session_files::DeleteFileCommand`]
/// / [`bcs_service_api::application::session_files::ShareMintCommand`].
///
/// - `caller_identities = [caller.actor_id] + owned bot UUIDs` (HTTP `caller_identities()`).
/// - `session_creator` = `session.created_by` (resolved by HTTP via `SessionRepoPort`).
/// - `driver_bot` = `group.driver_bot` (resolved by HTTP via `GroupRepoPort`).
///
/// Returns `true` if any of the caller's identities matches `owner.actor_id`,
/// `session_creator`, or `driver_bot`. Pure synchronous function — no registry
/// lookups — so the service crate avoids the `group_repo` dependency entirely.
pub fn can_mutate(
    caller_identities: &[String],
    owner: &ActorRef,
    session_creator: Option<&str>,
    driver_bot: Option<&str>,
) -> bool {
    if caller_identities.iter().any(|id| id == &owner.actor_id) {
        return true;
    }
    if let Some(creator) = session_creator {
        if caller_identities.iter().any(|id| id == creator) {
            return true;
        }
    }
    if let Some(driver) = driver_bot {
        if caller_identities.iter().any(|id| id == driver) {
            return true;
        }
    }
    false
}

/// Test whether `caller` may share a file in the session. Sharing is gated on
/// session membership only — not on file ownership: the caller is a member when
/// any of their identities (`caller.actor_id` plus bots they own, pre-resolved
/// by the HTTP layer as `caller_identities`) appears among the session's own
/// `participants`. Pure set intersection — no IO — mirroring `can_mutate`'s
/// contract that the service crate stays free of group/bot repo dependencies.
pub fn can_share(
    caller_identities: &[String],
    session_participants: &[String],
) -> bool {
    caller_identities
        .iter()
        .any(|id| session_participants.iter().any(|part| part == id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ActorRef};

    fn actor(id: &str) -> ActorRef {
        ActorRef { actor_kind: ActorKind::Human, actor_id: id.into() }
    }

    #[test]
    fn owner_match_allows_mutate() {
        assert!(can_mutate(&["u1".into()], &actor("u1"), None, None));
    }

    #[test]
    fn creator_match_allows_mutate() {
        assert!(can_mutate(&["u1".into()], &actor("u2"), Some("u1"), None));
    }

    #[test]
    fn driver_bot_match_allows_mutate() {
        assert!(can_mutate(&["bot-x".into()], &actor("u2"), None, Some("bot-x")));
    }

    #[test]
    fn no_match_denies() {
        assert!(!can_mutate(&["u9".into()], &actor("u1"), Some("u2"), Some("bot-y")));
    }

    #[test]
    fn empty_identities_denies() {
        assert!(!can_mutate(&[], &actor("u1"), Some("u2"), Some("bot-x")));
    }

    #[test]
    fn multiple_identities_one_match_allows() {
        let ids = vec!["u1".to_string(), "bot-a".into(), "u3".into()];
        assert!(can_mutate(&ids, &actor("u3"), None, None));
    }

    // ---- can_share: membership-gated sharing ------------------------------

    #[test]
    fn share_member_allows() {
        assert!(can_share(&["u1".into()], &["u1".into(), "u2".into()]));
    }

    #[test]
    fn share_member_via_owned_bot_allows() {
        let ids = vec!["human_h".to_string(), "bot_a".into()];
        assert!(can_share(&ids, &["bot_a".into()]));
    }

    #[test]
    fn share_non_member_denies() {
        assert!(!can_share(&["u9".into()], &["u1".into(), "u2".into()]));
    }

    #[test]
    fn share_empty_identities_denies() {
        assert!(!can_share(&[], &["u1".into()]));
    }

    // ---- validate_file_name: path-traversal guard ---------------------------

    #[test]
    fn safe_names_pass() {
        assert!(validate_file_name("x.txt").is_ok());
        assert!(validate_file_name("report (final).pdf").is_ok());
        // Non-ASCII / Chinese names are safe — this is not a whitelist.
        assert!(validate_file_name("自由.txt").is_ok());
        assert!(validate_file_name("a b.tar.gz").is_ok());
    }

    #[test]
    fn empty_name_rejected() {
        assert!(validate_file_name("").is_err());
    }

    #[test]
    fn path_separators_rejected() {
        assert!(validate_file_name("a/b").is_err());
        assert!(validate_file_name("a\\b").is_err());
        assert!(validate_file_name("/etc/passwd").is_err());
        // Whole-name traversal segments resolve outside the root.
        assert!(validate_file_name(".").is_err());
        assert!(validate_file_name("..").is_err());
    }

    #[test]
    fn traversal_relative_name_rejected() {
        // The classic attack: a file_name that climbs out of the key dir.
        assert!(validate_file_name("../../etc/passwd").is_err());
        assert!(validate_file_name("..\\..\\windows").is_err());
    }

    #[test]
    fn nul_byte_rejected() {
        assert!(validate_file_name("evil\0.txt").is_err());
    }
}