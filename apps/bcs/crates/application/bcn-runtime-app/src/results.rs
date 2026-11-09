//! Worker-owned external effects. Only canonical saved results cross the trusted inbox boundary.
use crate::*;
pub(crate) async fn execute(
    ports: &RuntimePorts,
    judge: &dyn JudgeExecutor,
    command: Command,
    now: u64,
) -> RuntimeResult<()> {
    let event_id = EventId::new(
        command.command_id.scope.clone(),
        hash("worker-effect", &command.command_id)?,
    );
    if ports
        .inbox
        .find_input(&command.session_id, &event_id)
        .await?
        .is_some()
    {
        return Ok(());
    }
    let (kind, at_ms) = match command.effect {
        EffectIntent::SaveResult { intent } => {
            let result = ports.results.put_if_absent(intent).await?;
            let at = result.saved_at_ms;
            (SessionInputKind::ResultSaved { result }, at)
        }
        EffectIntent::Judge { request } => {
            let branch = judge.execute(request.clone()).await?;
            if !request.choices.contains(&branch) {
                return Err(RuntimeError::InvalidInput(
                    "judge selected an unavailable branch".into(),
                ));
            }
            (
                SessionInputKind::JudgeDecided {
                    task_id: request.task_id,
                    node_id: request.node_id,
                    branch,
                },
                now,
            )
        }
        _ => {
            return Err(RuntimeError::InvalidInput(
                "Worker received connector effect".into(),
            ));
        }
    };
    ports
        .inbox
        .append_and_wake(
            &command.session_id,
            SessionInput {
                event_id,
                at_ms,
                kind,
            },
        )
        .await?;
    Ok(())
}
