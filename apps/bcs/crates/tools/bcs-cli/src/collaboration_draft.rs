//! File-to-BCS authoring workflow. The normal chat reply remains the sender.
use crate::BcsClient;
use anyhow::{Result, anyhow, bail};
use clap::Subcommand;
use reqwest::Method;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::Write,
    path::{Path, PathBuf},
};

const MAX_BYTES: usize = 65_536;
const MAX_REVISION: u64 = 9_007_199_254_740_991;

#[derive(Subcommand)]
pub enum DraftCommands {
    /// Upload a file, validate its stored revision, and produce a short AixUI reference
    Create {
        file: PathBuf,
        #[arg(long, value_parser = parse_id)]
        draft_id: String,
        #[arg(long)]
        title: Option<String>,
    },
    /// Append exactly one revision using compare-and-swap
    Update {
        #[arg(value_parser = parse_id)]
        draft_id: String,
        file: PathBuf,
        #[arg(long, value_parser = parse_revision)]
        expected_revision: u64,
        #[arg(long)]
        title: Option<String>,
    },
    /// Download a fixed revision without overwriting an existing file
    Get {
        #[arg(value_parser = parse_id)]
        draft_id: String,
        #[arg(long, value_parser = parse_revision)]
        revision: u64,
        #[arg(long)]
        output: PathBuf,
    },
    /// Validate the original stored bytes of one revision
    Validate {
        #[arg(value_parser = parse_id)]
        draft_id: String,
        #[arg(long, value_parser = parse_revision)]
        revision: u64,
    },
    /// Preview a fixed revision, including its actual invalid-definition diagnostics
    Preview {
        #[arg(value_parser = parse_id)]
        draft_id: String,
        #[arg(long, value_parser = parse_revision)]
        revision: u64,
    },
    /// Permanently revoke all read references for this revision
    Revoke {
        #[arg(value_parser = parse_id)]
        draft_id: String,
        #[arg(long, value_parser = parse_revision)]
        revision: u64,
    },
    /// Delete source revisions and retain an ID tombstone
    Delete {
        #[arg(value_parser = parse_id)]
        draft_id: String,
    },
}

fn parse_id(id: &str) -> Result<String, String> {
    uuid::Uuid::parse_str(id)
        .ok()
        .filter(|u| u.to_string() == id)
        .map(|_| id.to_string())
        .ok_or_else(|| "draft ID must be a canonical UUID".into())
}

fn parse_revision(value: &str) -> Result<u64, String> {
    value
        .parse::<u64>()
        .ok()
        .filter(|n| (1..=MAX_REVISION).contains(n))
        .ok_or_else(|| "revision must be a positive safe integer".into())
}

#[derive(Deserialize)]
pub struct Revision {
    pub draft_id: String,
    pub revision: u64,
    pub definition_yaml: String,
    pub title: Option<String>,
    pub content_hash: String,
    pub size_bytes: usize,
}

impl Revision {
    fn verify(value: Value, id: &str, revision: u64) -> Result<Self> {
        let r: Self = serde_json::from_value(value)
            .map_err(|_| anyhow!("invalid draft revision response"))?;
        if r.draft_id != id
            || r.revision != revision
            || r.definition_yaml.len() > MAX_BYTES
            || r.size_bytes != r.definition_yaml.len()
            || r.content_hash != hash(&r.definition_yaml)
            || r.title
                .as_ref()
                .is_some_and(|t| t.trim().is_empty() || t.chars().count() > 120)
        {
            bail!("draft integrity check failed");
        }
        Ok(r)
    }
    fn summary(&self) -> Value {
        json!({"draft_id":self.draft_id,"revision":self.revision,"content_hash":self.content_hash,"size_bytes":self.size_bytes})
    }
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Reference {
    pub schema_version: u8,
    pub draft_id: String,
    pub revision: u64,
}

pub fn reference_message(mut value: Value, id: &str, revision: u64) -> Result<(Reference, String)> {
    // The HTTP adapter retains this inert field for deployed CLI readers.
    // New cards omit it, including when talking to an older server.
    if let Some(fields) = value.as_object_mut() {
        fields.remove("readToken");
    }
    let r: Reference =
        serde_json::from_value(value).map_err(|_| anyhow!("invalid draft reference response"))?;
    if r.schema_version != 2
        || r.draft_id != id
        || r.revision != revision
        || parse_id(&r.draft_id).is_err()
        || !(1..=MAX_REVISION).contains(&revision)
    {
        bail!("invalid draft reference response");
    }
    // Validated fields contain only ASCII UUID, field names and integers.
    let message = format!(
        "<AixUI type=\"card\" component=\"bcsPanel.CollaborationDefinitionCard\" params='{}' />",
        serde_json::to_string(&r)?
    );
    Ok((r, message))
}

fn hash(s: &str) -> String {
    hex::encode(Sha256::digest(s.as_bytes()))
}

fn input(file: &Path, title: &Option<String>) -> Result<String> {
    if title
        .as_ref()
        .is_some_and(|t| t.trim().is_empty() || t.chars().count() > 120)
    {
        bail!("title must contain 1..=120 Unicode code points and must not be blank");
    }
    let bytes = std::fs::read(file)?;
    if bytes.len() > MAX_BYTES {
        bail!("draft YAML exceeds 65536 bytes");
    }
    String::from_utf8(bytes).map_err(|_| anyhow!("draft YAML must be valid UTF-8"))
}

async fn get(client: &BcsClient, id: &str, revision: u64) -> Result<Revision> {
    Revision::verify(
        client
            .draft_request(Method::GET, &format!("{id}/revisions/{revision}"), None)
            .await?,
        id,
        revision,
    )
}

async fn validate(client: &BcsClient, r: &Revision) -> Result<Value> {
    let v = client
        .draft_request(
            Method::POST,
            &format!("{}/revisions/{}/validate", r.draft_id, r.revision),
            None,
        )
        .await?;
    if v["draft_id"] != r.draft_id
        || v["revision"] != r.revision
        || v["content_hash"] != r.content_hash
        || v["validated_at"]
            .as_str()
            .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
            .is_none()
        || v["validator"].as_str().is_none_or(str::is_empty)
        || v["validation"]["valid"].as_bool().is_none()
        || !v["validation"]["summary"].is_object()
    {
        bail!("invalid fixed-revision validation response");
    }
    Ok(v)
}

/// Always returns structured phase information; callers print it even on failure.
pub async fn execute(client: &BcsClient, command: DraftCommands) -> (Value, bool) {
    match execute_inner(client, command).await {
        Ok(result) => result,
        Err(_) => (
            json!({"saved":false,"phase":"request","error":"draft operation failed; no delivery message produced"}),
            false,
        ),
    }
}

async fn execute_inner(client: &BcsClient, command: DraftCommands) -> Result<(Value, bool)> {
    match command {
        DraftCommands::Create {
            file,
            draft_id,
            title,
        } => {
            let yaml = input(&file, &title)?;
            save(client, &draft_id, yaml, title, None).await
        }
        DraftCommands::Update {
            draft_id,
            file,
            expected_revision,
            title,
        } => {
            if expected_revision == MAX_REVISION {
                bail!("revision limit reached");
            }
            let yaml = input(&file, &title)?;
            save(client, &draft_id, yaml, title, Some(expected_revision)).await
        }
        DraftCommands::Get {
            draft_id,
            revision,
            output,
        } => {
            let r = get(client, &draft_id, revision).await?;
            let parent = output
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            let mut temp = tempfile::NamedTempFile::new_in(parent)?;
            temp.write_all(r.definition_yaml.as_bytes())?;
            temp.as_file().sync_all()?;
            temp.persist_noclobber(&output)
                .map_err(|_| anyhow!("output exists or cannot be persisted"))?;
            let mut result = r.summary();
            result["output"] = json!(output);
            Ok((result, true))
        }
        DraftCommands::Validate { draft_id, revision } => {
            let r = get(client, &draft_id, revision).await?;
            let result = validate(client, &r).await?;
            let valid = result["validation"]["valid"] == true;
            Ok((result, valid))
        }
        DraftCommands::Preview { draft_id, revision } => {
            let r = get(client, &draft_id, revision).await?;
            finish(client, r, false, true).await
        }
        DraftCommands::Revoke { draft_id, revision } => {
            client
                .draft_request(
                    Method::DELETE,
                    &format!("{draft_id}/revisions/{revision}/share"),
                    None,
                )
                .await?;
            Ok((
                json!({"draft_id":draft_id,"revision":revision,"revoked":true}),
                true,
            ))
        }
        DraftCommands::Delete { draft_id } => {
            client
                .draft_request(Method::DELETE, &draft_id, None)
                .await?;
            Ok((json!({"draft_id":draft_id,"deleted":true}), true))
        }
    }
}

async fn save(
    client: &BcsClient,
    id: &str,
    yaml: String,
    title: Option<String>,
    expected: Option<u64>,
) -> Result<(Value, bool)> {
    let revision = expected.map_or(1, |n| n + 1);
    let mut body = json!({"definition_yaml":yaml,"title":title});
    let (method, path) = if let Some(n) = expected {
        body["expected_revision"] = json!(n);
        (Method::POST, format!("{id}/revisions"))
    } else {
        (Method::PUT, id.to_string())
    };
    let mut recovered = false;
    let r = match client.draft_request(method, &path, Some(&body)).await {
        Ok(value) => match Revision::verify(value, id, revision) {
            Ok(r) => r,
            Err(_) if expected.is_some() => match get(client, id, revision).await {
                Ok(r) => {
                    recovered = true;
                    r
                }
                Err(_) => {
                    return Ok((
                        json!({"saved":null,"draft_id":id,"revision":revision,"phase":"integrity","error":"save response failed integrity checks; inspect this fixed revision"}),
                        false,
                    ));
                }
            },
            Err(_) => {
                return Ok((
                    json!({"saved":null,"draft_id":id,"revision":revision,"phase":"integrity","error":"save response failed integrity checks; inspect this fixed revision"}),
                    false,
                ));
            }
        },
        Err(error)
            if expected.is_some()
                && (error.status.is_none()
                    || error.status == Some(409)
                    || error.status.is_some_and(|s| s >= 500)) =>
        {
            // Only n+1, never head. Matching content is not attributed to this call.
            match get(client, id, revision).await {
                Ok(r) => {
                    recovered = true;
                    r
                }
                Err(_) => {
                    return Ok((
                        json!({"saved":null,"draft_id":id,"revision":revision,"phase":"save","error":"update outcome unknown; inspect this fixed revision"}),
                        false,
                    ));
                }
            }
        }
        Err(e) => {
            return Ok((
                json!({"saved":if e.status.is_none() || e.status.is_some_and(|s| s>=500) { Value::Null } else { json!(false) },"draft_id":id,"revision":revision,"phase":"save","http_status":e.status,"error":"save failed or is uncertain; retry create with the same draft ID"}),
                false,
            ));
        }
    };
    if r.content_hash != hash(&yaml) || r.definition_yaml != yaml || r.title != title {
        return Ok((
            json!({"saved":false,"draft_id":id,"revision":revision,"phase":"conflict","error":"fixed revision contains different content"}),
            false,
        ));
    }
    finish(client, r, recovered, false).await
}

async fn finish(
    client: &BcsClient,
    r: Revision,
    recovered: bool,
    allow_invalid: bool,
) -> Result<(Value, bool)> {
    let mut output = r.summary();
    output["saved"] = json!(true);
    output["matched_existing_revision"] = json!(recovered);
    let validation = match validate(client, &r).await {
        Ok(v) => v,
        Err(_) => {
            output["phase"] = json!("validate");
            output["error"] = json!("stored revision validation unavailable or malformed");
            return Ok((output, false));
        }
    };
    output["validation"] = validation;
    if !allow_invalid && output["validation"]["validation"]["valid"] != true {
        output["phase"] = json!("validate");
        return Ok((output, false));
    }
    let reference = client
        .draft_request(
            Method::GET,
            &format!("{}/revisions/{}/reference", r.draft_id, r.revision),
            None,
        )
        .await;
    let built = reference
        .ok()
        .and_then(|v| reference_message(v, &r.draft_id, r.revision).ok());
    let Some((reference, message)) = built else {
        output["phase"] = json!("reference");
        output["error"] = json!("stored revision reference unavailable");
        return Ok((output, false));
    };
    output["reference"] = json!(reference);
    output["message"] = json!(message);
    output["phase"] = json!("ready");
    Ok((output, true))
}
