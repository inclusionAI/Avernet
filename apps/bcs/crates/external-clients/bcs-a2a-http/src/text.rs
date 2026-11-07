//! Append-only projection of one A2A Artifact into BCS delta text.
//!
//! Callers clone the projection, apply an update, ingest the returned suffix,
//! then replace the original only on successful ingestion. A tool event must
//! not reset this state. Task completion is handled separately from lastChunk.

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ArtifactText {
    text: String,
    initialized: bool,
    finished: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TextProjectionError {
    MissingBaseline,
    IntermediateReplacement,
    RevisionUnsupported,
    ArtifactAlreadyFinished,
}

impl ArtifactText {
    pub fn text(&self) -> &str { &self.text }

    /// Returns only the new suffix to ingest. Errors leave the state intact.
    pub fn update(
        &mut self, text: &str, append: bool, last_chunk: bool,
    ) -> Result<String, TextProjectionError> {
        if self.finished {
            // An identical complete replacement can be observed again during recovery.
            if !append && last_chunk && text == self.text { return Ok(String::new()); }
            return Err(TextProjectionError::ArtifactAlreadyFinished);
        }
        let suffix = if append {
            if !self.initialized { return Err(TextProjectionError::MissingBaseline); }
            text.to_owned()
        } else if !self.initialized {
            text.to_owned()
        } else {
            if !last_chunk { return Err(TextProjectionError::IntermediateReplacement); }
            text.strip_prefix(&self.text)
                .ok_or(TextProjectionError::RevisionUnsupported)?.to_owned()
        };
        self.text.push_str(&suffix);
        self.initialized = true;
        self.finished = last_chunk;
        Ok(suffix)
    }

    /// A Task/GetTask snapshot establishes the baseline or repairs a gap.
    /// This is not permission to accept intermediate replacement events.
    pub fn snapshot(&mut self, text: &str) -> Result<String, TextProjectionError> {
        let suffix = if self.initialized {
            text.strip_prefix(&self.text)
                .ok_or(TextProjectionError::RevisionUnsupported)?
        } else { text };
        if self.finished && !suffix.is_empty() {
            return Err(TextProjectionError::ArtifactAlreadyFinished);
        }
        let suffix = suffix.to_owned();
        self.text.push_str(&suffix);
        self.initialized = true;
        Ok(suffix)
    }
}
