//! Per-node accumulated output and bounded failure evidence, independent of Runner deltas.
use crate::*;
pub(crate) fn bounded_output(parts: Vec<MessagePart>, budget: usize) -> Vec<MessagePart> {
    let mut out = Vec::new();
    for part in parts {
        let mut candidate = out.clone();
        candidate.push(part.clone());
        if size(&candidate).is_ok_and(|n| n <= budget) {
            out = candidate;
            continue;
        }
        if let MessagePart::Text { text } = part {
            let mut boundaries: Vec<_> = text
                .char_indices()
                .map(|(i, _)| i)
                .take_while(|i| *i <= budget)
                .collect();
            if text.len() <= budget {
                boundaries.push(text.len());
            }
            let (mut lo, mut hi) = (0, boundaries.len());
            while lo + 1 < hi {
                let mid = (lo + hi) / 2;
                let mut candidate = out.clone();
                candidate.push(MessagePart::Text {
                    text: text[..boundaries[mid]].into(),
                });
                if size(&candidate).is_ok_and(|n| n <= budget) {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            if let Some(end) = boundaries.get(lo).copied().filter(|end| *end > 0) {
                out.push(MessagePart::Text {
                    text: text[..end].into(),
                });
            }
        }
        break;
    }
    out
}
pub(crate) async fn reset(
    ports: &RuntimePorts,
    record: &DispatchRecord,
    output: Vec<MessagePart>,
    sequence: u64,
) -> RuntimeResult<()> {
    let snapshot = ports
        .events
        .read_snapshot(&record.context.task_id)
        .await?
        .ok_or(RuntimeError::NotFound)?;
    let prior = ports
        .events
        .read_projection(&record.fence.dispatch_id)
        .await?
        .unwrap_or_default();
    if size(&snapshot.task.output)?.saturating_sub(size(&prior)?) + size(&output)?
        > MAX_VALUE_BYTES - 131072
    {
        return Err(RuntimeError::CapacityExceeded);
    }
    let id = EventId::new(
        record.context.task_id.scope.clone(),
        hash(
            "connector-reset",
            &(&record.fence, sequence, &snapshot.cursor, &output),
        )?,
    );
    ports
        .events
        .publish_reset(
            &record.fence,
            ConnectorProjection {
                task_id: record.context.task_id.clone(),
                output,
                expected_cursor: snapshot.cursor,
            },
            id,
        )
        .await?;
    Ok(())
}
pub(crate) async fn delta(
    ports: &RuntimePorts,
    record: &DispatchRecord,
    text: String,
    sequence: u64,
) -> RuntimeResult<()> {
    let snapshot = ports
        .events
        .read_snapshot(&record.context.task_id)
        .await?
        .ok_or(RuntimeError::NotFound)?;
    if size(&snapshot.task.output)? + size(&text)? > MAX_VALUE_BYTES - 131072 {
        return Err(RuntimeError::CapacityExceeded);
    }
    let id = EventId::new(
        record.context.task_id.scope.clone(),
        hash(
            "connector-delta",
            &(&record.fence, sequence, &snapshot.cursor, &text),
        )?,
    );
    ports
        .events
        .append(
            &record.fence,
            TaskEvent {
                id,
                task_id: record.context.task_id.clone(),
                cursor: snapshot.cursor,
                kind: TaskEventKind::Delta { text },
            },
        )
        .await?;
    // The repository may emit aggregate Reset instead. Subscribers always reread its canonical stream.
    Ok(())
}
