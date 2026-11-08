//! Display-name and ID @mention parsing for message routing.

use super::*;

fn is_unified_ideograph(ch: char) -> bool {
    matches!(
        ch,
        '\u{3400}'..='\u{4DBF}'
            | '\u{4E00}'..='\u{9FFF}'
            | '\u{F900}'..='\u{FAFF}'
            | '\u{20000}'..='\u{2FA1F}'
    )
}

fn is_display_name_mention_boundary(remainder: &str) -> bool {
    let Some(next) = remainder.chars().next() else {
        return true;
    };
    next.is_whitespace()
        || matches!(
            next,
            ',' | '，'
                | '.'
                | '。'
                | '!'
                | '！'
                | '?'
                | '？'
                | ';'
                | '；'
                | '、'
                | '：'
                | '@'
                | '*'
                | '`'
                | '"'
                | '\''
                | ')'
                | '）'
                | ']'
                | '】'
                | '}'
                | '》'
        )
        || is_unified_ideograph(next)
}

impl MessageRouter {
    pub(super) fn resolve_mentions(&self, session: &Group, message: &str) -> Vec<String> {
        let mut resolved_mentions = Vec::new();
        for (at_index, _) in message.match_indices('@') {
            let after_at = &message[at_index + 1..];
            let matched_participant = session
                .participants
                .iter()
                .filter_map(|participant| {
                    let name = participant.bot_name.as_deref()?;
                    if name.is_empty() || !after_at.starts_with(name) {
                        return None;
                    }
                    let remainder = &after_at[name.len()..];
                    if is_display_name_mention_boundary(remainder) {
                        Some((participant, name))
                    } else {
                        None
                    }
                })
                .max_by_key(|(_, name)| name.len());

            if let Some((participant, name)) = matched_participant {
                if !resolved_mentions.contains(&participant.bot_uuid) {
                    debug!(target: MSG_LOG_TARGET, mention = %name, resolved_to = %participant.bot_uuid, "routing full display-name mention match");
                    resolved_mentions.push(participant.bot_uuid.clone());
                }
                continue;
            }

            let at_mention = &message[at_index..];
            let Some(captures) = self.mention_regex.captures(at_mention) else {
                continue;
            };
            if captures.get(0).map_or(true, |value| value.start() != 0) {
                continue;
            }
            let Some(mention) = captures.get(1).map(|value| value.as_str()) else {
                continue;
            };
            let result = session.get_participant(mention).map(|p| p.bot_uuid.clone());
            debug!(target: MSG_LOG_TARGET, mention = %mention, matched = result.is_some(), resolved_to = ?result, "routing mention match");
            if let Some(bot_uuid) = result {
                if !resolved_mentions.contains(&bot_uuid) {
                    resolved_mentions.push(bot_uuid);
                }
            }
        }

        resolved_mentions
    }

    /// Check if message contains @ALL mention.
    pub(super) fn has_all_mention(&self, message: &str) -> bool {
        let lower = message.to_lowercase();
        lower.contains("@all") || lower.contains("@所有人")
    }
}
