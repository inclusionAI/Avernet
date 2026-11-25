use super::{
    Cli, ChannelCommands, Commands, Parser, build_channel_bind_payload, json,
    redact_channel_bind_debug_payload,
};

#[test]
fn test_channel_bind_command_parses_defaults() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "channel",
        "bind",
        "--account",
        "robot_1",
        "--target-id",
        "group_1",
        "--robot-code",
        "robot_1",
        "--client-id",
        "client_id",
        "--client-secret",
        "secret",
    ])
    .unwrap();

    match cli.command {
        Commands::Channel {
            command:
                ChannelCommands::Bind {
                    account,
                    target_kind,
                    target_id,
                    group_chat_scope,
                    visibility,
                    env,
                    send_mode,
                    message_type,
                    ..
                },
            ..
        } => {
            assert_eq!(account, "robot_1");
            assert_eq!(target_kind, "group");
            assert_eq!(target_id, "group_1");
            assert_eq!(group_chat_scope, None);
            assert_eq!(visibility, "lead_only");
            assert_eq!(env, "dev");
            assert_eq!(send_mode, "normal");
            assert_eq!(message_type, "markdown");
        }
        _ => panic!("expected channel bind command"),
    }
}

#[test]
fn test_channel_bind_payload_builds_group_dingtalk_normal() {
    let command = ChannelCommands::Bind {
        account: "robot_1".to_string(),
        target_kind: "group".to_string(),
        target_id: "group_1".to_string(),
        group_chat_scope: Some("conversation_shared".to_string()),
        visibility: "full_transcript".to_string(),
        env: "pre".to_string(),
        robot_code: "robot_1".to_string(),
        client_id: "client_id".to_string(),
        client_secret: "secret".to_string(),
        send_mode: "normal".to_string(),
        card_template_id: None,
        message_type: "text".to_string(),
    };

    let payload = build_channel_bind_payload(&command).unwrap();

    assert_eq!(
        payload,
        json!({
            "channel_type": "dingtalk",
            "account_ref": "robot_1",
            "target": { "group": { "group_id": "group_1" } },
            "group_chat_scope": "conversation_shared",
            "outbound_visibility": "full_transcript",
            "env": "pre",
            "config": {
                "channel_type": "dingtalk",
                "robot_code": "robot_1",
                "client_id": "client_id",
                "client_secret": "secret",
                "send_mode": {
                    "mode": "normal",
                    "message_type": "text"
                }
            }
        })
    );
}

#[test]
fn test_channel_bind_payload_builds_bot_streaming_card() {
    let command = ChannelCommands::Bind {
        account: "robot_1".to_string(),
        target_kind: "bot".to_string(),
        target_id: "bot_1".to_string(),
        group_chat_scope: Some("per_sender".to_string()),
        visibility: "lead_only".to_string(),
        env: "dev".to_string(),
        robot_code: "robot_1".to_string(),
        client_id: "client_id".to_string(),
        client_secret: "secret".to_string(),
        send_mode: "streaming_card".to_string(),
        card_template_id: Some("card_tpl".to_string()),
        message_type: "markdown".to_string(),
    };

    let payload = build_channel_bind_payload(&command).unwrap();

    assert_eq!(payload["target"], json!({ "bot": { "bot_id": "bot_1" } }));
    assert_eq!(payload["group_chat_scope"], "per_sender");
    assert_eq!(
        payload["config"]["send_mode"],
        json!({
            "mode": "streaming_card",
            "card_template_id": "card_tpl",
            "fallback_message_type": "markdown"
        })
    );
}

#[test]
fn test_channel_bind_payload_requires_card_template_for_streaming_card() {
    let command = ChannelCommands::Bind {
        account: "robot_1".to_string(),
        target_kind: "group".to_string(),
        target_id: "group_1".to_string(),
        group_chat_scope: None,
        visibility: "lead_only".to_string(),
        env: "dev".to_string(),
        robot_code: "robot_1".to_string(),
        client_id: "client_id".to_string(),
        client_secret: "secret".to_string(),
        send_mode: "streaming_card".to_string(),
        card_template_id: None,
        message_type: "markdown".to_string(),
    };

    let err = build_channel_bind_payload(&command).unwrap_err();
    assert!(err.to_string().contains("card-template-id"));
}

#[test]
fn test_channel_bind_debug_payload_redacts_client_secret() {
    let command = ChannelCommands::Bind {
        account: "robot_1".to_string(),
        target_kind: "group".to_string(),
        target_id: "group_1".to_string(),
        group_chat_scope: None,
        visibility: "lead_only".to_string(),
        env: "dev".to_string(),
        robot_code: "robot_1".to_string(),
        client_id: "client_id".to_string(),
        client_secret: "secret".to_string(),
        send_mode: "normal".to_string(),
        card_template_id: None,
        message_type: "markdown".to_string(),
    };
    let payload = build_channel_bind_payload(&command).unwrap();

    let redacted = redact_channel_bind_debug_payload(&payload);

    assert_eq!(payload["config"]["client_secret"], "secret");
    assert_eq!(redacted["config"]["client_secret"], "<redacted>");
}

#[test]
fn test_channel_list_command_parses() {
    let cli = Cli::try_parse_from(["bcs-cli", "channel", "list"]).unwrap();

    match cli.command {
        Commands::Channel {
            command: ChannelCommands::List,
            ..
        } => {}
        _ => panic!("expected channel list command"),
    }
}

#[test]
fn test_channel_conversation_id_command_parses_session() {
    let cli = Cli::try_parse_from([
        "bcs-cli",
        "channel",
        "conversation-id",
        "--session",
        "group_1:session_1",
    ])
    .unwrap();

    match cli.command {
        Commands::Channel {
            command: ChannelCommands::ConversationId { session },
            ..
        } => {
            assert_eq!(session, "group_1:session_1");
        }
        _ => panic!("expected channel conversation-id command"),
    }
}

#[test]
fn test_channel_unbind_command_parses_id() {
    let cli =
        Cli::try_parse_from(["bcs-cli", "channel", "unbind", "--id", "binding_1"]).unwrap();

    match cli.command {
        Commands::Channel {
            command: ChannelCommands::Unbind { id },
            ..
        } => {
            assert_eq!(id, "binding_1");
        }
        _ => panic!("expected channel unbind command"),
    }
}
