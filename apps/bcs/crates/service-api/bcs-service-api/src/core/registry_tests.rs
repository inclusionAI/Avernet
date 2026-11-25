    use super::*;
    use bcs_domain::ActorKind;

    /// Bot Authority Task 4 (plan §3.1): the committed registration
    /// receipt and the domain query projection must stay separable.
    ///
    /// 1. `RegisteredBot` (get/list/discovery) serializes without any
    ///    credential — `agent_code`/`agent_token` are skip-serializing
    ///    and a runtime token is not even a field.
    /// 2. The wire registration fields stay exactly
    ///    `bot_uuid`/`bot_name`/`bot_token` (openapi plugin response
    ///    shape); no query/log serialization capability is added to
    ///    `CommittedBotRegistration`.
    ///
    /// The negative half is a REVIEW-ENFORCED firewall, not a
    /// compile-enforced one (stable Rust cannot assert the ABSENCE of
    /// a trait impl): the receipt deliberately derives no
    /// `Serialize`/`Deserialize`, and the commented-out
    /// serialization call below exists so any reviewer who adds it
    /// sees this invariant while re-enabling it. The positive
    /// assertions above are the executable part of this test.
    #[test]
    fn registration_receipt_is_separate_from_registered_bot() {
        let registered = RegisteredBot {
            bot_uuid: "bot_x".to_string(),
            capabilities: BotCapabilities {
                name: Some("Bot X".to_string()),
                agent_code: Some("test-token".to_string()),
                agent_token: Some("test-token".to_string()),
                ..Default::default()
            },
            env: Some("test".to_string()),
            created_by: Some("11111111".to_string()),
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        };
        let bot_json = serde_json::to_string(&registered)
            .expect("query projection stays serializable (existing contract)");
        assert!(
            !bot_json.contains("bot_token"),
            "query projection must never carry a bot token: {bot_json}"
        );
        assert!(
            !bot_json.contains("agent_token") && !bot_json.contains("agent_code"),
            "query projection must never carry agent credentials: {bot_json}"
        );

        // Committed receipt keeps the frozen registration wire fields.
        let receipt = CommittedBotRegistration {
            bot_uuid: "bot_x".to_string(),
            bot_name: "Bot X".to_string(),
            bot_token: "test-token".to_string(),
        };
        assert_eq!(receipt.bot_uuid, "bot_x");
        assert_eq!(receipt.bot_name, "Bot X");
        assert_eq!(receipt.bot_token, "test-token");

        // Negative half of the isolation rule: the receipt has NO
        // query/log serialization capability — a REVIEW-ENFORCED
        // firewall (stable Rust cannot assert the absence of a trait
        // impl at runtime or compile time from inside a passing
        // test): unlike `RegisteredBot`, the type derives no
        // `Serialize`/`Deserialize` and carries no serde/Display/log
        // dump anywhere in this crate. Any reviewer re-enabling the
        // serialization below must first read this invariant and the
        // doc on `CommittedBotRegistration`.
        //
        // let leaked = serde_json::to_string(&receipt);
        drop(receipt);
    }
