---
status: adopted
date: 2026-11-26
owner: BCS distributed coordination
---

# Connector partition ownership

## Scope and authority

This amends the Connector runtime
([2026-11-17-connector-runtime](../2026-11-17-connector-runtime/)). The calls a
Connector instance runs are no longer only the ones whose hints it happened to
read first. Each command-stream partition of a pool has exactly one owner
Connector instance, and that owner runs the pool's calls in that partition.
Dispatch fencing (holder epoch, version bands), the dispatch document as the
source of truth, and the Worker's supervisor re-posting hints are unchanged.

## Problem

Today any Connector may receive any hint (the consumer group hands each entry
to whichever consumer reads first), and a call held by another instance can be
taken over only once membership proves that instance `Dead`. A drain therefore
cannot hand calls over: it stops them and retires, and recovery waits for the
retire to make the instance `Dead` and for hints to be re-posted. The held
index is per instance, so a new holder cannot find the calls it should look at.

## Behavior

1. **Fixed partitions, one owner each.** A pool's calls are spread over the
   same `N` command-stream partitions the Worker posts to
   (`partition = sha256(did)[..8] mod N`). Each partition has an owner record;
   at most one live Connector instance owns a partition at a time, under a
   rising owner epoch.
2. **Acquiring partitions.** Every Connector periodically looks at every
   partition. A partition with no owner, or whose owner is `Dead`, is claimed.
   Live non-draining Connectors are ranked per partition by highest random
   weight (HRW); a Connector waits `rank x candidate_backoff` before claiming,
   so normally the best-ranked one wins. A healthy owner is never displaced.
   `Unknown` liveness is never treated as `Dead`.
3. **Reading only owned partitions.** A Connector reads hints only from the
   partitions it owns.
4. **Per-partition in-flight set.** Each partition keeps the set of its
   unfinished calls. A call is added before its claim and removed only after
   it ended (or its document is gone). The set may list too much, never too
   little. It replaces the per-instance held index.
5. **Claim rule.** A Connector claims a dispatch when it owns the dispatch's
   partition (proved against the current owner record), and then may take
   over the call from any other holder. Without that proof the old rule still
   applies: only a `Dead` holder may be taken over.
6. **Recovery on acquisition.** When a Connector acquires a partition (by
   claim or by handoff), it claims the calls listed in that partition's
   in-flight set right away, instead of waiting for re-posted hints.
7. **Graceful drain with handoff.** On drain a Connector:
   1. stops claiming and asks every running call to stop at a safe point: not
      while a start, a reply or a cancel is being sent, and not while the
      agent's event stream is open; a call waiting for a human reply or
      followed by polling stops at once;
   2. stops whatever still runs shortly before the grace period ends, keeping
      time for step 3;
   3. hands each partition it owns directly to one successor: the best
      HRW-ranked live, non-draining Connector. The successor commits the
      transfer into the partition's owner record. A rejection moves on to the
      next candidate; an unclear answer is resolved from the owner record;
   4. retires its registration.
   Partitions not handed over before the deadline are claimed by others once
   the drained instance is `Dead`, as today.
8. **Peer channel.** A Connector registers a control address; the handoff
   request travels to the successor's control endpoint. A Connector that is
   draining, not ready, or not this incarnation refuses or fences it.

## Acceptance criteria

- AC1 A partition owner record for Connectors never collides with the Worker's
  shard owner records (`…:<pool>:p0003:owner` vs `…:s0042:owner`).
- AC2 Two Connectors never own the same partition at the same epoch; a
  partition owned by a live Connector is not claimed by another.
- AC3 A Connector reads hints only from partitions it owns.
- AC4 The partition owner may take over a call held by a live other instance;
  a non-owner may not; a claim with a stale ownership proof is refused.
- AC5 A lost claim race never removes a call from the in-flight set; an ended
  call is removed.
- AC6 On acquiring a partition the owner claims the calls listed for it.
- AC7 Drain stops calls at safe points, hands every owned partition to a peer
  when one accepts, then retires; the successor owns the partitions and can
  take the calls over immediately.
- AC8 A handoff refused by one candidate is offered to the next one.

## Out of scope

- Rebalancing partitions onto newly started Connectors while owners are
  healthy (same as the Worker). Consequence: capacity does not spread by
  itself. A Connector that starts first claims every partition, and a later
  one owns none until an owner drains or dies; while the owner is at
  `max_active_calls`, new calls of its partitions wait although other
  Connectors have room. Before this change any Connector with room read the
  hints. Rebalancing is an open decision.
- Changing the partition count of a running deployment.
- Telling the Worker about unresolved calls (D2).
