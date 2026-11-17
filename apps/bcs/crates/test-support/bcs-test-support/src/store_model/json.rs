use super::*;
use async_trait::async_trait;
use bcs_store_api::json::*;

fn pointer_valid(p: &str) -> bool {
    if !p.is_empty() && !p.starts_with('/') {
        return false;
    }
    let mut chars = p.chars();
    while let Some(c) = chars.next() {
        if c == '~' && !matches!(chars.next(), Some('0' | '1')) {
            return false;
        }
    }
    true
}
fn edit(value: &mut Value, e: &JsonEdit) -> StoreResult<()> {
    let p = match e {
        JsonEdit::Set { pointer, .. } | JsonEdit::Remove { pointer } => pointer,
    };
    if p.is_empty() || !pointer_valid(p) {
        return Err(invalid("invalid patch pointer"));
    }
    let (parent, last) = p.rsplit_once('/').unwrap();
    let token = last.replace("~1", "/").replace("~0", "~");
    let parent = value
        .pointer_mut(parent)
        .ok_or_else(|| invalid("missing parent"))?;
    match parent {
        Value::Object(map) => match e {
            JsonEdit::Set { value, .. } => {
                map.insert(token, value.clone());
            }
            JsonEdit::Remove { .. } => {
                map.remove(&token)
                    .ok_or_else(|| invalid("missing target"))?;
            }
        },
        Value::Array(array) => {
            let i = token
                .parse::<usize>()
                .map_err(|_| invalid("invalid array index"))?;
            if i.to_string() != token || i >= array.len() {
                return Err(invalid("invalid array index"));
            }
            match e {
                JsonEdit::Set { value, .. } => array[i] = value.clone(),
                JsonEdit::Remove { .. } => {
                    array.remove(i);
                }
            }
        }
        _ => return Err(invalid("parent not a container")),
    }
    Ok(())
}
#[async_trait]
impl JsonStorePlugin for Model {
    async fn get_json(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "json")?;
        Ok(s.json.get(key).map(|x| Versioned {
            value: x.value.clone(),
            version: s.versions[key],
        }))
    }
    async fn get_paths(&self, key: &str, paths: &[String]) -> StoreResult<Option<JsonProjection>> {
        if paths.iter().any(|p| !pointer_valid(p)) {
            return Err(invalid("invalid pointer"));
        }
        Ok(self.get_json(key).await?.map(|x| JsonProjection {
            version: x.version,
            values: paths.iter().map(|p| x.value.pointer(p).cloned()).collect(),
        }))
    }
    async fn mutate_json(
        &self,
        key: &str,
        c: WriteCondition,
        m: JsonMutation,
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        expiry(None, e, self.time())?;
        if let JsonMutation::Patch(edits) = &m {
            if edits.is_empty() {
                return Err(invalid("empty patch"));
            }
            for edit in edits {
                let p = match edit {
                    JsonEdit::Set { pointer, .. } | JsonEdit::Remove { pointer } => pointer,
                };
                if p.is_empty() || !pointer_valid(p) {
                    return Err(invalid("invalid pointer"));
                }
            }
        }
        let c = if self.broken_json_cas && matches!(c, WriteCondition::Version(_)) {
            let matched = {
                let s = self.state.lock().unwrap();
                s.next(key, c, s.json.contains_key(key))?.is_some()
            };
            if !matched {
                return Ok(WriteOutcome::ConditionNotMet);
            }
            tokio::task::yield_now().await;
            WriteCondition::Any
        } else {
            c
        };
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "json")?;
        let exists = s.json.contains_key(key);
        let Some(v) = s.next(key, c, exists)? else {
            return Ok(WriteOutcome::ConditionNotMet);
        };
        if !exists && !matches!(m, JsonMutation::Replace(_)) {
            return Ok(WriteOutcome::ConditionNotMet);
        }
        let expires = expiry(s.json.get(key).and_then(|x| x.expires), e, self.time())?;
        let next = match m {
            JsonMutation::Replace(value) => Some(value),
            JsonMutation::Delete => None,
            JsonMutation::Patch(edits) => {
                let mut value = s.json[key].value.clone();
                for e in &edits {
                    edit(&mut value, e)?;
                    if self.broken_json {
                        s.json.get_mut(key).unwrap().value = value.clone();
                    }
                }
                Some(value)
            }
        };
        match next {
            Some(value) => {
                s.json.insert(key.into(), Record { value, expires });
            }
            None => {
                s.json.remove(key);
            }
        }
        s.versions.insert(key.into(), v);
        self.finish(WriteOutcome::Applied { version: v })
    }
}
