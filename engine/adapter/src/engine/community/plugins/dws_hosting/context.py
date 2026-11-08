"""Bounded, read-only DWS directory enrichment using the authorized profile."""

from collections.abc import Callable

from engine.community.kernel.dws_connection import DwsError
from engine.community.kernel.dws_context import HostingContext, PersonFacts, rule_hash
from engine.community.kernel.dws_hosting import Turn
from engine.community.plugins.dws_hosting.client import DwsCliConnection
from engine.community.plugins.dws_hosting import wire


def names(row: dict) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value.strip()
            for key in ("name", "nick", "flowerName", "orgUserName")
            if isinstance(value := row.get(key), str) and 0 < len(value.strip()) <= 200
        )
    )


def text(row: dict, key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) and len(value) <= 512 else ""


def employee(row: dict, base: PersonFacts) -> PersonFacts:
    item = row.get("orgEmployeeModel", {})
    if not isinstance(item, dict):
        raise DwsError("directory_identity_mismatch")
    # COSEC: get-self and get use different ID keys. Every supplied ID must
    # agree with the exact contact-search identity, never merely the name.
    ids = {
        item[key]
        for key in ("userId", "orgUserId")
        if isinstance(item.get(key), str) and item[key]
    }
    if not base.user_id or ids != {base.user_id}:
        raise DwsError("directory_identity_mismatch")
    depts, labels = item.get("depts", []), item.get("labels", [])
    if not isinstance(depts, list) or not isinstance(labels, list):
        raise DwsError("invalid_directory")
    roles = tuple(
        text(label, "name") or text(label, "labelName")
        for label in labels
        if isinstance(label, dict)
    )
    return base.model_copy(
        update={
            "names": tuple(dict.fromkeys((*base.names, *names(item)))),
            "departments": tuple(
                text(d, "deptName") for d in depts if isinstance(d, dict)
            ),
            "position": text(item, "position")
            or text(item, "orgTitle")
            or base.position,
            "roles": roles,
            "manager_user_id": text(item, "orgMasterUserId"),
            "manager_name": text(item, "orgMasterDisplayName"),
            # COSEC: absence of a label does not establish non-HR membership.
            "is_hr": "yes"
            if any(r.casefold() in ("hr", "人事", "人力资源") for r in roles)
            else "unknown",
            "sources": (*base.sources, "contact.user.get"),
        }
    )


class DwsHostingContext:
    def __init__(
        self,
        connection: DwsCliConnection,
        clock_ms: Callable[[], int],
        *,
        confirmed_contacts: dict[str, str] | None = None,
    ):
        self.connection, self.clock_ms = connection, clock_ms
        self.confirmed_contacts = dict(confirmed_contacts or {})
        self._cache: dict[tuple[str, ...], tuple[int, list[dict]]] = {}

    async def _rows(self, args: tuple[str, ...]) -> list[dict]:
        now = self.clock_ms()
        cached = self._cache.get(args)
        if cached and 0 <= now - cached[0] < 300_000:
            return cached[1]
        raw = await self.connection.process.capture(
            (*args, "-f", "json", *self.connection._profile)
        )
        data = wire.payload(raw)
        rows = data.get("result")
        if (
            data.get("success") is not True
            or data.get("errorCode") not in (None, "", 0)
            or not isinstance(rows, list)
            or len(rows) > 100
            or any(not isinstance(row, dict) for row in rows)
        ):
            raise DwsError("directory_incomplete")
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[args] = now, rows
        return rows

    async def _sender(self, turn: Turn) -> PersonFacts:
        ids = {m.sender_id for m in turn.messages}
        display = {
            m.sender_name.strip() for m in turn.messages if m.sender_name.strip()
        }
        if len(ids) != 1 or len(display) != 1:
            raise DwsError("sender_identity_unknown")
        sender_id = next(iter(ids))
        rows = await self._rows(
            ("contact", "user", "search", "--query", next(iter(display)))
        )
        matches = [r for r in rows if r.get("openDingTalkId") == sender_id]
        if len(matches) != 1:
            raise DwsError("sender_identity_unknown")
        row = matches[0]
        facts = PersonFacts(
            open_id=sender_id,
            user_id=text(row, "userId"),
            names=names(row),
            position=text(row, "title"),
            identity_verified=True,
            sources=("contact.user.search:exact_open_id",),
        )
        if facts.user_id:
            details = await self._rows(
                ("contact", "user", "get", "--ids", facts.user_id)
            )
            if len(details) != 1:
                raise DwsError("directory_incomplete")
            facts = employee(details[0], facts)
        resolution = {}
        for name in facts.names:
            candidates = {
                text(r, "openDingTalkId")
                for r in rows
                if name.casefold() in {n.casefold() for n in names(r)}
            }
            confirmed = self.confirmed_contacts.get(name)
            # COSEC: pins only disambiguate live directory identities. They are
            # not a hosting whitelist, and never prove a missing directory match.
            if confirmed:
                resolution[name] = (
                    ("confirmed" if confirmed == sender_id else "other_contact")
                    if confirmed in candidates
                    else "unknown"
                )
            elif candidates == {sender_id}:
                resolution[name] = "unique"
            else:
                resolution[name] = "ambiguous" if sender_id in candidates else "unknown"
        return facts.model_copy(update={"name_resolution": resolution})

    async def _owner(self) -> PersonFacts:
        rows = await self._rows(("contact", "user", "get-self"))
        if len(rows) != 1:
            raise DwsError("directory_incomplete")
        return employee(
            rows[0],
            PersonFacts(
                user_id=self.connection.identity.user_id,
                open_id=self.connection.identity.self_open_id,
                identity_verified=True,
                sources=("contact.user.get-self",),
            ),
        )

    async def _manager(self, owner: PersonFacts) -> str:
        if owner.manager_user_id:
            return owner.manager_user_id
        rows = await self._rows(
            (
                "aisearch",
                "person",
                "--query",
                owner.user_id,
                "--dimension",
                "supervisor",
            )
        )
        # A search candidate is not automatically an organization edge.
        if len(rows) != 1:
            return ""
        row = rows[0]
        meta = row.get("meta")
        if (
            not isinstance(meta, dict)
            or meta.get("queryType") != "supervisor"
            or meta.get("relationDesc") != f"{owner.user_id}的上级"
        ):
            return ""
        return text(row, "userId")

    async def prepare(self, turn: Turn) -> HostingContext:
        now = self.clock_ms()
        owner, sender = PersonFacts(), PersonFacts()
        disposition, reason = "unknown", "directory_unavailable_or_unverified"
        try:
            if (turn.binding.organization_id, turn.binding.account_id) != (
                self.connection.identity.organization_id,
                self.connection.identity.user_id,
            ):
                raise DwsError("context_account_mismatch")
            sender = await self._sender(turn)
            owner = await self._owner()
            # Relationship enrichment is optional, not a rule parser or permission
            # decision. A failed lookup remains unknown for the model to consider.
            try:
                manager = await self._manager(owner)
            except (DwsError, TimeoutError, ValueError):
                manager = ""
            sender = sender.model_copy(
                update={
                    "is_manager": ("yes" if sender.user_id == manager else "no")
                    if manager and sender.user_id
                    else "unknown",
                    "is_subordinate": "yes"
                    if sender.manager_user_id == owner.user_id
                    else "unknown",
                    "same_manager": "yes"
                    if manager and sender.manager_user_id == manager
                    else "unknown",
                    "sources": (*sender.sources, "owner.supervisor")
                    if manager
                    else sender.sources,
                }
            )
            disposition, reason = "allow", "identity_verified_rules_for_model"
        except (DwsError, TimeoutError, ValueError):
            disposition, reason = "unknown", "directory_unavailable_or_unverified"
        return HostingContext(
            binding_id=turn.binding.binding_id,
            turn_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            sender_id=turn.messages[0].sender_id,
            rule_hash=rule_hash(turn),
            observed_ms=now,
            expires_ms=now + 300_000,
            disposition=disposition,
            reason=reason,
            owner=owner,
            sender=sender,
        )
