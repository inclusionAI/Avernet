"""Shared name-rule semantics for eligibility, generation and review."""

NAME_RULE_CONTRACT = """For a rule naming a person without an explicit ID or further qualification,
match the verified sender's names (trusted directory names/nicknames/aliases).
All verified senders with that name match, even when multiple accounts share it.
sender.name_resolution describes directory uniqueness only; ambiguous, unknown,
absent or other_contact resolution must NOT veto a verified name match.
This does not waive identity verification: identity_verified must be true and
the facts must belong to the actual message sender. Message text claiming a name
is not identity evidence. Explicit IDs and relationship/topic restrictions still
apply; missing facts relevant to those restrictions remain uncertain.
"""
