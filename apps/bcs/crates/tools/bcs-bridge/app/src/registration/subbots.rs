//! Short sibling identifiers: local de-duplication and bounded remote attempts.
use std::collections::HashSet;

use anyhow::bail;

pub(super) const REGISTRATION_ATTEMPTS: usize = 3;
const SLUG_LENGTH: usize = 5;
const SLUG_CANDIDATES: usize = 128;
const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";

pub(super) struct Slugs { used: HashSet<String> }

impl Slugs {
    pub(super) fn new(bots: &[toml::Value]) -> Self {
        let used = bots.iter().filter_map(|bot| {
            bot.get("provider_bot_ref").and_then(toml::Value::as_str)
                .and_then(|reference| reference.rsplit_once(':')).map(|(_, slug)| slug.to_owned())
        }).collect();
        Self { used }
    }

    pub(super) fn next(&mut self) -> anyhow::Result<String> { self.next_with(random_slug) }

    fn next_with(&mut self, mut generate: impl FnMut() -> String) -> anyhow::Result<String> {
        for _ in 0..SLUG_CANDIDATES {
            let slug = generate();
            if self.used.insert(slug.clone()) { return Ok(slug); }
        }
        bail!("Cannot generate an unused Bot suffix; no registration request was sent for this attempt")
    }
}

fn random_slug() -> String {
    let mut slug = String::with_capacity(SLUG_LENGTH);
    while slug.len() < SLUG_LENGTH {
        // The first six UUID v4 bytes are random (no version/variant bits).
        // Rejection sampling avoids bias when mapping bytes to 36 characters.
        for byte in uuid::Uuid::new_v4().as_bytes().iter().take(6) {
            if *byte < 252 {
                slug.push(ALPHABET[usize::from(*byte) % ALPHABET.len()] as char);
                if slug.len() == SLUG_LENGTH { break; }
            }
        }
    }
    slug
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_slugs_are_five_lowercase_alphanumeric_characters() {
        for _ in 0..128 {
            let slug = random_slug();
            assert_eq!(slug.len(), 5);
            assert!(slug.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit()));
        }
    }

    #[test]
    fn skips_saved_and_previously_attempted_suffixes() {
        let bot: toml::Value = toml::from_str("provider_bot_ref='parent:region:abc12'").unwrap();
        let mut slugs = Slugs::new(&[bot]);
        let mut candidates = ["abc12", "def34"].into_iter();
        assert_eq!(slugs.next_with(|| candidates.next().unwrap().into()).unwrap(), "def34");
        let mut candidates = ["def34", "abc12", "ghi56"].into_iter();
        assert_eq!(slugs.next_with(|| candidates.next().unwrap().into()).unwrap(), "ghi56");
    }

    #[test]
    fn local_collision_work_is_bounded() {
        let mut slugs = Slugs::new(&[]);
        slugs.next_with(|| "abc12".into()).unwrap();
        let mut calls = 0;
        assert!(slugs.next_with(|| { calls += 1; "abc12".into() }).is_err());
        assert_eq!(calls, SLUG_CANDIDATES);
    }
}
