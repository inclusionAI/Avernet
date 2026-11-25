use super::*;

#[test]
fn corrupt_and_overflowing_scalars_are_never_accepted() {
    for value in [DbValue::Null, DbValue::I64(-1), DbValue::U64(u64::MAX), DbValue::F64(1.0), DbValue::String("1".into())] {
        let row = DbRow::new([("n".into(), value)].into());
        assert_eq!(number(&row, "n"), Err(AgentInvokeError::InvalidState));
    }
    assert_eq!(number(&DbRow::empty(), "n"), Err(AgentInvokeError::InvalidState));
    for value in [DbValue::I64(1), DbValue::U64(1)] {
        let row = DbRow::new([("n".into(), value)].into());
        assert_eq!(number(&row, "n"), Ok(1)); assert_eq!(positive(&row, "n").unwrap().get(), 1);
    }
    let row = DbRow::new([("n".into(), DbValue::I64(0))].into());
    assert_eq!(positive(&row, "n"), Err(AgentInvokeError::InvalidState));
    assert_eq!(advance(0).unwrap().get(), 1);
    for v in [i64::MAX as u64, u64::MAX] { assert_eq!(advance(v), Err(AgentInvokeError::InvalidState)); }
}

#[test]
fn booleans_and_byte_strings_are_decoded_strictly() {
    for (value, expected) in [(DbValue::Bool(true), true), (DbValue::Bool(false), false), (DbValue::I64(1), true), (DbValue::I64(0), false)] {
        assert_eq!(boolean(&DbRow::new([("n".into(), value)].into()), "n"), Ok(expected));
    }
    assert_eq!(boolean(&DbRow::new([("n".into(), DbValue::I64(2))].into()), "n"), Err(AgentInvokeError::InvalidState));
    assert_eq!(string(&DbRow::empty(), "n"), Err(AgentInvokeError::InvalidState));
    assert_eq!(string(&DbRow::new([("n".into(), DbValue::Bytes(vec![255]))].into()), "n"), Err(AgentInvokeError::InvalidState));
    assert_eq!(string(&DbRow::new([("n".into(), DbValue::Bytes(b"Bot ".to_vec()))].into()), "n"), Ok("Bot ".into()));
}

#[test]
fn errors_are_closed_and_unknown_commits_are_never_classified_retryable() {
    for error in [DbError::InvalidInput("private".into()), DbError::Conversion("private".into()),
        DbError::Unsupported("private".into()), DbError::Timeout, DbError::Backend("private".into())] {
        assert_eq!(write_error(DbTransactionFailure::not_committed(error)),AgentInvokeError::Unavailable);
    }
    for error in [DbError::ConditionFailed {expected:1,actual:0}, DbError::Backend("UNIQUE constraint failed".into())] {
        assert_eq!(write_error(DbTransactionFailure::not_committed(error)),AgentInvokeError::Conflict);
    }
    for error in [DbError::Backend("UNIQUE constraint failed".into()), DbError::Timeout,
        DbError::ConditionFailed {expected:1,actual:0}, DbError::Backend("private".into())] {
        assert_eq!(write_error(DbTransactionFailure::unknown(error)),AgentInvokeError::CommitUnknown);
    }
}

#[test]
fn mysql_identity_residuals_and_database_clock_are_explicit() {
    let s = DbAgentInvokeStore::new(Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap()), DbSqlFlavor::Mysql,
        AgentInvokeScope { namespace: "test".into(), env: "dev".into() },
        AgentInvokeLimits { max_out_degree: std::num::NonZeroU32::new(1).unwrap(), max_in_degree: std::num::NonZeroU32::new(1).unwrap(),
            max_pending_out: std::num::NonZeroU32::new(1).unwrap(), max_pending_in: std::num::NonZeroU32::new(1).unwrap() }, NonZeroU64::new(1).unwrap()).unwrap();
    assert_eq!(s.exact("bot_id"), "CAST(bot_id AS BINARY)");
    assert_eq!(s.now_ms(), "CAST(UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3)) * 1000 AS UNSIGNED)");
    // This asserts SQL spelling only; no SQLite driver is used as MySQL proof.
}
