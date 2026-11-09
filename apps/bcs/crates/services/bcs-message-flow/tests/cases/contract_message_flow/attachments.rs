use super::*;

// ==== Workbench realtime attachment echo ====

fn image_attachment() -> bcs_domain::Attachment {
    bcs_domain::Attachment {
        attachment_id: "file_1".to_string(),
        attachment_type: bcs_domain::AttachmentType::Image,
        file_name: "a.png".to_string(),
        mime_type: Some("image/png".to_string()),
        size: Some(4),
        sha256: Some("abcd".to_string()),
        url: "https://bcs.test/sessions/shared-file/content?token=fromsender".to_string(),
        expires_at: Some(1_786_346_756_000),
    }
}

fn file_attachment() -> bcs_domain::Attachment {
    bcs_domain::Attachment {
        attachment_id: "file_2".to_string(),
        attachment_type: bcs_domain::AttachmentType::File,
        file_name: "design.pdf".to_string(),
        mime_type: Some("application/pdf".to_string()),
        size: None,
        sha256: None,
        url: "https://download.example.com/temporary".to_string(),
        expires_at: None,
    }
}

fn human_web_send(
    message: &str,
    attachments: Option<Vec<bcs_domain::Attachment>>,
) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some("session-1".to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: message.to_string(),
        mentions: vec![],
        attachments,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

#[tokio::test]
async fn web_send_event_echoes_attachments_verbatim() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(human_web_send("看图", Some(vec![image_attachment()])))
        .await
        .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let event: serde_json::Value = serde_json::from_str(&events[0]).unwrap();
    let atts = &event["payload"]["message"]["attachments"];
    assert!(atts.is_array(), "event: {event}");
    assert_eq!(atts[0]["attachment_id"], "file_1");
    assert_eq!(atts[0]["type"], "image");
    assert_eq!(atts[0]["file_name"], "a.png");
    assert_eq!(atts[0]["mime_type"], "image/png");
    assert_eq!(atts[0]["size"], 4);
    assert_eq!(atts[0]["sha256"], "abcd");
    // url and expires_at are echoed verbatim (expires_at in milliseconds).
    assert_eq!(
        atts[0]["url"],
        "https://bcs.test/sessions/shared-file/content?token=fromsender"
    );
    assert_eq!(atts[0]["expires_at"], 1_786_346_756_000_u64);
    // Existing content shape is untouched.
    assert_eq!(event["payload"]["message"]["content"][0]["text"], "看图");
}

#[tokio::test]
async fn temporary_file_attachment_is_sent_only_to_active_chat_send() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(human_web_send("", Some(vec![file_attachment()])))
        .await
        .unwrap();

    let frames = support.bot_delivery.frames().await;
    let send = frames
        .iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(request) if request.method == "chat.send" => request.params.as_ref(),
            _ => None,
        })
        .expect("chat.send params");
    let inject = frames
        .iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(request) if request.method == "chat.inject" => {
                request.params.as_ref()
            }
            _ => None,
        })
        .expect("chat.inject params");
    assert_eq!(send["attachments"][0]["type"], "file");
    assert!(inject.get("attachments").is_none());

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let event: serde_json::Value = serde_json::from_str(&events[0]).unwrap();
    let attachment = &event["payload"]["message"]["attachments"][0];
    assert_eq!(attachment["attachment_id"], "file_2");
    assert_eq!(attachment["type"], "file");
    assert!(attachment.get("url").is_none(), "event: {event}");
    assert!(attachment.get("expires_at").is_none(), "event: {event}");
    assert!(!events[0].contains("download.example.com"));
}

#[tokio::test]
async fn web_send_event_without_attachments_has_no_attachments_key() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(human_web_send("plain", None))
        .await
        .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let event: serde_json::Value = serde_json::from_str(&events[0]).unwrap();
    assert!(
        event["payload"]["message"].get("attachments").is_none(),
        "event: {event}"
    );
}

#[tokio::test]
async fn web_send_event_omits_attachments_key_for_empty_attachment_list() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(human_web_send("plain", Some(Vec::new())))
        .await
        .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let event: serde_json::Value = serde_json::from_str(&events[0]).unwrap();
    assert!(
        event["payload"]["message"].get("attachments").is_none(),
        "event: {event}"
    );
}
