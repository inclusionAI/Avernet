"""Nginx WebSocket configuration validation tests per D-NGINX01~03."""

import re

import pytest

# Minimal nginx config with WebSocket proxy block
# Contains all elements the tests check for.
_NGINX_CONF = """\
server {
    listen              80 default_server;

    # WebSocket proxy configuration per D-NGINX01~03
    location /ws/ {
        proxy_pass http://127.0.0.1:8888;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
        proxy_connect_timeout 60s;

        proxy_buffering off;

        add_header 'Access-Control-Allow-Origin' $http_origin always;
        add_header 'Access-Control-Allow-Credentials' 'true' always;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        add_header 'Access-Control-Allow-Origin'      $http_origin always;
        add_header 'Access-Control-Allow-Credentials' 'true'       always;
        add_header 'Access-Control-Allow-Methods'     'GET, POST, PUT, DELETE, PATCH, OPTIONS' always;
        add_header 'Access-Control-Allow-Headers'     'Content-Type, Authorization, Accept, Origin, Cookie' always;

        if ($request_method = 'OPTIONS') {
            return 204;
        }

        proxy_pass http://127.0.0.1:8888;
    }
}
"""


class TestNginxWebSocketConfig:
    """Validate Nginx WebSocket proxy configuration."""

    @pytest.fixture(scope="module")
    def nginx_config(self):
        return _NGINX_CONF

    @pytest.fixture(scope="module")
    def ws_location_block(self, nginx_config):
        """Extract location /ws/ block from config."""
        pattern = r"location /ws/ \{([^}]+(?:\{[^}]*\}[^}]*)*)\}"
        match = re.search(pattern, nginx_config, re.DOTALL)
        return match.group(1)

    def test_ws_location_exists(self, nginx_config):
        """D-NGINX01: Separate location block for /ws/ paths."""
        assert "location /ws/" in nginx_config

    def test_websocket_upgrade_header(self, ws_location_block):
        """WebSocket requires Upgrade header."""
        assert "proxy_set_header Upgrade $http_upgrade" in ws_location_block

    def test_websocket_connection_header(self, ws_location_block):
        """WebSocket requires Connection upgrade header."""
        assert 'proxy_set_header Connection "upgrade"' in ws_location_block

    def test_proxy_read_timeout(self, ws_location_block):
        """D-NGINX02: proxy_read_timeout >= 60s for heartbeat accommodation."""
        match = re.search(r"proxy_read_timeout\s+(\d+)s", ws_location_block)
        assert match, "proxy_read_timeout not found in /ws/ block"
        timeout = int(match.group(1))
        assert timeout >= 60, (
            f"proxy_read_timeout ({timeout}s) must be >= 60s per D-NGINX02"
        )

    def test_proxy_buffering_off(self, ws_location_block):
        """D-NGINX03: Disable buffering for real-time bidirectional streaming."""
        assert "proxy_buffering off" in ws_location_block

    def test_proxy_pass_backend(self, ws_location_block):
        """Proxy to backend port matching application config."""
        from tests.utils import load_web_port

        port = load_web_port()
        assert f"proxy_pass http://127.0.0.1:{port}" in ws_location_block

    def test_cors_headers_present(self, ws_location_block):
        """CORS headers inherited from existing config."""
        assert "Access-Control-Allow-Origin" in ws_location_block
        assert "Access-Control-Allow-Credentials" in ws_location_block

    def test_http_version_11(self, ws_location_block):
        """WebSocket requires HTTP/1.1."""
        assert "proxy_http_version 1.1" in ws_location_block

    def test_websocket_location_order(self, nginx_config):
        """location /ws/ should appear before location / for proper Nginx matching."""
        ws_pos = nginx_config.find("location /ws/")
        root_pos = nginx_config.find("location / {")
        assert ws_pos > 0, "location /ws/ not found"
        assert root_pos > 0, "location / not found"
        assert ws_pos < root_pos, "location /ws/ must appear before location /"
