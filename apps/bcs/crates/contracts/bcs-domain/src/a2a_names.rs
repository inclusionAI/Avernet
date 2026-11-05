//! BCN public names; Bot names are exact Unicode strings, not remote addresses.
pub fn valid_provider_name(name:&str)->bool {
    let bytes=name.as_bytes();
    (1..=64).contains(&bytes.len()) && bytes.first().is_some_and(u8::is_ascii_lowercase)
        && bytes.iter().all(|c|c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c,b'-'|b'_'))
}
pub fn card_bot_name(card:&serde_json::Value)->Result<&str,&'static str> {
    let name=card.get("name").and_then(serde_json::Value::as_str).ok_or("AgentCard.name is required")?;
    if !(2..=64).contains(&name.chars().count()) || name!=name.trim() || name.chars().any(char::is_control) {
        return Err("AgentCard.name must contain 2..64 characters without surrounding whitespace or control characters");
    }
    Ok(name)
}
