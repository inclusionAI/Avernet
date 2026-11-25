"""Offline guard tests: stdlib unittest only; no browser or server is started."""

from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import visual_editor_browser as harness
from visual_editor_network import (
    OfflineNetworkGuard,
    create_guarded_context,
    default_dist_dir,
)

BASE_URL = "http://127.0.0.1:5189"
SPEC = {"id": "editor-test", "title": "Example", "nodes": [], "custom": False}
SAVE_PATHS = ("/api/workflows/save", "/api/auto-heal/save")


class OfflineNetworkGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dist = self.root / "web"
        self.assets = self.dist / "assets"
        self.assets.mkdir(parents=True)
        (self.dist / "index.html").write_bytes(b"<!doctype html><div>real build</div>")
        (self.assets / "app.js").write_bytes(b"console.log('real build');")
        (self.assets / "app.css").write_bytes(b"body { margin: 0; }")
        (self.assets / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.guard = OfflineNetworkGuard(BASE_URL, self.dist, SPEC)

    def request(self, path: str, method: str = "GET", **kwargs: object) -> Mock:
        url = path if ":" in path.split("/")[0] else BASE_URL + path
        request = SimpleNamespace(
            url=url,
            method=method,
            post_data=kwargs.get("body"),
            redirected_from=kwargs.get("redirected_from"),
        )
        # No continue_, fetch, fallback, or response forwarding exists on this double.
        route = Mock(spec=["request", "abort", "fulfill"])
        route.request = request
        self.guard.handle_route(route)
        return route

    def assert_blocked(self, path: str, method: str = "GET", **kwargs: object) -> None:
        count = len(self.guard.violations)
        route = self.request(path, method, **kwargs)
        route.abort.assert_called_once_with("blockedbyclient")
        route.fulfill.assert_not_called()
        self.assertEqual(len(self.guard.violations), count + 1)
        failure = self.guard.violations[-1]
        self.assertEqual(failure["method"], method)
        self.assertTrue(failure["reason"])
        with self.assertRaises(AssertionError):
            self.guard.assert_clean()

    def save_body(self, **updates: object) -> str:
        return json.dumps({"workflowId": "editor-test", "spec": SPEC, **updates})

    def test_default_dist_is_relative_to_helper_not_working_directory(self) -> None:
        expected = Path(__file__).resolve().parents[3] / "app/clawweb/dist/web"
        self.assertEqual(default_dist_dir(), expected)

    def test_invalid_base_urls_fail_before_browser_start(self) -> None:
        for url in (
            "https://example.com",
            "ftp://localhost",
            "file:///tmp/index.html",
            "http://user:password@localhost:5189",
            "http://localhost:5189/path",
            "http://localhost:5189?next=/",
            "http://localhost:5189#fragment",
            "http://localhost:0",
            "http://localhost:65536",
            "http://localhost:bad",
            "http://localhost:5189\n",
            " http://localhost:5189",
            "//localhost:5189",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OfflineNetworkGuard(url, self.dist, SPEC)

    def test_missing_dist_or_index_fails_before_browser_start(self) -> None:
        with self.assertRaises((ValueError, OSError)):
            OfflineNetworkGuard(BASE_URL, self.root / "missing", SPEC)
        (self.dist / "index.html").unlink()
        with self.assertRaises((ValueError, OSError)):
            OfflineNetworkGuard(BASE_URL, self.dist, SPEC)

    def test_index_symlink_is_rejected_before_browser_start(self) -> None:
        target = self.root / "private.html"
        target.write_text("private", encoding="utf-8")
        (self.dist / "index.html").unlink()
        (self.dist / "index.html").symlink_to(target)
        with self.assertRaises((ValueError, OSError)):
            OfflineNetworkGuard(BASE_URL, self.dist, SPEC)

    def test_exact_page_allowlist_fulfills_real_index_without_redirects(self) -> None:
        for path in (
            "/",
            "/index.html",
            "/workflows/workspace?workflowId=editor-test&tab=editor",
        ):
            with self.subTest(path=path):
                route = self.request(path)
                route.abort.assert_not_called()
                response = route.fulfill.call_args.kwargs
                self.assertEqual(response["status"], 200)
                self.assertEqual(
                    response["body"], (self.dist / "index.html").read_bytes()
                )
                self.assertEqual(response["content_type"], "text/html")
                self.assertNotIn("location", response.get("headers", {}))
        self.guard.assert_clean()

    def test_asset_bytes_and_mime_types_are_local(self) -> None:
        for name, mime in (
            ("app.js", "text/javascript"),
            ("app.css", "text/css"),
            ("icon.png", "image/png"),
        ):
            with self.subTest(name=name):
                response = self.request(f"/assets/{name}?v=1").fulfill.call_args.kwargs
                self.assertEqual(response["body"], (self.assets / name).read_bytes())
                self.assertEqual(response["content_type"], mime)
                self.assertEqual(response["status"], 200)

    def test_nested_real_asset_is_allowed(self) -> None:
        (self.assets / "nested").mkdir()
        (self.assets / "nested/font.woff2").write_bytes(b"font")
        self.assertEqual(
            self.request("/assets/nested/font.woff2").fulfill.call_args.kwargs["body"],
            b"font",
        )

    def test_other_loopback_ports_aliases_schemes_and_external_hosts_are_blocked(
        self,
    ) -> None:
        for origin in (
            "http://127.0.0.1:5190",
            "http://127.0.0.1",
            "http://localhost:5189",
            "https://127.0.0.1:5189",
            "http://[::1]:5189",
            "http://127.0.0.2:5189",
            "https://example.com",
            "http://127.0.0.1.evil.test:5189",
        ):
            for path in ("/workflows/workspace", "/assets/app.js", "/api/auth/me"):
                with self.subTest(origin=origin, path=path):
                    self.assert_blocked(origin + path)
        self.assertEqual(len(self.guard.external), 24)

    def test_credentials_malformed_urls_and_non_http_schemes_are_blocked(self) -> None:
        for url in (
            "http://user:pass@127.0.0.1:5189/assets/app.js",
            "http://127.0.0.1:bad/assets/app.js",
            "http://[bad/assets/app.js",
            "http://127.0.0.1:5189/assets/app.js\n",
            "file:///tmp/index.html",
            "ftp://127.0.0.1:5189/assets/app.js",
            "data:text/html,not-allowed",
        ):
            with self.subTest(url=url):
                self.assert_blocked(url)

    def test_default_http_port_is_compared_as_origin_not_string_prefix(self) -> None:
        self.guard = OfflineNetworkGuard("http://localhost/", self.dist, SPEC)
        self.request("http://localhost:80/assets/app.js").fulfill.assert_called_once()
        self.guard.assert_clean()

    def test_static_requests_allow_get_only(self) -> None:
        for method in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
            "TRACE",
            "CONNECT",
        ):
            for path in ("/workflows/workspace", "/assets/app.js"):
                with self.subTest(method=method, path=path):
                    self.assert_blocked(path, method)

    def test_no_spa_fallback_for_unknown_or_missing_paths(self) -> None:
        (self.dist / "private.txt").write_text("not public", encoding="utf-8")
        for path in (
            "/unknown",
            "/favicon.ico",
            "/private.txt",
            "/assets/missing.js",
            "/assets/",
            "/@vite/client",
            "/api",
            "/api/unknown",
        ):
            with self.subTest(path=path):
                self.assert_blocked(path)

    def test_traversal_encoded_separators_and_ambiguous_paths_are_blocked(self) -> None:
        for path in (
            "/assets/../index.html",
            "/assets/./app.js",
            "/assets/%2e%2e/index.html",
            "/assets/%252e%252e/index.html",
            "/assets/%2e%2e%2findex.html",
            "/assets/%2Fapp.js",
            "/assets/%5capp.js",
            "/assets/..\\index.html",
            "/assets//app.js",
            "//assets/app.js",
            "/assets/%00app.js",
            "/assets/%FFapp.js",
            "/assets/%ZZapp.js",
            "/%61pi/auth/me",
        ):
            with self.subTest(path=path):
                self.assert_blocked(path)

    def test_asset_file_directory_and_asset_root_symlinks_are_blocked(self) -> None:
        (self.root / "private.js").write_text("private", encoding="utf-8")
        (self.assets / "escape.js").symlink_to(self.root / "private.js")
        (self.assets / "escape").symlink_to(self.root, target_is_directory=True)
        self.assert_blocked("/assets/escape.js")
        self.assert_blocked("/assets/escape/private.js")
        self.assets.rename(self.dist / "original-assets")
        self.assets.symlink_to(self.dist / "original-assets", target_is_directory=True)
        self.assert_blocked("/assets/app.js")

    def test_file_read_failure_aborts_and_records_failure(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            self.assert_blocked("/assets/app.js")

    def test_redirected_requests_are_rejected_even_for_allowed_targets(self) -> None:
        for path in ("/assets/app.js", "/api/auth/me", "/workflows/workspace"):
            with self.subTest(path=path):
                self.assert_blocked(path, redirected_from=object())

    def test_exact_get_api_whitelist_preserves_response_shapes(self) -> None:
        expected = {
            "/api/runs/workflow-types": "workflows",
            "/api/workflows/editor-test": "nodes",
            "/api/workflows/editor-test/history?limit=50": "history",
            "/api/auth/me": "userId",
        }
        for path, key in expected.items():
            with self.subTest(path=path):
                response = self.request(path).fulfill.call_args.kwargs
                self.assertIn(key, json.loads(response["body"]))
                self.assertEqual(response["content_type"], "application/json")
        self.assertEqual(
            json.loads(self.request("/api/facades").fulfill.call_args.kwargs["body"]),
            [],
        )
        self.assertEqual(len(self.guard.calls), 5)
        self.guard.assert_clean()

    def test_unknown_api_history_suffix_and_wrong_methods_never_return_fake_200(
        self,
    ) -> None:
        for path, method in (
            ("/api/unknown", "GET"),
            ("/api/other/history", "GET"),
            ("/api/workflows/other/history", "GET"),
            ("/api/workflows/editor-test/", "GET"),
            ("/api/auth/me", "POST"),
            ("/api/workflows/editor-test", "DELETE"),
            ("/api/workflows/validate", "GET"),
            ("/api/workflows/save", "PUT"),
            ("/api/auto-heal/save", "GET"),
            ("/api/workflows/save", "OPTIONS"),
        ):
            with self.subTest(path=path, method=method):
                self.assert_blocked(path, method)
        self.assertEqual(len(self.guard.calls), 10)

    def test_validation_post_is_read_only_and_does_not_open_save_window(self) -> None:
        body = json.dumps({"spec": SPEC})
        response = self.request(
            "/api/workflows/validate", "POST", body=body
        ).fulfill.call_args.kwargs
        self.assertEqual(json.loads(response["body"]), {"valid": True, "issues": []})
        self.assertEqual(self.guard.saved, SPEC)
        self.assert_blocked(SAVE_PATHS[0], "POST", body=self.save_body())

    def test_save_is_blocked_before_and_after_explicit_save_step(self) -> None:
        for path in SAVE_PATHS:
            self.assert_blocked(path, "POST", body=self.save_body())
        with self.guard.allow_save():
            for path in SAVE_PATHS:
                self.request(
                    path, "POST", body=self.save_body()
                ).fulfill.assert_called_once()
        for path in SAVE_PATHS:
            self.assert_blocked(path, "POST", body=self.save_body())

    def test_save_step_allows_only_one_db_then_git_pair_and_keeps_payload(self) -> None:
        updated = {**SPEC, "title": "Changed", "custom": {"count": 0, "nil": None}}
        body = self.save_body(spec=updated)
        with self.guard.allow_save():
            db = json.loads(
                self.request(SAVE_PATHS[0], "POST", body=body).fulfill.call_args.kwargs[
                    "body"
                ]
            )
            git = json.loads(
                self.request(SAVE_PATHS[1], "POST", body=body).fulfill.call_args.kwargs[
                    "body"
                ]
            )
        self.assertEqual(db, updated)
        self.assertEqual(self.guard.saved, updated)
        self.assertEqual(git["branch"], "saved/editor-test")
        self.assertEqual([call["path"] for call in self.guard.calls], list(SAVE_PATHS))
        self.assertEqual(SPEC["title"], "Example")
        self.guard.assert_clean()

    def test_out_of_order_or_repeated_saves_are_blocked(self) -> None:
        with self.guard.allow_save():
            self.assert_blocked(SAVE_PATHS[1], "POST", body=self.save_body())
            self.request(SAVE_PATHS[0], "POST", body=self.save_body())
            self.assert_blocked(SAVE_PATHS[0], "POST", body=self.save_body())
            self.request(SAVE_PATHS[1], "POST", body=self.save_body())
            self.assert_blocked(SAVE_PATHS[1], "POST", body=self.save_body())

    def test_save_window_closes_after_exception(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "UI failed"), self.guard.allow_save():
            raise RuntimeError("UI failed")
        self.assert_blocked(SAVE_PATHS[0], "POST", body=self.save_body())

    def test_nested_save_windows_are_not_allowed(self) -> None:
        with (
            self.guard.allow_save(),
            self.assertRaises(RuntimeError),
            self.guard.allow_save(),
        ):
            self.fail("Nested save window was accepted")

    def test_deploy_and_other_writes_are_blocked_in_every_phase(self) -> None:
        for saving in (False, True):
            with self.subTest(saving=saving):

                def attempt_writes() -> None:
                    for path in (
                        "/api/auto-heal/deploy",
                        "/api/auto-heal/run",
                        "/api/workflows/delete",
                    ):
                        for method in ("GET", "POST", "PUT", "DELETE"):
                            self.assert_blocked(path, method, body=self.save_body())

                if saving:
                    with self.guard.allow_save():
                        attempt_writes()
                else:
                    attempt_writes()
        self.assertEqual(self.guard.saved, SPEC)

    def test_invalid_save_payload_aborts_without_changing_saved_state(self) -> None:
        for body in (
            None,
            "not json",
            "[]",
            "null",
            "{}",
            '{"spec": []}',
            self.save_body(workflowId="other"),
            self.save_body(spec={"id": "other", "nodes": []}),
        ):
            with self.subTest(body=body), self.guard.allow_save():
                self.assert_blocked(SAVE_PATHS[0], "POST", body=body)
                self.assertEqual(self.guard.saved, SPEC)

    def test_invalid_validation_payload_is_not_acknowledged(self) -> None:
        for body in (None, "{}", "[]", '{"spec": {"id": "wrong"}}'):
            with self.subTest(body=body):
                self.assert_blocked("/api/workflows/validate", "POST", body=body)

    def test_git_snapshot_must_match_the_saved_db_spec(self) -> None:
        with self.guard.allow_save():
            self.request(SAVE_PATHS[0], "POST", body=self.save_body())
            self.assert_blocked(
                SAVE_PATHS[1],
                "POST",
                body=self.save_body(spec={**SPEC, "title": "Different"}),
            )

    def test_guard_copies_input_spec(self) -> None:
        fixture = copy.deepcopy(SPEC)
        guard = OfflineNetworkGuard(BASE_URL, self.dist, fixture)
        fixture["title"] = "Mutated by caller"
        self.assertEqual(guard.saved, SPEC)

    def test_all_websockets_are_closed_without_connecting_to_server(self) -> None:
        for url in (
            "ws://127.0.0.1:5189/ws",
            "ws://localhost:9999/ws",
            "wss://example.com/ws",
        ):
            with self.subTest(url=url):
                websocket = Mock(spec=["url", "close"])
                websocket.url = url
                self.guard.handle_websocket(websocket)
                websocket.close.assert_called_once_with(
                    code=1008, reason="Offline browser harness"
                )
        self.assertEqual(len(self.guard.violations), 3)
        with self.assertRaises(AssertionError):
            self.guard.assert_clean()

    def test_context_guards_precede_any_page_including_popups(self) -> None:
        events = []
        browser = Mock(spec=["new_context"])
        context = Mock(spec=["route", "route_web_socket", "new_page"])
        browser.new_context.return_value = context
        context.route.side_effect = lambda *args: events.append(("http", args))
        context.route_web_socket.side_effect = lambda *args: events.append(("ws", args))
        context.new_page.side_effect = lambda: events.append(("page", ()))
        guarded = create_guarded_context(browser, self.guard)
        context.new_page.assert_not_called()
        self.assertIs(guarded, context)
        guarded.new_page()
        self.assertEqual([name for name, _ in events], ["http", "ws", "page"])
        self.assertEqual(
            browser.new_context.call_args.kwargs["service_workers"], "block"
        )
        self.assertEqual(events[0][1], ("**/*", self.guard.handle_route))
        self.assertEqual(events[1][1], ("**/*", self.guard.handle_websocket))

    def test_minimap_evidence_rejects_missing_or_zero_size_nodes(self) -> None:
        page = Mock(spec=["wait_for_function", "locator"])
        good = {"width": 180, "height": 90, "renderedWidth": 12, "renderedHeight": 6}
        for nodes in (
            [],
            [{**good, "width": 0}],
            [{**good, "height": 0}],
            [{**good, "renderedWidth": 0}],
            [{**good, "width": float("nan")}],
        ):
            with self.subTest(nodes=nodes):
                page.locator.return_value.evaluate_all.return_value = nodes
                with self.assertRaises(AssertionError):
                    harness.measure_minimap(page, "unit test", 1)
        page.locator.return_value.evaluate_all.return_value = [good]
        evidence = harness.measure_minimap(page, "unit test", 1)
        self.assertEqual(evidence["minimap_nodes"], [good])
        self.assertEqual(page.wait_for_function.call_args.kwargs["arg"], 1)

    def test_ui_geometry_is_rejected_in_spec_but_executor_fields_are_preserved(
        self,
    ) -> None:
        fixture = {"nodes": [{"id": "test", "executor": {"width": 10}}]}
        self.assertTrue(harness.has_no_ui_geometry(fixture))
        for key in harness.UI_GEOMETRY_FIELDS:
            with self.subTest(key=key):
                self.assertFalse(harness.has_no_ui_geometry({**fixture, key: 1}))
                self.assertFalse(
                    harness.has_no_ui_geometry({"nodes": [{"id": "test", key: 1}]})
                )

    def test_reports_record_actual_partial_counts_and_network_failures(self) -> None:
        # This tests reporting/context wiring, not browser behavior. The entire
        # Playwright module is replaced, so it cannot launch a process, even with -S.
        for failure in (False, True):
            with self.subTest(failure=failure):
                output = self.root / f"artifacts-{failure}"
                driver = MagicMock()
                fake_api = ModuleType("playwright.sync_api")
                fake_api.__dict__["sync_playwright"] = Mock(return_value=driver)
                browser = driver.__enter__.return_value.chromium.launch.return_value
                context = browser.new_context.return_value
                modules = {
                    "playwright": ModuleType("playwright"),
                    "playwright.sync_api": fake_api,
                }
                argv = [
                    "visual_editor_browser.py",
                    "--base-url",
                    BASE_URL,
                    "--dist-dir",
                    str(self.dist),
                    "--artifacts",
                    str(output),
                ]

                def exercise_report(
                    page,
                    guard,
                    artifacts,
                    passed,
                    errors,
                    measurements,
                    *,
                    expected_context=context,
                    expected_output=output,
                    fail=failure,
                ):
                    self.assertIs(page, expected_context.new_page.return_value)
                    self.assertEqual(artifacts, expected_output)
                    passed.extend(["first actual check", "second actual check"])
                    measurements["drag"] = {"screen_distance": 42}
                    if fail:
                        route = Mock(spec=["request", "abort", "fulfill"])
                        route.request = SimpleNamespace(
                            url=BASE_URL + "/api/unexpected",
                            method="POST",
                            redirected_from=None,
                            post_data=None,
                        )
                        guard.handle_route(route)
                        guard.assert_clean()

                with (
                    patch.dict(sys.modules, modules),
                    patch.object(sys, "argv", argv),
                    patch.object(harness, "run_checks", side_effect=exercise_report),
                    redirect_stdout(io.StringIO()) as stdout,
                ):
                    if failure:
                        with self.assertRaisesRegex(
                            AssertionError, "Offline network policy"
                        ):
                            harness.main()
                    else:
                        harness.main()
                report = json.loads((output / "results.json").read_text())
                self.assertEqual(report["success"], not failure)
                self.assertEqual(report["passed"], 2)
                self.assertEqual(len(report["checks"]), 2)
                self.assertEqual(bool(report["network_violations"]), failure)
                self.assertEqual(bool(report["errors"]), failure)
                self.assertEqual(report["base_url"], BASE_URL)
                self.assertEqual(report["dist_dir"], str(self.dist))
                self.assertEqual(report["measurements"]["drag"]["screen_distance"], 42)
                self.assertIn("2 PASSED", stdout.getvalue())
                browser.close.assert_called_once()
                self.assertEqual(
                    browser.new_context.call_args.kwargs["service_workers"], "block"
                )
                names = [call[0] for call in context.mock_calls]
                self.assertLess(names.index("route"), names.index("new_page"))
                self.assertLess(
                    names.index("route_web_socket"), names.index("new_page")
                )


if __name__ == "__main__":
    unittest.main()
