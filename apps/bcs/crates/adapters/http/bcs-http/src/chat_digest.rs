use axum::http::StatusCode;

const CHAT_DIGEST_TARGET: &str = "bcs_chat_digest";

#[derive(Debug)]
pub(crate) struct ChatDigestRecord<'a> {
    pub endpoint: &'a str,
    pub from_bot_id: Option<&'a str>,
    pub target_bot_id: &'a str,
    pub run_id: Option<&'a str>,
    pub session_id: Option<&'a str>,
    pub client: Option<&'a str>,
    pub async_mode: bool,
    pub timeout_ms: Option<u64>,
    pub message_len: usize,
    pub duration_ms: u128,
    pub success: bool,
    pub status_code: StatusCode,
    pub error_kind: Option<&'a str>,
}

impl ChatDigestRecord<'_> {
    pub(crate) fn to_digest_line(&self) -> String {
        format!(
            "endpoint={},from_bot_id={},target_bot_id={},run_id={},session_id={},client={},async_mode={},timeout_ms={},message_len={},duration_ms={},success={},status_code={},error_kind={}",
            sanitize_digest_value(self.endpoint),
            digest_option(self.from_bot_id),
            sanitize_digest_value(self.target_bot_id),
            digest_option(self.run_id),
            digest_option(self.session_id),
            digest_option(self.client),
            self.async_mode,
            self.timeout_ms.map(|value| value.to_string()).unwrap_or_default(),
            self.message_len,
            self.duration_ms,
            self.success,
            self.status_code.as_u16(),
            digest_option(self.error_kind),
        )
    }
}

pub(crate) fn log_chat_digest(record: &ChatDigestRecord<'_>) {
    tracing::info!(target: CHAT_DIGEST_TARGET, "{}", record.to_digest_line());
}

fn digest_option(value: Option<&str>) -> String {
    value.map(sanitize_digest_value).unwrap_or_default()
}

fn sanitize_digest_value(value: &str) -> String {
    value
        .chars()
        .map(|ch| match ch {
            ',' | '\n' | '\r' | '\t' => ' ',
            _ => ch,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_digest_line_is_comma_delimited_and_records_required_fields() {
        let record = ChatDigestRecord {
            endpoint: "bot_chat_async",
            from_bot_id: Some("caller-bot"),
            target_bot_id: "target-bot",
            run_id: Some("run-123"),
            session_id: Some("session-1"),
            client: Some("bcs-cli/0.1"),
            async_mode: true,
            timeout_ms: Some(60_000),
            message_len: 12,
            duration_ms: 34,
            success: true,
            status_code: StatusCode::ACCEPTED,
            error_kind: None,
        };

        let line = record.to_digest_line();

        assert_eq!(line.split(',').count(), 13);
        assert_eq!(
            line,
            "endpoint=bot_chat_async,from_bot_id=caller-bot,target_bot_id=target-bot,run_id=run-123,session_id=session-1,client=bcs-cli/0.1,async_mode=true,timeout_ms=60000,message_len=12,duration_ms=34,success=true,status_code=202,error_kind="
        );
    }

    #[test]
    fn chat_digest_line_sanitizes_comma_values() {
        let record = ChatDigestRecord {
            endpoint: "bot_chat",
            from_bot_id: Some("caller,bot\none"),
            target_bot_id: "target,bot",
            run_id: None,
            session_id: None,
            client: Some("bcs-cli,debug\nmode"),
            async_mode: false,
            timeout_ms: None,
            message_len: 0,
            duration_ms: 1,
            success: false,
            status_code: StatusCode::UNAUTHORIZED,
            error_kind: Some("auth,failed"),
        };

        let line = record.to_digest_line();

        assert_eq!(line.split(',').count(), 13);
        assert!(line.contains("from_bot_id=caller bot one"));
        assert!(line.contains("target_bot_id=target bot"));
        assert!(line.contains("client=bcs-cli debug mode"));
        assert!(line.ends_with("error_kind=auth failed"));
    }
}
