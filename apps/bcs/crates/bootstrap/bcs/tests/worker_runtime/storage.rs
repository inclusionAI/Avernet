//! Drive the persistent-document contract against the in-memory mock. This is
//! semantic coverage only; process restart deliberately loses these documents.
use bcs_store_api::{Version, document::DocumentStorePlugin, json::JsonEdit};
use bcs_test_support::store_model::Model;
use serde_json::{Value, json};
pub async fn command(model: &Model, request: &Value) -> anyhow::Result<Value> {
    let key = request["key"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("key missing"))?;
    match request["action"].as_str() {
        Some("create") => Ok(json!(format!(
            "{:?}",
            model.create_document(key, &request["value"]).await?
        ))),
        Some("read") => {
            let found = model.read_document(key).await?;
            Ok(found
                .map(|v| json!({"version":v.version.0,"value":v.value}))
                .unwrap_or(Value::Null))
        }
        Some("paths") => {
            let paths: Vec<String> = serde_json::from_value(request["paths"].clone())?;
            let found = model.read_document_paths(key, &paths).await?;
            Ok(found.map(|v|json!({"version":v.version.0,"values":v.values,"present":v.values.iter().map(Option::is_some).collect::<Vec<_>>()})).unwrap_or(Value::Null))
        }
        Some("patch") => {
            let edits = request["edits"]
                .as_array()
                .ok_or_else(|| anyhow::anyhow!("edits missing"))?
                .iter()
                .map(|v| {
                    let pointer = v["pointer"]
                        .as_str()
                        .ok_or_else(|| anyhow::anyhow!("pointer missing"))?
                        .to_owned();
                    match v["op"].as_str() {
                        Some("set") => Ok(JsonEdit::Set {
                            pointer,
                            value: v["value"].clone(),
                        }),
                        Some("remove") => Ok(JsonEdit::Remove { pointer }),
                        _ => anyhow::bail!("invalid edit"),
                    }
                })
                .collect::<anyhow::Result<Vec<_>>>()?;
            Ok(json!(format!(
                "{:?}",
                model
                    .patch_document(
                        key,
                        Version(request["version"].as_u64().unwrap_or(0)),
                        &edits
                    )
                    .await?
            )))
        }
        _ => anyhow::bail!("invalid storage action"),
    }
}
