//! Shared SQL lifecycle contract scenarios.
use super::*;

// ---------------------------------------------------------------------------
// Review Finding #8: the pending closure identity is
// read INSIDE the transaction. The pre-read is
// parameter preparation only; the deterministic
// one-shot transaction hook below mutates the
// pendings between the pre-read and the lock so the
// count-stable / identity-drift interleave ("cancel
// Bob → create Carol") runs deterministically. The
// first attempt must roll back on the per-row
// identity guards and the retry must close the row
// the transaction ACTUALLY saw — the closure audit
// never names a row the transaction did not see.
// ---------------------------------------------------------------------------

/// Arm the one-shot hook: at the next transaction's
/// entrance, cancel `cancelled`'s pending and create
/// `created`'s pending (the same command interleave
/// a racing owner/target pair produces).
fn arm_cancel_and_new_pending_hook(
    fixture: &Fixture,
    bot_id: &str,
    cancelled: &str,
    created: &str,
) {
    // +2 days in the DB clock domain of each flavor.
    let expires_at = match fixture.flavor {
        DbSqlFlavor::Mysql => {
            "DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 2 DAY)"
        }
        DbSqlFlavor::Sqlite => "datetime('now', '+2 days')",
    };
    let db = fixture.db.clone();
    let bot_id = bot_id.to_string();
    let cancelled = cancelled.to_string();
    let created = created.to_string();
    fixture.db.arm_transaction_hook(move || {
        let db = db.clone();
        let bot_id = bot_id.clone();
        let cancelled = cancelled.clone();
        let created = created.clone();
        async move {
            db.inner
                .execute(DbStatement::with_params(
                    format!(
                        "UPDATE bot_ownership_transfers \
                         SET status = 'cancelled', \
                             decided_at = CURRENT_TIMESTAMP, \
                             gmt_modified = CURRENT_TIMESTAMP \
                         WHERE env = ? AND bot_id = ? \
                           AND target_owner_actor_id = ? \
                           AND status = 'pending'"
                    )
                    .as_str(),
                    vec![
                        DbValue::from(resolve_env().as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(cancelled.as_str()),
                    ],
                ))
                .await
                .expect("hook: cancel the pre-read pending");
            db.inner
                .execute(DbStatement::with_params(
                    format!(
                        "INSERT INTO bot_ownership_transfers \
                         (env, bot_id, target_owner_actor_id, \
                          from_owner_actor_id, \
                          expected_ownership_version, status, \
                          requested_at, expires_at, \
                          bot_name_snapshot) \
                         VALUES (?, ?, ?, '11111111', 1, 'pending', \
                                 CURRENT_TIMESTAMP, {expires_at}, \
                                 'snapshot')"
                    )
                    .as_str(),
                    vec![
                        DbValue::from(resolve_env().as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(created.as_str()),
                    ],
                ))
                .await
                .expect("hook: create the other pending");
        }
    });
}

async fn transfer_pending_row(
    fixture: &Fixture,
    bot_id: &str,
    target: &str,
) -> Vec<(String, Option<String>, Option<String>, Option<String>)> {
    fixture
        .db
        .query(DbStatement::with_params(
            format!(
                "SELECT status, decided_by_kind, decided_by_id, reason \
                 FROM bot_ownership_transfers \
                 WHERE bot_id = ? AND target_owner_actor_id = ?"
            )
            .as_str(),
            vec![
                DbValue::from(bot_id),
                DbValue::from(target),
            ],
        ))
        .await
        .expect("transfer rows")
        .into_iter()
        .map(|row| {
            (
                bcs_db_api::db_get_column::<String>(&row, "status")
                    .unwrap(),
                row.get_string("decided_by_kind")
                    .expect("decided_by_kind"),
                row.get_string("decided_by_id")
                    .expect("decided_by_id"),
                row.get_string("reason").expect("reason"),
            )
        })
        .collect()
}

async fn closure_audit_subjects(
    fixture: &Fixture,
    bot_id: &str,
) -> Vec<String> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT subject_id FROM bot_authority_changes \
             WHERE bot_id = ? AND action = 'transfer_invalidate' \
             ORDER BY sequence",
            vec![DbValue::from(bot_id)],
        ))
        .await
        .expect("closure audits")
        .iter()
        .map(|row| {
            bcs_db_api::db_get_column::<String>(
                row,
                "subject_id",
            )
            .unwrap()
        })
        .collect()
}

/// The reviewer's exact interleave (ordinary
/// retirement): "cancel Bob → create Carol" between
/// the pre-read and the transaction keeps the
/// pending COUNT at 1 while the identity changes.
/// The count-only guard of the old shape passed —
/// and the retirement audited Bob for Carol's
/// closure. Now the per-row in-transaction identity
/// read rolls attempt 1 back; the retry closes
/// CAROL's row and the closure audit names CAROL.
pub async fn retire_closes_the_pending_identity_seen_in_tx(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendrace".to_string(),
            capabilities(),
            "11111111",
            "race-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");
    seed_pending_transfer(&fixture, "bot_pendrace", "human_bob")
        .await;
    arm_cancel_and_new_pending_hook(
        &fixture,
        "bot_pendrace",
        "human_bob",
        "human_carol",
    );

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendrace",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired after the identity drift");
    assert!(changed);

    // Bob's row: untouched by the retirement (the
    // racing command already closed it — NO
    // double-close).
    let bob = transfer_pending_row(&fixture, "bot_pendrace", "human_bob").await;
    assert_eq!(bob.len(), 1, "no double-close inserted rows");
    assert_eq!(bob[0].0, "cancelled", "Bob keeps his own terminal state");
    // Carol's row: the one the transaction closed, with
    // the fixed system marker and reason.
    let carol =
        transfer_pending_row(&fixture, "bot_pendrace", "human_carol").await;
    assert_eq!(carol.len(), 1);
    assert_eq!(carol[0].0, "invalidated");
    assert_eq!(carol[0].1.as_deref(), Some("system"));
    assert_eq!(
        carol[0].2.as_deref(),
        Some("system_transfer_close")
    );
    assert_eq!(carol[0].3.as_deref(), Some("bot_retired"));
    // The closure audit names CAROL — the identity the
    // transaction actually saw — exactly once, never Bob.
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendrace").await,
        vec!["human_carol".to_string()],
        "the closure audit must name the in-tx identity"
    );
}

/// The same interleave through the Provider
/// retirement composite (the shared closure helper).
pub async fn provider_retire_closes_the_pending_identity_seen_in_tx(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref(
                "bot_pendprov",
                BotConnectionMode::Plugin,
                "prov-ref",
            ),
            capabilities(),
            "11111111",
            "plugin-token",
            mutation("11111111"),
        )
        .await
        .expect("registered provider bot");
    seed_pending_transfer(&fixture, "bot_pendprov", "human_bob")
        .await;
    arm_cancel_and_new_pending_hook(
        &fixture,
        "bot_pendprov",
        "human_bob",
        "human_carol",
    );

    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pendprov".into(),
            provider_bot_ref: "prov-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await
        .expect("retired after the identity drift");
    assert!(retirement.changed);

    let bob =
        transfer_pending_row(&fixture, "bot_pendprov", "human_bob").await;
    assert_eq!(bob[0].0, "cancelled", "no double-close");
    let carol =
        transfer_pending_row(&fixture, "bot_pendprov", "human_carol").await;
    assert_eq!(carol[0].0, "invalidated");
    assert_eq!(
        carol[0].2.as_deref(),
        Some("system_transfer_close")
    );
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendprov").await,
        vec!["human_carol".to_string()],
        "the closure audit must name the in-tx identity"
    );
}

/// Count drift (the only pending vanishes between
/// the pre-read and the transaction): the count
/// guard rolls attempt 1 back, the retry sees an
/// empty pending set and commits WITHOUT writing
/// any closure or closure audit.
pub async fn retire_retries_when_the_pending_vanishes_between_preread_and_tx(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendvanish".to_string(),
            capabilities(),
            "11111111",
            "vanish-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");
    seed_pending_transfer(
        &fixture,
        "bot_pendvanish",
        "human_bob",
    )
    .await;
    let db = fixture.db.clone();
    fixture
        .db
        .arm_transaction_hook(move || {
            let db = db.clone();
            async move {
                db.inner
                    .execute(DbStatement::with_params(
                        "UPDATE bot_ownership_transfers \
                         SET status = 'cancelled', \
                             decided_at = CURRENT_TIMESTAMP \
                         WHERE bot_id = 'bot_pendvanish' \
                           AND target_owner_actor_id = 'human_bob' \
                           AND status = 'pending'",
                        vec![],
                    ))
                    .await
                    .expect("hook: cancel the pending");
            }
        });

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendvanish",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired after the count drift");
    assert!(changed);
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendvanish").await,
        Vec::<String>::new(),
        "a vanished pending must not produce a closure audit"
    );
    assert_eq!(
        audit_actions(&fixture, "bot_pendvanish").await,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
        ],
        "the register + retire audits, no closure"
    );
}

/// Zero pending: the retirement writes NO closure
/// audit ("no audit before actual change") and no
/// transfer rows.
pub async fn retire_with_no_pendings_writes_no_closure_audit(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendzero".to_string(),
            capabilities(),
            "11111111",
            "zero-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendzero",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired");
    assert!(changed);
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendzero").await,
        Vec::<String>::new(),
        "no closure audit without pendings"
    );
    assert_eq!(
        audit_actions(&fixture, "bot_pendzero").await,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
        ],
    );
}
