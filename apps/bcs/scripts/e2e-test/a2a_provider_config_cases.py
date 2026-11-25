"""Provider default-owner lifecycle over real HTTP, without direct storage writes."""
import copy
import uuid
from urllib.parse import urlencode


def exercise(story, provider, original):
    admin = provider["provider_admin_token"]
    path = "/providers/" + provider["provider_id"]

    def read():
        return story.request("GET", path, token=admin)

    def patch(body, status=200):
        return story.request("PATCH", path, body, token=admin, status=status)

    initial = read()
    assert initial["a2a"]["default_owner"] == story.owner
    # Empty object, omitted object and null object preserve the setting.
    for body in [{"a2a": {}}, {"name": initial["name"]}, {"a2a": None}]:
        patch(body)
        assert read()["a2a"]["default_owner"] == story.owner

    # Configuration validation must precede the metadata write.
    for owner in ["", " ", " bad", "human_bad", "bad\nowner", "x" * 129]:
        patch({"name": "Must not commit", "a2a": {"default_owner": owner}}, 400)
        current = read()
        assert current["name"] == initial["name"]
        assert current["a2a"]["default_owner"] == story.owner
        assert current["webhook_url"] == initial["webhook_url"]
        assert current["protocol_version"] == initial["protocol_version"]

    # Replace, then restore a known Human identity before admission.
    patch({"a2a": {"default_owner": "e2e-owner-" + uuid.uuid4().hex}})
    assert read()["a2a"]["default_owner"].startswith("e2e-owner-")
    patch({"a2a": {"default_owner": story.owner}})
    for mode in ["omitted", "null", "empty"]:
        card = copy.deepcopy(original)
        card["name"] = "Default owner " + mode
        body = {"provider_bot_ref": "default-" + uuid.uuid4().hex, "agent_card": card}
        if mode != "omitted":
            body["owners"] = None if mode == "null" else []
        created = story.request("POST", "/a2a/agents", body, token=admin, status=201)
        bindings = story.request("GET", path + "/bots", token=admin)
        bot = next(item for item in bindings["items"] if item["bot_uuid"] == created["bot_id"])
        story.bots.append((provider, bot))
        found = story.request("GET", "/a2a/agents?" + urlencode({"provider_bot_ref": body["provider_bot_ref"]}), token=admin)
        assert found["items"][0]["bot_id"] == created["bot_id"]

    # Clear rather than replacing the whole Provider configuration. Later empty
    # owners must fail, while the existing story still registers explicit owners.
    patch({"a2a": {"default_owner": None}})
    final = read()
    assert final["a2a"]["default_owner"] is None
    assert final["webhook_url"] == initial["webhook_url"]
    assert final["protocol_version"] == initial["protocol_version"]
    for owners in [None, []]:
        ref = "no-default-" + uuid.uuid4().hex
        result = story.request("POST", "/a2a/agents", {
            "provider_bot_ref": ref, "owners": owners, "agent_card": original,
        }, token=admin, status=422)
        assert result["error"]["code"] == "INVALID_OWNER"
        assert story.request("GET", "/a2a/agents?" + urlencode({"provider_bot_ref": ref}), token=admin)["items"] == []
