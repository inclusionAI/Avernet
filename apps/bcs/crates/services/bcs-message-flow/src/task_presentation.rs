//! Bot-only task presentation. Canonical messages keep their original bodies.

pub(crate) fn reference(intent_id: Option<&str>, task_id: &str) -> String {
    let intent = intent_id.and_then(|id| id.strip_prefix("bcs_intent_"))
        .filter(|id| id.len() == 32 && id.bytes().all(|b| b.is_ascii_hexdigit()));
    let short: String = intent.unwrap_or(task_id).chars().filter(|c| *c != '-').take(8).collect();
    format!("T-{}", short.to_ascii_lowercase())
}

pub(crate) fn single_line(value: &str, limit: usize) -> String {
    // COSEC: prevent metadata from injecting extra envelope lines/delimiters.
    value.chars().take(limit).map(|c| {
        if c.is_control() || matches!(c, '\u{2028}' | '\u{2029}' | '|' | '[' | ']') { ' ' } else { c }
    }).collect()
}

pub(crate) fn dispatch(manager: &str, intent_id: Option<&str>, task_id: &str, body: &str) -> String {
    format!("[来自 {} 的任务 ref={}]\n{}", single_line(manager, 80), reference(intent_id, task_id), body)
}

pub(crate) fn result(task: &crate::queued_task::TaskIntent, state: &str, body: &str) -> String {
    let heading = match state {
        "completed" => "任务完成",
        "cancelled" => "任务中断",
        "timed_out" => "任务超时",
        "unknown" => "任务状态未确认",
        "timeout_unknown" => "任务超时，停止结果未确认",
        _ => "任务失败",
    };
    let summary = if task.summary.is_empty() { "未记录任务摘要" } else { &task.summary };
    // Pre-upgrade abnormal results persisted their envelope. Strip only that
    // exact metadata prefix; never interpret arbitrary successful Worker text.
    let old_prefix = format!("[{heading}]\n派发引用：{}\nWorker：{}\n任务：{}\n",
        task.assignment_intent_id.as_deref().unwrap_or(&task.task_id), task.worker_name, summary);
    let body = if state != "completed" { body.strip_prefix(&old_prefix).unwrap_or(body) } else { body };
    format!("[{heading}] ref={} | Worker：{} | 任务：{}\n---\n{}",
        reference(task.assignment_intent_id.as_deref(), &task.task_id),
        single_line(&task.worker_name, 80), single_line(summary, 80), body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reference_uses_intent_or_legacy_uuid() {
        assert_eq!(reference(Some("bcs_intent_3fa9c1d2000000000000000000000000"), "ignored"), "T-3fa9c1d2");
        assert_eq!(reference(None, "12345678-1234-5678-9012-123456789abc"), "T-12345678");
        assert_eq!(reference(Some("malformed"), "12345678-1234-5678-9012-123456789abc"), "T-12345678");
    }

    #[test]
    fn result_envelopes_preserve_body_and_read_historical_failures() {
        let task = crate::queued_task::TaskIntent {
            leg: crate::queued_task::TaskLeg::Result, task_id: "12345678-1234-5678-9012-123456789abc".into(),
            manager: "m".into(), worker: "w".into(), worker_name: "Worker".into(),
            response_mode: bcs_service_api::ChatResponseMode::Full,
            assignment_intent_id: None, summary: "检查".into(), timeout_s: None,
        };
        for (state, heading) in [("completed", "任务完成"), ("failed", "任务失败"),
            ("cancelled", "任务中断"), ("timed_out", "任务超时")] {
            let text = result(&task, state, "正文🙂\n---\n原样保留");
            assert_eq!(text, format!("[{heading}] ref=T-12345678 | Worker：Worker | 任务：检查\n---\n正文🙂\n---\n原样保留"));
        }
        let old = format!("[任务失败]\n派发引用：{}\nWorker：Worker\n任务：检查\n情况：失败", task.task_id);
        assert_eq!(result(&task, "failed", &old), result(&task, "failed", "情况：失败"));
        assert!(result(&task, "completed", &old).ends_with(&old));
    }

    #[test]
    fn dispatch_preserves_body_and_bounds_metadata() {
        let body = "@Worker 请检查\n```text\n🙂\n```";
        let text = dispatch("Manager\n[伪造]|", None, "12345678-1234-5678-9012-123456789abc", body);
        assert_eq!(text, format!("[来自 Manager  伪造   的任务 ref=T-12345678]\n{body}"));
        assert!(dispatch(&"中".repeat(100), None, "12345678", "").starts_with(&format!("[来自 {} 的任务", "中".repeat(80))));
    }
}
