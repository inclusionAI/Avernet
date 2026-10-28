"""Lua scripts for the strict manager-sync coordination boundary.

Every script runs against keys that live in **one Redis Cluster slot**:
``plugins.community.manager_sync_coordination`` gives all coordination
keys of a target the same ``{sha256(target_id)}`` hash tag, so EVAL
never hits a CROSSSLOT error and the whole marker/lease family for a
namespace moves together.

Every script first verifies the **namespace generation sentinel**
(``GET <generation> == ARGV``); a missing or mismatched sentinel means
the namespace was lost (never initialized, or flushed away) and the
only safe answer is refusal — the same stop-the-sender behavior the
in-memory reference fake models with ``generation=None``. Normal
operation never auto-creates the sentinel; explicit ops-side
initialization owns that.

Return-code contract (integers survive EVAL round-trips; string
returns arrive as ``bytes``):

- ``-2`` — generation sentinel missing or mismatched (refuse; the
  caller raises ``CoordinationUnavailable``).
- Marker/lease verdicts differ per script:

  - ``CLAIM``: ``0`` a marker blocks (BLOCKED), ``1`` acquired,
    ``2`` a live lease blocks (BUSY). Markers outrank live leases —
    BLOCKED is checked before BUSY.
  - ``BEGIN``: ``-1`` the lease is not this holder's (lost),
    ``0`` a marker exists, ``1`` the in-flight record is open.
  - ``FINISH``: ``1`` the in-flight record was compare-deleted,
    ``0`` the value did not match (late or foreign finish).
  - ``RENEW``: ``1`` lease extended, ``0`` not this holder's lease.
  - ``RELEASE``: ``1`` lease compare-deleted, ``0`` not this
    holder's lease. Markers are never even passed to this script.
  - ``CLEAR_CONFIRMED``: ``1`` exactly one matching marker removed
    (in-flight family first, then owner-hold), ``0`` nothing matched.
  - ``HOLD_OWNER_CHECK``: the owner-hold marker id — the existing one
    if any (idempotent), otherwise the caller's fresh unique candidate
    token (generated Python-side per call and passed via ARGV — never
    derived from tenant/env/bot_pk, so a cleared barrier's id can never
    reach the barrier built after it); the in-flight record is never
    touched by this script.

The ``BEGIN`` script is the Task 3 brief's script verbatim (its
ordering is the safety core: generation → lease → in-flight →
owner-hold → SET); the other scripts follow the same conventions.
"""

from __future__ import annotations

#: Refusal code shared by every script: the generation sentinel is
#: missing or does not match the caller's expectation.
GENERATION_REFUSED = -2

#: claim: KEYS = [lease, inflight, owner_hold, generation]
#: ARGV = [holder, ttl_seconds, expected_generation]
CLAIM = """
-- claim: KEYS = [lease, inflight, owner_hold, generation]
-- ARGV = [holder, ttl_seconds, expected_generation]
if redis.call('GET', KEYS[4]) ~= ARGV[3] then
  return -2
end
if redis.call('EXISTS', KEYS[2]) ~= 0 then
  return 0
end
if redis.call('EXISTS', KEYS[3]) ~= 0 then
  return 0
end
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
  return 1
end
return 2
"""

#: begin: KEYS = [lease, inflight, owner_hold, generation]
#: ARGV = [holder, serialized_attempt, expected_generation]
#: The brief's minimum script, verbatim: generation → lease →
#: in-flight → owner-hold → SET (TTL-less serialized attempt).
BEGIN = """
-- begin: KEYS=lease, inflight, owner_hold, generation
-- ARGV=holder, serialized_attempt, expected_generation
if redis.call('GET', KEYS[4]) ~= ARGV[3] then return -2 end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return -1 end
if redis.call('EXISTS', KEYS[2]) ~= 0 then return 0 end
if redis.call('EXISTS', KEYS[3]) ~= 0 then return 0 end
redis.call('SET', KEYS[2], ARGV[2])
return 1
"""

#: finish: KEYS = [inflight, generation]
#: ARGV = [serialized_attempt, expected_generation]
#: Compare-and-delete ONLY the in-flight record; a late or foreign
#: finish (holder / attempt id / digest mismatch) changes nothing, and
#: the owner-hold marker is not even passed to the script.
FINISH = """
-- finish: KEYS = [inflight, generation]
-- ARGV = [serialized_attempt, expected_generation]
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return -2
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

#: renew: KEYS = [lease, generation]
#: ARGV = [holder, ttl_seconds, expected_generation]
RENEW = """
-- renew: KEYS = [lease, generation]
-- ARGV = [holder, ttl_seconds, expected_generation]
if redis.call('GET', KEYS[2]) ~= ARGV[3] then
  return -2
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

#: release: KEYS = [lease, generation]
#: ARGV = [holder, expected_generation]
RELEASE = """
-- release: KEYS = [lease, generation]
-- ARGV = [holder, expected_generation]
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return -2
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

#: clear_confirmed: KEYS = [inflight, owner_hold, generation]
#: ARGV = [marker_id, expected_generation]
#: CAS-delete exactly ONE marker whose id matches: the in-flight
#: family first (by the serialized attempt's ``attempt_id``), then the
#: owner-hold family (by its stored id). No match → 0.
CLEAR_CONFIRMED = """
-- clear_confirmed: KEYS = [inflight, owner_hold, generation]
-- ARGV = [marker_id, expected_generation]
if redis.call('GET', KEYS[3]) ~= ARGV[2] then
  return -2
end
local inflight = redis.call('GET', KEYS[1])
if inflight then
  local ok, decoded = pcall(cjson.decode, inflight)
  if ok and type(decoded) == 'table' and decoded['attempt_id'] == ARGV[1] then
    return redis.call('DEL', KEYS[1])
  end
end
if redis.call('GET', KEYS[2]) == ARGV[1] then
  return redis.call('DEL', KEYS[2])
end
return 0
"""

#: hold_owner_check: KEYS = [owner_hold, generation]
#: ARGV = [candidate_token, expected_generation]
#: Idempotent: return the existing marker id if one is present (never
#: overwrite, never touch the in-flight record), else SET the caller's
#: fresh unique candidate token and return it. The candidate is
#: generated Python-side per call — a barrier rebuilt after a clear
#: must never be reachable by an id recorded against the old one. The
#: marker is TTL-less by design — it is resolved explicitly via
#: ``clear_confirmed``.
HOLD_OWNER_CHECK = """
-- hold_owner_check: KEYS = [owner_hold, generation]
-- ARGV = [candidate_token, expected_generation]
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return -2
end
local existing = redis.call('GET', KEYS[1])
if existing then
  return existing
end
redis.call('SET', KEYS[1], ARGV[1])
return ARGV[1]
"""
