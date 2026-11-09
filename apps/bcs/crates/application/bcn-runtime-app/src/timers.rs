//! Persisted absolute deadlines become stable, replayable inbox inputs after restart.
use crate::*;
pub(crate) async fn restore(
    ports: &RuntimePorts,
    reducer: &dyn SessionRunner,
    cp: &SessionCheckpoint,
    now: u64,
) -> RuntimeResult<()> {
    for deadline in cp.deadlines.iter().filter(|d| d.at_ms <= now).take(64) {
        let event_id = EventId::new(
            cp.spec.session_id.scope.clone(),
            hash("timer", &deadline.timer_id)?,
        );
        if ports
            .inbox
            .find_input(&cp.spec.session_id, &event_id)
            .await?
            .is_some()
        {
            continue;
        }
        let mut input = SessionInput {
            event_id,
            at_ms: deadline.at_ms,
            kind: SessionInputKind::TimerFired {
                timer_id: deadline.timer_id.clone(),
                output: vec![],
            },
        };
        // Pure preview identifies the authoritative affected node; no hash parsing or effects.
        let preview = reducer.reduce(&cp.state, &input)?;
        let intent = preview.effects.iter().find_map(|e| match e {
            EffectIntent::SaveResult { intent }
                if intent.error == Some(RuntimeError::ExecutionUnknown) =>
            {
                Some(intent)
            }
            _ => None,
        });
        if let Some(intent) = intent {
            if let Some(dispatch) = cp
                .state
                .current_task
                .as_ref()
                .filter(|t| t.task_id == intent.task_id)
                .and_then(|t| t.nodes.get(intent.node_id.as_deref().unwrap_or("direct")))
                .and_then(|n| n.dispatch_id.as_ref())
            {
                let output = match ports.events.read_projection(dispatch).await {
                    Ok(output) => output.unwrap_or_default(),
                    Err(RuntimeError::NotFound) => vec![],
                    Err(e) => return Err(e),
                };
                input.kind = SessionInputKind::TimerFired {
                    timer_id: deadline.timer_id.clone(),
                    output: projection::bounded_output(output, MAX_CONTENT_BYTES),
                };
            }
        }
        ports
            .inbox
            .append_and_wake(&cp.spec.session_id, input)
            .await?;
    }
    Ok(())
}
