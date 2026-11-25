//! Transport-neutral catalog label write intent; policy/normalized values live in Service API.
use std::collections::BTreeMap;
use serde::{Deserialize, Deserializer, Serialize};
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct CatalogLabelsInput(pub BTreeMap<String, Option<Vec<String>>>);
/// A missing field stays None; a present null fails rather than becoming omission.
pub fn present_input<'de, D: Deserializer<'de>>(d:D)->Result<Option<CatalogLabelsInput>,D::Error> {
    CatalogLabelsInput::deserialize(d).map(Some)
}
