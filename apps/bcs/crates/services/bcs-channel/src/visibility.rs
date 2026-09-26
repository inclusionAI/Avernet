use bcs_domain::{GroupStrategy, ParticipantRole, Visibility};

/// Returns whether a sender role should be visible for a binding strategy.
pub fn visibility_allows(
    strategy: GroupStrategy,
    visibility: Visibility,
    sender_role: ParticipantRole,
) -> bool {
    match visibility {
        Visibility::FullTranscript => true,
        Visibility::LeadOnly => match strategy {
            GroupStrategy::Chat => sender_role == ParticipantRole::Driver,
            GroupStrategy::ManagerWorker => sender_role == ParticipantRole::Manager,
            GroupStrategy::StateMachine => false,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_transcript_allows_all_roles() {
        for strategy in [
            GroupStrategy::Chat,
            GroupStrategy::ManagerWorker,
            GroupStrategy::StateMachine,
        ] {
            for role in [
                ParticipantRole::Driver,
                ParticipantRole::Consultant,
                ParticipantRole::Manager,
                ParticipantRole::Worker,
                ParticipantRole::Observer,
            ] {
                assert!(visibility_allows(strategy, Visibility::FullTranscript, role));
            }
        }
    }

    #[test]
    fn lead_only_allows_chat_driver() {
        assert!(visibility_allows(
            GroupStrategy::Chat,
            Visibility::LeadOnly,
            ParticipantRole::Driver,
        ));
        assert!(!visibility_allows(
            GroupStrategy::Chat,
            Visibility::LeadOnly,
            ParticipantRole::Consultant,
        ));
    }

    #[test]
    fn lead_only_allows_manager_worker_manager() {
        assert!(visibility_allows(
            GroupStrategy::ManagerWorker,
            Visibility::LeadOnly,
            ParticipantRole::Manager,
        ));
        assert!(!visibility_allows(
            GroupStrategy::ManagerWorker,
            Visibility::LeadOnly,
            ParticipantRole::Worker,
        ));
    }

    #[test]
    fn lead_only_blocks_state_machine_output() {
        for role in [
            ParticipantRole::Driver,
            ParticipantRole::Consultant,
            ParticipantRole::Manager,
            ParticipantRole::Worker,
            ParticipantRole::Observer,
        ] {
            assert!(!visibility_allows(
                GroupStrategy::StateMachine,
                Visibility::LeadOnly,
                role,
            ));
        }
    }
}
