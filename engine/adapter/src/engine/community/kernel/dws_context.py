"""Verified identities, relationship facts and a turn-bound identity fence.

An allow disposition establishes identity eligibility, not a match to the
owner's natural-language rules. Generation and independent review judge rules.
"""

import hashlib
from typing import Annotated, Literal

from pydantic import Field

from engine.community.kernel.dws_hosting import Identifier, Turn, Value


class PersonFacts(Value):
    open_id: str = ""
    user_id: str = ""
    names: tuple[str, ...] = ()
    name_resolution: dict[
        str, Literal["unique", "confirmed", "other_contact", "ambiguous", "unknown"]
    ] = {}
    departments: tuple[str, ...] = ()
    position: str = ""
    roles: tuple[str, ...] = ()
    manager_user_id: str = ""
    manager_name: str = ""
    identity_verified: bool = False
    # Missing labels/manager fields are not negative membership evidence.
    is_hr: Literal["yes", "no", "unknown"] = "unknown"
    # Direct manager only; not proof about the wider leadership chain.
    is_manager: Literal["yes", "no", "unknown"] = "unknown"
    is_subordinate: Literal["yes", "no", "unknown"] = "unknown"
    same_manager: Literal["yes", "no", "unknown"] = "unknown"
    sources: tuple[str, ...] = ()


class HostingContext(Value):
    version: Literal[1] = 1
    binding_id: Identifier
    turn_id: Identifier
    conversation_id: Identifier
    sender_id: Identifier
    rule_hash: str
    observed_ms: Annotated[int, Field(ge=0)]
    expires_ms: Annotated[int, Field(ge=0)]
    disposition: Literal["allow", "deny", "unknown"]
    reason: str
    owner: PersonFacts = PersonFacts()
    sender: PersonFacts = PersonFacts()

    def permits(self, turn: Turn, now_ms: int) -> bool:
        return (
            self.disposition == "allow"
            and self.sender.identity_verified
            and self.sender.open_id == self.sender_id
            and self.binding_id == turn.binding.binding_id
            and self.turn_id == turn.turn_id
            and self.conversation_id == turn.conversation_id
            and self.rule_hash == rule_hash(turn)
            and {m.sender_id for m in turn.messages} == {self.sender_id}
            and self.observed_ms <= now_ms < self.expires_ms
        )


def rule_hash(turn: Turn) -> str:
    return hashlib.sha256(f"{turn.rule_version}\n{turn.rule_text}".encode()).hexdigest()
