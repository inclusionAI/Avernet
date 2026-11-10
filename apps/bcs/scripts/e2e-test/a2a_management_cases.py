"""Public Card lifecycle and rejected-admission cases against the real server."""
import copy
import uuid
from urllib.parse import urlencode

SOURCE = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v2.md"


def exercise(story, provider, original):
    admin = provider["provider_admin_token"]
    card = copy.deepcopy(original)
    card["name"] = "Managed-" + uuid.uuid4().hex
    card["provider"] = {"organization": "E2E", "url": "https://example.test"}
    card["documentationUrl"] = "https://example.test/docs"
    card["iconUrl"] = "https://example.test/icon.png"
    card["futureData"] = {"nested": [1, "preserved"]}
    card["capabilities"]["extensions"] = [{"uri": SOURCE, "required": False,
        "params": {"party": "first_party", "future": True}}]

    reference = "managed-" + uuid.uuid4().hex

    def register(value, status=201, ref=None):
        return story.request("POST", "/a2a/agents", {"provider_bot_ref": ref or reference, "owners": [story.owner], "agent_card": value},
                             token=admin, status=status)

    created = register(card)
    assert created["agent_card"] == card
    bindings = story.request("GET", "/providers/" + provider["provider_id"] + "/bots", token=admin)
    story.bots.append((provider, next(item for item in bindings["items"] if item["bot_uuid"] == created["bot_id"])))
    assert register(card, 409)["error"]["code"] == "PROVIDER_BOT_REF_CONFLICT"
    other = register(card, ref=reference + "-other")
    assert other["bot_id"] != created["bot_id"]
    bindings = story.request("GET", "/providers/" + provider["provider_id"] + "/bots", token=admin)
    story.bots.append((provider, next(item for item in bindings["items"] if item["bot_uuid"] == other["bot_id"])))
    control = "/a2a/agents/" + created["bot_id"] + "/agent-card"
    changed = copy.deepcopy(card)
    changed["name"] += "-renamed"
    changed["capabilities"]["extensions"][0]["params"]["party"] = "third_party"
    updated = story.request("PUT", control, {"agent_card": changed}, token=admin)
    assert updated["bot_id"] == created["bot_id"] and updated["agent_card"] == changed
    fetched = story.request("GET", control, token=admin)
    assert fetched == updated
    found = story.request("GET", "/a2a/agents?" + urlencode({"provider_bot_ref": reference}), token=admin)
    assert len(found["items"]) == 1 and found["items"][0]["bot_id"] == created["bot_id"]
    story.request("PUT", control, {"agent_name": changed["name"], "agent_card": changed}, token=admin, status=400)
    story.request("GET", control + "?bot_name=legacy", token=admin, status=400)

    # Each case isolates one invalid declaration; none may create a Bot.
    for key, value, code in [
        ("name", " ", "INVALID_CARD"), ("description", 42, "INVALID_CARD"),
        ("version", "", "INVALID_CARD"), ("skills", {}, "INVALID_CARD"),
        ("supportedInterfaces", [], "INVALID_CARD"),
        ("defaultInputModes", ["image/png"], "CARD_CONTENT_UNSUPPORTED"),
        ("defaultOutputModes", [42], "INVALID_CARD"),
    ]:
        bad = copy.deepcopy(card)
        bad["name"] = "Rejected-" + uuid.uuid4().hex
        bad[key] = value
        response = register(bad, 422)
        assert response["error"]["code"] == code
        assert response["error"]["request_id"] and response["error"]["details"]
    bad = copy.deepcopy(card)
    bad["capabilities"]["extensions"][0]["params"]["party"] = "unknown_party"
    assert register(bad, 422)["error"]["code"] == "INVALID_CARD"
    for key, value in [("documentationUrl", "relative/path"), ("iconUrl", 7),
                       ("provider", {"organization": "E2E", "url": "relative"})]:
        bad = copy.deepcopy(card)
        bad[key] = value
        assert register(bad, 422)["error"]["code"] == "INVALID_CARD"
    bad = copy.deepcopy(card)
    bad["skills"] = [copy.deepcopy(card["skills"][0])] * 2
    assert register(bad, 422)["error"]["code"] == "INVALID_CARD"
    bad = copy.deepcopy(card)
    bad["capabilities"]["streaming"] = "yes"
    assert register(bad, 422)["error"]["code"] == "INVALID_CARD"
    bad = copy.deepcopy(card)
    bad["securitySchemes"] = {"auth": {"httpAuthSecurityScheme": {"scheme": "bearer"}}}
    bad["securityRequirements"] = [{"schemes": {"auth": {"list": []}}}]
    assert register(bad, 422)["error"]["code"] == "CARD_AUTH_UNSUPPORTED"
    bad = copy.deepcopy(card)
    bad["capabilities"]["extensions"] = [{"uri": "https://example.test/required", "required": True}]
    assert register(bad, 422)["error"]["code"] == "CARD_EXTENSION_UNSUPPORTED"
    bad = copy.deepcopy(card)
    bad["supportedInterfaces"][0]["protocolVersion"] = "9.9"
    assert register(bad, 422)["error"]["code"] == "CARD_INTERFACE_UNSUPPORTED"
    result = story.request("POST", "/a2a/agents", {"provider_bot_ref": reference + "-invalid", "owners": [], "agent_card": card}, token=admin, status=422)
    assert result["error"]["code"] == "INVALID_OWNER"
    result = story.request("POST", "/a2a/agents", {"provider_bot_ref": reference + "-invalid", "owners": [story.owner], "agent_card": card,
        "agent_card_url": story.peer.base + "/agent-card.json"}, token=admin, status=422)
    assert result["error"]["code"] == "CARD_SOURCE_INVALID"
