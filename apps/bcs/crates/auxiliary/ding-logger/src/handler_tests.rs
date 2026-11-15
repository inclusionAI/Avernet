use super::*;
use serde_json::json;
use base64::{Engine as _, engine::general_purpose::STANDARD};

#[path = "../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_ding_frames_decode_filter_deduplicate_and_preserve_sender() {
    legacy::isolated(|| {
        let groups = HashSet::from(["allowed".to_string()]);
        let dedup = DedupStore::new();
        let callback = json!({"conversationId":"allowed", "msgId":"callback-1", "senderStaffId":"staff", "senderNick":"昵称", "text":{"content":"你好"}});
        for data in [callback.clone(), json!(callback.to_string()), json!(STANDARD.encode(callback.to_string()))] {
            handle_frame(&json!({"type":"CALLBACK", "data":data}), &groups, &dedup).unwrap();
        }
        let scene = json!({"openConversationId":"allowed", "openMsgId":"scene-1", "senderUserId":"scene-staff", "nickName":"Scene", "msgContent":json!({"text":"scene text"}).to_string()});
        handle_frame(&json!({"type":"EVENT", "headers":{"eventType":"chat.message"}, "data":scene}), &groups, &dedup).unwrap();
        for frame in [
            json!({"type":"SYSTEM"}),
            json!({"type":"EVENT", "headers":{"eventType":"chat.member"}, "data":{}}),
            json!({"type":"CALLBACK", "data":{"conversationId":"other", "msgId":"filtered"}}),
        ] { handle_frame(&frame, &groups, &dedup).unwrap(); }
        let rows = legacy::rows("ding_group_message");
        assert_eq!(rows.len(), 2, "encodings share dedup identity; other groups and non-message events stay silent");
        let callback: Value = serde_json::from_str(rows[0].message.trim()).unwrap();
        assert_eq!(callback["sender_staff_id"], "staff");
        assert_eq!(callback["sender_nick"], "昵称");
        assert_eq!(callback["content"], "你好");
        let event: Value = serde_json::from_str(rows[1].message.trim()).unwrap();
        assert_eq!(event["message_id"], "scene-1");
        assert_eq!(event["sender_staff_id"], "scene-staff");
        assert_eq!(event["content"], "scene text");
    });
}

#[test]
fn coverage_ding_malformed_frames_return_errors_without_consuming_dedup_ids() {
    let groups = HashSet::from(["allowed".into()]);
    let dedup = DedupStore::new();
    for frame in [
        json!({"type":"CALLBACK"}),
        json!({"type":"CALLBACK", "data":"not json or base64"}),
        json!({"type":"EVENT", "headers":{"eventType":"chat.message"}, "data":{"msgContent":"{"}}),
        json!({"type":"CALLBACK", "data":STANDARD.encode([0xff, 0xfe])}),
    ] { assert!(handle_frame(&frame, &groups, &dedup).is_err(), "{frame}"); }
    assert!(!dedup.is_duplicate("unused"));
    assert!(dedup.is_duplicate("unused"));
    let defaults = parse_callback_record(&json!({})).unwrap();
    assert!(defaults.group_id.is_empty() && defaults.content.is_empty() && defaults.message_id.is_empty());
    assert!(parse_event_record(&json!({"msgContent":"{}"})).unwrap().unwrap().content.is_empty());
}
