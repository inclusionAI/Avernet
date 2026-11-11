#!/usr/bin/env python3
"""Exercise Fuse v2 through a production BCS process and an HTTP catalog peer.

All BCS mutations use public HTTP routes. The peer records the wire contract
and injects catalog failures; the BCS process inherits LLVM_PROFILE_FILE.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid


def eventually(check, description, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"Timed out: {description}")


class Catalog:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.acknowledged = set()
        self.cards = {}
        self.failures = {}
        self.search = {"items": [], "status": "ok", "reasons": []}
        self.credential = uuid.uuid4().hex

    def matching(self, method, path, start=0):
        with self.lock:
            return [body for verb, url, body in self.calls[start:] if (verb, url) == (method, path)]

    def count(self):
        with self.lock:
            return len(self.calls)

    def deleted(self, path, start):
        with self.lock:
            return any(index in self.acknowledged and (verb, url) == ("DELETE", path)
                       for index, (verb, url, _) in enumerate(self.calls) if index >= start)

    def card(self, identity):
        with self.lock:
            return self.cards.get(identity)

    def fail(self, path, mode):
        with self.lock:
            self.failures[path] = mode

    def results(self, items, degraded=False):
        with self.lock:
            self.search = {"items": items, "status": "degraded" if degraded else "ok",
                           "reasons": ["reranker_unavailable"] if degraded else []}

    @contextmanager
    def serve(self):
        catalog = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
                path = urllib.parse.unquote(self.path)
                with catalog.lock:
                    catalog.calls.append((self.command, path, body))
                    mode = catalog.failures.get(path)
                    if self.headers.get("Authorization") != "Bearer " + catalog.credential:
                        status, response = 401, {"detail": "missing catalog credential"}
                    elif mode == "unavailable":
                        status, response = 503, {"detail": "catalog unavailable"}
                    elif path == "/api/v2/search" and self.command == "POST":
                        status, response = 200, catalog.search
                    elif path.startswith("/api/v2/agent-cards/") and self.command in ("PUT", "DELETE"):
                        identity = path.removeprefix("/api/v2/agent-cards/")
                        accepted = mode != "rejected"
                        if accepted:
                            catalog.acknowledged.add(len(catalog.calls) - 1)
                        if self.command == "PUT":
                            if accepted:
                                catalog.cards[identity] = body
                            response = {"saved": accepted, "card_version": 1}
                        else:
                            if accepted:
                                catalog.cards.pop(identity, None)
                            response = {"deleted": accepted}
                        status = 202
                    else:
                        status, response = 404, {"detail": "unknown catalog route"}
                    payload = json.dumps(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_PUT = do_DELETE = do_POST = handle_request

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                thread.join()


class Story:
    def __init__(self, base, catalog):
        self.base, self.catalog = base, catalog
        self.owner = "fusecards" + uuid.uuid4().hex
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.checks = 0

    def request(self, method, path, body=None, *, owner=None, token=None):
        headers = {"Content-Type": "application/json", "X-Mock-User-Id": owner or self.owner,
                   "X-BCS-Service-Key": "fuse-card-local-story"}
        if token:
            headers["X-BCS-Bot-Token"] = token
        request = urllib.request.Request(self.base + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read()
        assert code in (200, 201), f"{method} {path.split('?', 1)[0]} returned HTTP {code}"
        self.checks += 1
        return json.loads(raw) if raw else {}

    def register(self, name, owner=None):
        self.request("POST", "/me/ensure-human", {}, owner=owner)
        token = self.request("GET", "/register/token", owner=owner)["token"]
        query = urllib.parse.urlencode({"token": token, "bot-name": name})
        result = self.request("POST", "/register?" + query, {}, owner=owner)
        identity = result["bot_uuid"]
        card = self.wait_card(identity, lambda card: card["card"]["name"] == name)
        assert card["card"]["skills"] == [] and card["card"]["domains"] == []
        return identity, result["bot_token"]

    def wait_card(self, identity, check):
        def current():
            card = self.catalog.card(identity)
            return card if card and check(card) else None
        self.checks += 1
        return eventually(current, "published Card " + identity)

    def removed(self, identity, start):
        path = "/api/v2/agent-cards/" + identity
        eventually(lambda: self.catalog.deleted(path, start) and
                   self.catalog.card(identity) is None, "deleted Card " + identity)
        self.checks += 1

    def profile(self, identity, **values):
        result = self.request("POST", "/admin/bots/onboard", {"bot_id": identity, **values})
        assert result["onboarded"] is True

    def search(self, caller, query="semantic invoice request"):
        params = urllib.parse.urlencode({"q": query, "current_bot_uuid": caller, "cooperatable_only": "true"})
        start = self.catalog.count()
        result = self.request("GET", "/actors/search?" + params)
        requests = self.catalog.matching("POST", "/api/v2/search", start)
        assert requests == [{"query": query, "limit": 20, "filters": {"resource_type": ["agent"]}}]
        assert "items" not in result
        return result

    def run(self):
        target, token = self.register("Card target")
        caller, auth = self.register("Card caller", self.owner + "caller")
        stranger, _ = self.register("Card stranger", self.owner + "stranger")
        self.profile(target, name="Card target", summary="Invoices", domains=["billing"],
                     skills=[{"name": "invoice", "description": "Invoice lookup"}])
        card = self.wait_card(target, lambda c: c["card"]["description"] == "Invoices\nbilling")
        assert card["resource_type"] == "agent"
        assert card["card"]["skills"][0]["tags"] == ["billing"]
        assert "visibility" not in card["card"] and token not in json.dumps(card)
        # Every bot/user visibility combination is publishable.
        for visibility in ("public", "protected", "private"):
            self.request("PUT", f"/bots/{target}/visibility", {"visibility": visibility})
            for user_visibility in ("public", "protected", "private"):
                self.request("POST", f"/admin/bots/{target}/ensure", {
                    "name": "Card target", "user_visibility": user_visibility})
                self.wait_card(target, lambda c: c["labels"] == {
                    "visibility": [visibility], "user_visibility": [user_visibility]})
        self.profile(target, name="Card target", summary="Updated invoices", domains=["finance"],
                     skills=[{"name": "payment"}])
        self.wait_card(target, lambda c: c["card"]["description"] == "Updated invoices\nfinance" and
                       c["card"]["skills"][0]["name"] == "payment")

        # Friendship survives a subsequent private visibility change.
        self.request("PUT", f"/bots/{target}/visibility", {"visibility": "public"})
        self.request("POST", "/friends/request", {"from_bot": caller, "to_bot": target},
                     owner=self.owner + "caller", token=auth)
        self.request("PUT", f"/bots/{target}/visibility", {"visibility": "private"})
        self.wait_card(target, lambda c: c["labels"]["visibility"] == ["private"])
        item = {"id": target, "name": "Fuse display name", "summary": "Invoice result",
                "score": 0.9, "score_source": "reranker"}
        self.catalog.results([item])
        result = self.search(caller)
        assert result["bots"][0]["bot_uuid"] == target
        assert result["bots"][0]["capabilities"]["name"] == "Card target"
        assert result["bots"][0]["score"] == 0.9 and result["bots"][0]["short_profile"] == "Invoice result"
        assert isinstance(result["bots"][0]["tags"], dict)
        self.catalog.results([{**item, "score": 0.02, "score_source": "rrf"}], degraded=True)
        for actor, expected in ((caller, [target]), (stranger, []), (target, [])):
            result = self.search(actor)
            assert [bot["bot_uuid"] for bot in result["bots"]] == expected
            assert result["context"]["recommend_response"]["recommendations"][0]["worker_id"] == target
            if expected:
                assert result["bots"][0]["score"] == 0.02
        self.catalog.results([{**item, "score": 0.02}])
        assert self.search(caller)["bots"] == []
        self.catalog.fail("/api/v2/search", "unavailable")
        fallback = self.search(caller, "Card target")
        assert target in [bot["bot_uuid"] for bot in fallback["bots"]]
        assert fallback["context"]["recommend_response"] is None
        self.catalog.fail("/api/v2/search", None)

        # Group publication follows resource visibility, not conversation status.
        # The public Group API requires its member Bots to be public too.
        self.request("PUT", f"/bots/{target}/visibility", {"visibility": "public"})
        group = self.request("POST", "/groups", {"driver_bot": target,
            "participants": [{"bot_uuid": target}], "label": "Private group", "visibility": "private"})["id"]
        self.removed(group, 0)
        assert not self.catalog.matching("PUT", "/api/v2/agent-cards/" + group)
        self.request("PATCH", f"/groups/{group}", {"visibility": "public", "name": "Billing group", "context": "Invoices"})
        card = self.wait_card(group, lambda c: c["card"]["name"] == "Billing group")
        assert card == {"resource_type": "group", "labels": {"visibility": ["public"]},
                        "card": {"groupId": group, "name": "Billing group", "description": "Invoices", "skills": []}}
        self.request("PATCH", f"/groups/{group}", {"context": ""})
        self.wait_card(group, lambda c: c["card"]["description"] == "")
        start = self.catalog.count()
        self.request("PATCH", f"/groups/{group}", {"visibility": "private"})
        self.removed(group, start)

        # Negative acknowledgements and outages must retry, then stop at the budget.
        path = "/api/v2/agent-cards/" + target
        self.catalog.fail(path, "rejected")
        start = self.catalog.count()
        self.profile(target, name="Card target", summary="Retry profile")
        eventually(lambda: len(self.catalog.matching("PUT", path, start)) >= 10, "ten failed PUT attempts")
        # Longer than this configuration's maximum per-attempt backoff (320 ms).
        time.sleep(0.7)
        assert len(self.catalog.matching("PUT", path, start)) == 10
        assert self.catalog.card(target)["card"]["description"] == "Updated invoices\nfinance"
        self.catalog.fail(path, None)
        self.profile(target, name="Card target", summary="Recovered profile")
        self.wait_card(target, lambda c: c["card"]["description"] == "Recovered profile\nfinance")

        # A retry after deletion must read fresh state and issue DELETE, not stale PUT.
        path = "/api/v2/agent-cards/" + group
        self.catalog.fail(path, "unavailable")
        start = self.catalog.count()
        self.request("PATCH", f"/groups/{group}", {"visibility": "public"})
        eventually(lambda: self.catalog.matching("PUT", path, start), "failed Group PUT")
        self.catalog.fail(path, "rejected")
        self.request("DELETE", f"/groups/{group}?bot_id={target}")
        eventually(lambda: len(self.catalog.matching("DELETE", path, start)) >= 2, "retried Group DELETE")
        self.catalog.fail(path, None)
        self.removed(group, start)
        with self.catalog.lock:
            methods = [method for method, url, _ in self.catalog.calls[start:] if url == path]
        assert "PUT" not in methods[methods.index("DELETE"):]
        deleted_at = self.catalog.count()
        time.sleep(0.15)
        assert not self.catalog.matching("PUT", path, deleted_at)
        start = self.catalog.count()
        self.request("DELETE", f"/bots/{target}")
        self.removed(target, start)
        with self.catalog.lock:
            assert all(path.startswith("/api/v2/") for _, path, _ in self.catalog.calls)
        print(f"Fuse Card E2E: {self.checks} HTTP/publication checks passed", flush=True)


@contextmanager
def bcs_process(binary, root, config, catalog):
    path = root / "bcs-config.json"
    path.write_text(json.dumps(config))
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
           and key not in {"REAL_SERVER_ENV", "ALIPAY_APP_ENV"}}
    env.update(SERVER_ENV="local", BCS_DEBUG="false", NO_PROXY="127.0.0.1,localhost,::1",
        BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
        BCS_SECRET_FUSE_CATALOG=catalog.credential,
        AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=uuid.uuid4().hex)
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen([binary, "-c", str(path)], cwd=root, env=env, stdout=log, stderr=log)
        try:
            story = Story(f"http://127.0.0.1:{config['port']}", catalog)
            def ready():
                assert process.poll() is None, "BCS exited during startup"
                try:
                    return story.request("GET", "/health")
                except (OSError, urllib.error.URLError):
                    return False
            eventually(ready, "BCS startup", timeout=30)
            yield story
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise AssertionError("BCS shutdown timed out; coverage profile incomplete")
            assert process.returncode == 0, f"BCS exited with {process.returncode}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    catalog = Catalog()
    with tempfile.TemporaryDirectory(prefix="bcs-fuse-cards-") as directory, catalog.serve() as url:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = tomllib.loads((Path(__file__).resolve().parents[2] / "configs/bcs-config-local.toml").read_text())
        config.update(bind="127.0.0.1", port=port, bcs_endpoint=f"http://127.0.0.1:{port}",
            strict_container_validation=False, auth={"chain": ["local"], "allow_mock_headers": True},
            database={"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}}, llm={"type": "none"},
            bcsfuse={"enabled": True, "api_version": "v2", "url": url, "authorization_ref": "fuse-catalog",
                     "sync_timeout_ms": 1000, "sync_retry_base_ms": 10})
        config["manifest"]["bundles"] = []
        config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "BCS_SECRET_"}}}
        config["bots_base_dir"] = str(root / "bots")
        config["session_files"]["backend"] = {"data_dir": str(root / "files")}
        config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
        config["security"]["outbound_url"]["allow_loopback"] = True
        with bcs_process(binary, root, config, catalog) as story:
            story.run()
        # Restart the same persisted database: no startup backfill is expected.
        start = catalog.count()
        with bcs_process(binary, root, config, catalog):
            time.sleep(0.25)
            assert catalog.count() == start, "Unchanged Cards were republished on startup"
        print("Fuse Card E2E: persisted restart without startup backfill passed", flush=True)


if __name__ == "__main__":
    main()
