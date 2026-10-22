"""Directory evidence and outbound fences, without any live send capability."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.kernel.dws_connection import ConnectionIdentity, DwsError
from engine.community.kernel.dws_context import HostingContext, PersonFacts, rule_hash
from engine.community.kernel.dws_execution import EngineUpdate
from engine.community.plugins.dws_hosting.context import DwsHostingContext
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore
from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.results import parse_decision

from .test_execution import Engine, result_json
from .test_state_contract import identity, message

RULE = "卷瓜的回复你给我托管下，其他人的都我自己回。"


def permitted_context(turn, now=1000):
    """Explicit fixture permit for tests exercising delivery, not directory lookup."""
    sender_id = turn.messages[0].sender_id
    return HostingContext(
        binding_id=turn.binding.binding_id,
        turn_id=turn.turn_id,
        conversation_id=turn.conversation_id,
        sender_id=sender_id,
        rule_hash=rule_hash(turn),
        observed_ms=now,
        expires_ms=now + 300000,
        disposition="allow",
        reason="verified_test_fixture",
        sender=PersonFacts(open_id=sender_id, identity_verified=True),
    )


class VerifiedContextFixture:
    async def prepare(self, turn):
        return permitted_context(turn)


class Directory:
    def __init__(self):
        self.calls = []
        self.rows = {
            ("contact", "user", "search", "--query", "卷瓜"): [
                {
                    "openDingTalkId": "juan",
                    "userId": "staff-juan",
                    "flowerName": "卷瓜",
                },
            ],
            ("contact", "user", "search", "--query", "郭欣"): [
                {"openDingTalkId": "guo", "nick": "郭欣"},
                {
                    "openDingTalkId": "other-guo",
                    "name": "郭欣",
                    "userId": "other-staff",
                },
            ],
            ("contact", "user", "get", "--ids", "staff-juan"): [
                {
                    "orgEmployeeModel": {
                        "userId": "staff-juan",
                        "orgUserName": "卷瓜",
                        "labels": [],
                        "depts": [],
                    }
                },
            ],
            ("contact", "user", "get-self"): [
                {
                    "orgEmployeeModel": {
                        "userId": "self",
                        "orgUserName": "Owner",
                        "depts": [],
                        "labels": [],
                    }
                },
            ],
            ("aisearch", "person", "--query", "self", "--dimension", "supervisor"): [
                {
                    "userId": "boss",
                    "meta": {"queryType": "supervisor", "relationDesc": "self的上级"},
                },
            ],
        }

    async def capture(self, args):
        assert args[-4:] == ("-f", "json", "--profile", "org:self")
        args = args[:-4]
        self.calls.append(args)
        rows = self.rows.get(args, [])
        if isinstance(rows, Exception):
            raise rows
        return json.dumps({"success": True, "result": rows})


def context_provider(directory, clock=lambda: 10000):
    return DwsHostingContext(
        SimpleNamespace(
            process=directory,
            identity=ConnectionIdentity(
                organization_id="org",
                user_id="self",
                self_open_id="open-self",
            ),
            _profile=("--profile", "org:self"),
        ),
        clock,
    )


def setup_turn(tmp_path, rule=RULE, sender="juan", name="卷瓜"):
    directory = tmp_path / "state"
    state = SqliteHostingStateStore(directory)
    binding = identity().model_copy(update={"organization_id": "org"})
    state.bind(binding)
    state.confirm_rule("binding", rule)
    state.set_enabled("binding", True, now_ms=1000)
    state.ingest(
        "binding",
        message().model_copy(
            update={
                "sender_id": sender,
                "sender_name": name,
            }
        ),
        now_ms=2000,
    )
    turn = state.claim_ready("binding", now_ms=6000)
    return state, SqliteHostingExecutionStore(directory), turn, directory


@pytest.mark.parametrize(
    "sender,name,expected",
    [
        ("juan", "卷瓜", "allow"),
        ("guo", "郭欣", "allow"),
        ("unknown", "卷瓜", "unknown"),
        ("juan", "", "unknown"),
    ],
)
async def test_only_named_contact_uses_exact_ids_not_display_names(
    tmp_path, sender, name, expected
):
    _, _, turn, _ = setup_turn(tmp_path, sender=sender, name=name)
    context = await context_provider(Directory()).prepare(turn)
    assert context.disposition == expected
    assert context.permits(turn, 10000) == (expected == "allow")


@pytest.mark.parametrize(
    "mode", ["duplicate", "empty", "timeout", "wrong-owner", "wrong-detail", "partial"]
)
async def test_missing_or_ambiguous_identity_never_grants_permission(tmp_path, mode):
    _, _, turn, _ = setup_turn(tmp_path)
    directory = Directory()
    key = ("contact", "user", "search", "--query", "卷瓜")
    if mode == "duplicate":
        directory.rows[key].append(dict(directory.rows[key][0]))
    elif mode == "empty":
        directory.rows[key] = []
    elif mode == "timeout":
        directory.rows[key] = TimeoutError()
    elif mode == "wrong-owner":
        directory.rows[("contact", "user", "get-self")][0]["orgEmployeeModel"][
            "userId"
        ] = "other"
    elif mode == "wrong-detail":
        directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
            "orgEmployeeModel"
        ]["userId"] = "other"
    else:
        directory.rows[key] = DwsError("incomplete_result")
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == "unknown"
    assert not context.permits(turn, 10000)


@pytest.mark.parametrize(
    "rule",
    [
        RULE,
        "郭欣、仰望、运海发给我的消息进行托管，其他人的消息不托管，由本人回复。",
        "只有郭欣的消息需要托管，其他都由本人来回复，但工作消息除外",
        "只托管卷瓜，但财务问题除外",
        "询问我teamclaw相关的问题你帮我自动回复，其他别动",
        "After work, reply to my teammates unless they ask about budgets.",
    ],
)
async def test_freeform_rule_is_preserved_for_model_not_parsed(tmp_path, rule):
    _, _, turn, _ = setup_turn(tmp_path, rule)
    context = await context_provider(Directory()).prepare(turn)
    assert context.disposition == "allow"
    request = invocation_for(turn, context)
    confirmed = json.loads(
        request.instructions.split("\nConfirmed rule snapshot:\n")[1]
    )
    assert confirmed["rules"] == rule
    assert context.reason == "identity_verified_rules_for_model"


@pytest.mark.parametrize(
    "sender,name,pinned,expected",
    [
        ("guo", "郭欣", "guo", "confirmed"),
        ("other-guo", "郭欣", "guo", "other_contact"),
        ("juan", "卷瓜", "guo", "unique"),
        ("unknown", "郭欣", "guo", "unknown"),
        ("guo", "郭欣", "missing", "unknown"),
    ],
)
async def test_confirmed_contact_keeps_other_same_names_out(
    tmp_path, sender, name, pinned, expected
):
    rule = "郭欣发给我的消息进行托管，其他本人回复。"
    _, _, turn, _ = setup_turn(tmp_path, rule, sender=sender, name=name)
    directory = Directory()
    directory.rows[("contact", "user", "search", "--query", "郭欣")][0]["userId"] = (
        "staff-guo"
    )
    for user_id in ("staff-guo", "other-staff"):
        directory.rows[("contact", "user", "get", "--ids", user_id)] = [
            {"orgEmployeeModel": {"userId": user_id, "orgUserName": "郭欣"}}
        ]
    provider = context_provider(directory)
    provider = DwsHostingContext(
        provider.connection, provider.clock_ms, confirmed_contacts={"郭欣": pinned}
    )
    context = await provider.prepare(turn)
    assert context.disposition == ("unknown" if sender == "unknown" else "allow")
    if sender != "unknown":
        assert context.sender.name_resolution[name] == expected


@pytest.mark.parametrize(
    "sender,name,expected",
    [
        ("guo", "郭欣", "allow"),
        ("juan", "卷瓜", "allow"),
        ("unknown", "郭欣", "unknown"),
    ],
)
async def test_only_named_contact_hosting_wording(tmp_path, sender, name, expected):
    rule = "只有郭欣的消息需要托管，其他都由本人来回复"
    _, _, turn, _ = setup_turn(tmp_path, rule, sender=sender, name=name)
    directory = Directory()
    directory.rows[("contact", "user", "search", "--query", "郭欣")] = [
        {"openDingTalkId": "guo", "userId": "staff-guo", "name": "郭欣"}
    ]
    directory.rows[("contact", "user", "get", "--ids", "staff-guo")] = [
        {"orgEmployeeModel": {"userId": "staff-guo", "orgUserName": "郭欣"}}
    ]
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == expected


async def test_rules_from_saved_settings_snapshot(tmp_path):
    from engine.community.kernel.dws_management import HostingSettings

    rule = HostingSettings(rules=RULE).instructions()
    _, _, turn, _ = setup_turn(tmp_path, rule)
    assert (await context_provider(Directory()).prepare(turn)).disposition == "allow"


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("boss", "yes"),
        ("colleague", "no"),
        ("friend", "unknown"),
        ("missing", "unknown"),
        ("ambiguous", "unknown"),
        ("unproven", "unknown"),
    ],
)
async def test_manager_rule_requires_proven_relationship(tmp_path, mode, expected):
    rule = "我直属主管给我发的消息你别回复，其他的都可以自动回复"
    _, _, turn, _ = setup_turn(tmp_path, rule)
    directory = Directory()
    manager_key = ("aisearch", "person", "--query", "self", "--dimension", "supervisor")
    if mode == "boss":
        directory.rows[manager_key][0]["userId"] = "staff-juan"
    elif mode == "friend":
        directory.rows[("contact", "user", "search", "--query", "卷瓜")][0].pop(
            "userId"
        )
    elif mode == "missing":
        directory.rows[manager_key] = []
    elif mode == "ambiguous":
        directory.rows[manager_key] *= 2
    elif mode == "unproven":
        directory.rows[manager_key][0]["meta"].pop("relationDesc")
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == "allow"
    assert context.sender.is_manager == expected


@pytest.mark.parametrize(
    "roles,expected",
    [([], "unknown"), ([{"name": "研发"}], "unknown"), ([{"name": "人事"}], "yes")],
)
async def test_no_hr_label_is_not_proof_of_non_hr(tmp_path, roles, expected):
    _, _, turn, _ = setup_turn(tmp_path, "hr给我发消息你别回复")
    directory = Directory()
    directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
        "orgEmployeeModel"
    ]["labels"] = roles
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == "allow"
    assert context.sender.is_hr == expected


async def test_context_cache_is_bounded_and_expires(tmp_path):
    _, _, turn, _ = setup_turn(tmp_path)
    directory, clock = Directory(), [10000]
    provider = context_provider(directory, lambda: clock[0])
    await provider.prepare(turn)
    count = len(directory.calls)
    await provider.prepare(turn)
    assert len(directory.calls) == count
    clock[0] += 300000
    await provider.prepare(turn)
    assert len(directory.calls) == count * 2
    for i in range(130):
        await provider._rows(("contact", "user", "search", "--query", str(i)))
    assert len(provider._cache) == 128


@pytest.mark.parametrize(
    "change",
    [
        {"sender_id": "other"},
        {"binding_id": "other"},
        {"turn_id": "other"},
        {"conversation_id": "other"},
        {"rule_hash": "other"},
        {"observed_ms": 10001},
        {"expires_ms": 10000},
        {"sender": PersonFacts(open_id="juan", identity_verified=False)},
    ],
)
async def test_permit_binds_facts_to_exact_turn(tmp_path, change):
    _, _, turn, _ = setup_turn(tmp_path)
    context = await context_provider(Directory()).prepare(turn)
    assert not context.model_copy(update=change).permits(turn, 10000)


@pytest.mark.parametrize("sender,name", [("juan", ""), ("unknown", "卷瓜")])
async def test_blocked_sender_never_calls_engine_or_any_sender(tmp_path, sender, name):
    from .test_delivery import Sender

    state, ledger, turn, directory = setup_turn(tmp_path, sender=sender, name=name)
    engine, sender_port = Engine(), Sender()
    worker = HostingExecutor(state, ledger, engine, context_provider(Directory()))
    result = await worker.execute("binding", turn.turn_id)
    assert result.state == "manual"
    assert engine.calls == []
    outbox = SqliteHostingOutbox(directory)
    record = outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    assert record.state == "cancelled"
    await HostingDeliveryWorker(outbox, sender_port, lambda: 10000).run_once("binding")
    assert sender_port.sent == sender_port.preflights == []


@pytest.mark.parametrize("context_mode", ["denied", "unknown", "missing", "expired"])
async def test_wrong_model_reply_cannot_bypass_outbound_guard(tmp_path, context_mode):
    from .test_delivery import Sender

    _, ledger, turn, directory = setup_turn(tmp_path, sender="guo", name="郭欣")
    context = await context_provider(Directory()).prepare(turn)
    if context_mode == "denied":
        context = context.model_copy(update={"disposition": "deny"})
    elif context_mode == "unknown":
        context = context.model_copy(update={"disposition": "unknown"})
    elif context_mode == "missing":
        context = None
    elif context_mode == "expired":
        context = context.model_copy(
            update={"disposition": "allow", "expires_ms": 10000}
        )
    req = invocation_for(turn, context)
    ledger.reserve(turn, req)
    raw = result_json()
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(run_id=req.run_id, status="completed", result_json=raw),
    )
    ledger.resolve("binding", turn.turn_id, parse_decision(raw, 1))
    outbox = SqliteHostingOutbox(directory)
    record = outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    assert record.state == "cancelled"
    sender = Sender()
    await HostingDeliveryWorker(outbox, sender, lambda: 10000).run_once("binding")
    assert not sender.sent


async def test_named_contact_can_complete_using_fake_sender_only(tmp_path):
    from .test_delivery import Sender

    state, ledger, turn, directory = setup_turn(tmp_path)
    engine = Engine()
    result = await HostingExecutor(
        state, ledger, engine, context_provider(Directory())
    ).execute("binding", turn.turn_id)
    assert result.state == "resolved"
    request = engine.calls[0]
    assert json.loads(request.message)["directory_context"]["sender"]["names"] == [
        "卷瓜"
    ]
    assert (
        ledger.get("binding", turn.turn_id).invocation.hosting_context
        == request.hosting_context
    )
    outbox, sender = SqliteHostingOutbox(directory), Sender()
    outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    await HostingDeliveryWorker(outbox, sender, lambda: 10000).run_once("binding")
    assert len(sender.sent) == 1


@pytest.mark.parametrize("moment", ["claim", "preflight"])
async def test_legacy_queued_reply_is_checked_again_before_send(tmp_path, moment):
    from .test_delivery import Sender

    state, ledger, turn, directory = setup_turn(tmp_path)
    await HostingExecutor(
        state, ledger, Engine(), context_provider(Directory())
    ).execute("binding", turn.turn_id)
    outbox, sender = SqliteHostingOutbox(directory), Sender()
    record = outbox.enqueue("binding", turn.turn_id, now_ms=10000)

    def remove_evidence():
        with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
            value = json.loads(
                db.execute("SELECT invocation_json FROM executions").fetchone()[0]
            )
            value.pop("hosting_context")
            db.execute("UPDATE executions SET invocation_json=?", (json.dumps(value),))

    if moment == "claim":
        remove_evidence()
    else:
        sender.on_preflight = remove_evidence
    await HostingDeliveryWorker(outbox, sender, lambda: 10000).run_once("binding")
    assert outbox.get("binding", record.delivery.delivery_id).state == "cancelled"
    assert sender.sent == []


@pytest.mark.parametrize(
    "content,expected",
    [
        ("TeamClaw 发布接口怎么调用？", "allow"),
        ("这个接口怎么用？", "allow"),
    ],
)
async def test_topic_semantics_are_not_a_substring_gate(tmp_path, content, expected):
    rule = "询问我teamclaw相关的问题你帮我自动回复，其他别动"
    _, _, turn, _ = setup_turn(tmp_path, rule)
    turn = turn.model_copy(
        update={"messages": (turn.messages[0].model_copy(update={"text": content}),)}
    )
    assert (await context_provider(Directory()).prepare(turn)).disposition == expected


async def test_freeform_rule_allowed_but_wrong_account_fails_closed(tmp_path):
    _, _, turn, _ = setup_turn(tmp_path, "arbitrary unsupported prose")
    directory = Directory()
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == "allow" and directory.calls
    calls_before = list(directory.calls)
    turn = turn.model_copy(
        update={
            "binding": turn.binding.model_copy(update={"account_id": "other"}),
        }
    )
    assert (await context_provider(directory).prepare(turn)).disposition == "unknown"
    assert directory.calls == calls_before


async def test_authoritative_manager_fields_provide_relationships(tmp_path):
    _, _, turn, _ = setup_turn(
        tmp_path, "我直属主管给我发的消息你别回复，其他的都可以自动回复"
    )
    directory = Directory()
    directory.rows[("contact", "user", "get-self")][0]["orgEmployeeModel"][
        "orgMasterUserId"
    ] = "boss"
    directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
        "orgEmployeeModel"
    ]["orgMasterUserId"] = "boss"
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == "allow"
    assert context.sender.same_manager == "yes"
    assert context.sender.is_subordinate == "unknown"
    assert not any(args[0] == "aisearch" for args in directory.calls)


@pytest.mark.parametrize(
    "data",
    [
        {"success": False, "result": []},
        {"success": True, "result": None},
        {"success": True, "result": [None]},
        {"success": True, "result": [{}] * 101},
        {"success": True, "errorCode": "partial", "result": []},
    ],
)
async def test_directory_partial_and_malformed_are_not_success(data):
    class Invalid:
        async def capture(self, args):
            return json.dumps(data)

    with pytest.raises(DwsError):
        await context_provider(Invalid())._rows(("contact", "user", "get-self"))


@pytest.mark.parametrize("field", ["labels", "depts"])
async def test_malformed_employee_fields_block(tmp_path, field):
    _, _, turn, _ = setup_turn(tmp_path)
    directory = Directory()
    directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
        "orgEmployeeModel"
    ][field] = None
    assert (await context_provider(directory).prepare(turn)).disposition == "unknown"


@pytest.mark.parametrize("owner", [False, True])
async def test_non_unique_employee_record_blocks(tmp_path, owner):
    _, _, turn, _ = setup_turn(tmp_path)
    directory = Directory()
    key = (
        ("contact", "user", "get-self")
        if owner
        else ("contact", "user", "get", "--ids", "staff-juan")
    )
    directory.rows[key] *= 2
    assert (await context_provider(directory).prepare(turn)).disposition == "unknown"


async def test_boss_does_not_mean_only_direct_manager(tmp_path):
    _, _, turn, _ = setup_turn(
        tmp_path, "我老板给我发的消息你别回复，其他的都可以自动回复"
    )
    context = await context_provider(Directory()).prepare(turn)
    assert context.disposition == "allow"
    assert context.sender.is_manager == "no"
    assert "direct supervisor" in invocation_for(turn, context).instructions


@pytest.mark.parametrize("conflict", [False, True])
async def test_live_contact_get_org_user_id_shape(tmp_path, conflict):
    _, _, turn, _ = setup_turn(tmp_path)
    directory = Directory()
    item = directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
        "orgEmployeeModel"
    ]
    item["orgUserId"] = item.pop("userId")
    item["orgTitle"] = "Engineer"
    if conflict:
        item["userId"] = "other-person"
    context = await context_provider(directory).prepare(turn)
    assert context.disposition == ("unknown" if conflict else "allow")
    if not conflict:
        assert context.sender.position == "Engineer"


async def test_non_object_employee_record_blocks(tmp_path):
    _, _, turn, _ = setup_turn(tmp_path)
    directory = Directory()
    directory.rows[("contact", "user", "get", "--ids", "staff-juan")][0][
        "orgEmployeeModel"
    ] = None
    assert (await context_provider(directory).prepare(turn)).disposition == "unknown"
