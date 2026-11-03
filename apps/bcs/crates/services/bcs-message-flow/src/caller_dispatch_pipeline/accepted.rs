//! Shared post-accept effects for the original send and same-dispatch barrier retry.
use super::*;

pub(super) type AcceptedEffect = Arc<dyn Fn(i64) + Send + Sync>;

impl CallerDispatchPipeline {
    pub(super) fn accepted_effect(
        &self,
        target: &BotDeliveryTarget,
        credential: &RedactedToken,
        command: &SendCallerExecutionCommand,
        lineage: Option<crate::caller_lineage::RootLineage>,
        kind: Option<bcs_domain::shared_stream::SharedContentKind>,
        proof: ProofToken,
        at_term: Option<u64>,
    ) -> AcceptedEffect {
        let pipeline = self.clone();
        let target = target.clone();
        let credential = credential.clone();
        let command = command.clone();
        Arc::new(move |now_ms| {
            // Review-VI P1 term fence: an accepted that matured across a
            // master-term boundary never writes into the fresh incarnation's
            // indexes nor fans out — the old term's identity effects die
            // with the term. (Identity carries no expiry window: no clock
            // comparison gates these effects.)
            if !pipeline.delivery_term_is_current(at_term) {
                tracing::info!(
                    run_id = %command.association.run_id,
                    "accepted effect folded: master term changed before post-accept effects"
                );
                return;
            }
            if let (Some(index), Some(lineage)) = (&pipeline.root_lineage, &lineage) {
                index.register_in_term(pipeline.delivery_term.as_deref(), at_term, || {
                if lineage.parent_execution {
                    // Runtime-only duplication; neither token nor subject enters durable storage.
                    let token = ProofToken::new(proof.format(), proof.expose_token().to_string(), proof.expires_at_ms());
                    if let Ok(token) = token {
                        index.register_parent_execution(&command.association.run_id, lineage.clone(),
                            &command.association.target_bot_id, token, now_ms);
                    }
                } else {
                    index.register(&command.association.run_id, lineage.clone(), now_ms);
                }
                });
            }
            pipeline.schedule_observe_fanout(observe::SharedDispatchInput {
                target_bot_id: &command.association.target_bot_id,
                group_id: command.group_id.as_deref(),
                session_id: &command.association.session_id,
                message_turn_id: &command.message_turn_id,
                caller_task: &command.caller_task,
                shared_content_kind: kind.as_ref(),
            }, &target, command.caller_subject_ref.expose(), &credential, at_term);
        })
    }
}
