use super::*;
use async_trait::async_trait;
use bcs_store_api::zset::*;
use std::{cmp::Ordering as Cmp, ops::Bound};
fn validate(entries: &[ZSetEntry]) -> StoreResult<()> {
    let mut seen = std::collections::HashSet::new();
    if entries.is_empty()
        || entries
            .iter()
            .any(|e| e.score.is_nan() || !seen.insert(&e.member))
    {
        return Err(invalid("invalid zset batch"));
    }
    Ok(())
}
fn add_one(set: &mut BTreeMap<String, f64>, e: &ZSetEntry, mode: ZAddMode) -> bool {
    let old = set.get(&e.member).copied();
    let apply = match mode {
        ZAddMode::Upsert => true,
        ZAddMode::InsertOnly => old.is_none(),
        ZAddMode::UpdateOnly => old.is_some(),
        ZAddMode::Lower => old.is_none_or(|x| e.score < x),
        ZAddMode::Higher => old.is_none_or(|x| e.score > x),
    };
    if apply {
        set.insert(e.member.clone(), e.score);
    }
    apply && old != Some(e.score)
}
fn select(s: &State, key: &str, r: &ZSetRange) -> StoreResult<Vec<ZSetEntry>> {
    if [&r.min, &r.max].iter().any(|b| match b {
        Bound::Included(v) | Bound::Excluded(v) => v.is_nan(),
        _ => false,
    }) || r.after.as_ref().is_some_and(|e| e.score.is_nan())
    {
        return Err(invalid("NaN range"));
    }
    let mut entries: Vec<_> = s
        .zsets
        .get(key)
        .into_iter()
        .flat_map(|s| s.iter())
        .map(|(m, v)| ZSetEntry {
            member: m.clone(),
            score: *v,
        })
        .filter(|e| {
            let lower = match r.min {
                Bound::Unbounded => true,
                Bound::Included(v) => e.score >= v,
                Bound::Excluded(v) => e.score > v,
            };
            let upper = match r.max {
                Bound::Unbounded => true,
                Bound::Included(v) => e.score <= v,
                Bound::Excluded(v) => e.score < v,
            };
            lower
                && upper
                && r.after.as_ref().is_none_or(|a| {
                    e.score > a.score || (e.score == a.score && e.member > a.member)
                })
        })
        .collect();
    entries.sort_by(|a, b| {
        a.score
            .partial_cmp(&b.score)
            .unwrap_or(Cmp::Equal)
            .then_with(|| a.member.cmp(&b.member))
    });
    entries.truncate(r.limit.get() as usize);
    Ok(entries)
}
#[async_trait]
impl ZSetStorePlugin for Model {
    async fn add(&self, key: &str, entries: &[ZSetEntry], mode: ZAddMode) -> StoreResult<u64> {
        validate(entries)?;
        let mut s = self.state.lock().unwrap();
        s.kind(key, "zset")?;
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        let set = s.zsets.entry(key.into()).or_default();
        let changed = entries.iter().filter(|e| add_one(set, e, mode)).count() as u64;
        if !self.broken_zset_noop || changed > 0 {
            s.versions.insert(key.into(), v);
        }
        Ok(changed)
    }
    async fn remove(&self, key: &str, members: &[String]) -> StoreResult<u64> {
        let mut s = self.state.lock().unwrap();
        s.kind(key, "zset")?;
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        let set = s.zsets.entry(key.into()).or_default();
        let count = members.iter().filter(|m| set.remove(*m).is_some()).count();
        s.versions.insert(key.into(), v);
        Ok(count as u64)
    }
    async fn range(&self, key: &str, r: &ZSetRange) -> StoreResult<Vec<ZSetEntry>> {
        let s = self.state.lock().unwrap();
        s.kind(key, "zset")?;
        select(&s, key, r)
    }
    async fn snapshot(&self, key: &str, r: &ZSetRange) -> StoreResult<ZSetSnapshot> {
        let s = self.state.lock().unwrap();
        s.kind(key, "zset")?;
        Ok(ZSetSnapshot {
            version: s.versions.get(key).copied(),
            entries: select(&s, key, r)?,
        })
    }
    async fn update_if_version(
        &self,
        key: &str,
        expected: Option<Version>,
        changes: &[ZSetMutation],
    ) -> StoreResult<WriteOutcome> {
        let entries: Vec<_> = changes
            .iter()
            .map(|m| match m {
                ZSetMutation::Put { entry, .. } => entry.clone(),
                ZSetMutation::Remove { member } => ZSetEntry {
                    member: member.clone(),
                    score: 0.,
                },
            })
            .collect();
        validate(&entries)?;
        if expected == Some(Version(0)) {
            return Err(invalid("zero version"));
        }
        if self.broken_zset_cas {
            let matched = { self.state.lock().unwrap().versions.get(key).copied() == expected };
            if !matched {
                return Ok(WriteOutcome::ConditionNotMet);
            }
            tokio::task::yield_now().await;
        }
        let mut s = self.state.lock().unwrap();
        s.kind(key, "zset")?;
        if !self.broken_zset_cas && s.versions.get(key).copied() != expected {
            return Ok(WriteOutcome::ConditionNotMet);
        }
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        let set = s.zsets.entry(key.into()).or_default();
        let mut changed = false;
        for m in changes {
            changed |= match m {
                ZSetMutation::Put { entry, mode } => add_one(set, entry, *mode),
                ZSetMutation::Remove { member } => set.remove(member).is_some(),
            };
        }
        let actual = if self.broken_zset_noop && !changed {
            expected.unwrap()
        } else {
            v
        };
        s.versions.insert(key.into(), actual);
        Ok(WriteOutcome::Applied { version: actual })
    }
}
