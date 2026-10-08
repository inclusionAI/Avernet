"""
Fake External Provider Server for S12 Smoke Tests

This module provides a local HTTP server that simulates external provider APIs
for testing purposes. It does NOT connect to real external services.

Features:
- Embedding API (OpenAI-compatible)
- Reranker API
- LLM API (Anthropic Messages API compatible)
- Request logging with masked Authorization headers
- Configurable error scenarios
- No real tokens required

Usage:
    from fake_external_provider_server import FakeProviderServer

    server = FakeProviderServer(port=19999)
    server.start()

    # Configure providers to use http://127.0.0.1:19999
    # ...

    server.stop()
"""
import json
import logging
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def mask_authorization_header(value: str) -> str:
    """
    Mask Authorization header value for safe logging.

    Examples:
        "Bearer abc123..." → "Bearer ***MASKED***"
        "abc123..." → "***MASKED***"
    """
    if not value:
        return ""

    # Remove any existing "Bearer " prefix
    if value.lower().startswith("bearer "):
        return "Bearer ***MASKED***"

    return "***MASKED***"


class FakeProviderHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for fake external providers.

    Handles requests for:
    - POST /v1/embeddings - Embedding API
    - POST /v1/rerank - Reranker API
    - POST /v1/messages - LLM API (Anthropic-compatible)

    All Authorization headers are masked in logs.
    """

    # Class-level configuration for error injection
    error_mode: str = "normal"  # "normal", "embed_401", "embed_malformed", "rerank_500", "llm_500"

    def log_message(self, format, *args):
        """Override to use our logger instead of stderr."""
        logger.info("%s - %s", self.address_string(), format % args)

    def _getAuthorization_header(self) -> Optional[str]:
        """Extract and log Authorization header (masked)."""
        # Check multiple header variations
        auth_header = (
            self.headers.get("Authorization") or
            self.headers.get("authorization") or
            self.headers.get("X-Api-Key") or
            self.headers.get("x-api-key")
        )

        if auth_header:
            masked = mask_authorization_header(auth_header)
            logger.info("Request Authorization: %s (length: %d)", masked, len(auth_header))
            return auth_header
        else:
            logger.warning("Request missing Authorization header")
            return None

    def _send_json_response(self, status_code: int, data: dict):
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_error_response(self, status_code: int, message: str):
        """Send error response."""
        self._send_json_response(status_code, {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": status_code
            }
        })

    def do_POST(self):
        """Handle POST requests."""
        # Log request
        logger.info("POST %s from %s", self.path, self.address_string())

        # Check Authorization header
        auth = self._getAuthorization_header()
        if not auth:
            self._send_error_response(401, "Missing Authorization header")
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                self._send_error_response(400, "Invalid JSON")
                return
        else:
            request_data = {}

        # Route to appropriate handler
        # Support both /v1/* and /* paths for compatibility
        path = urlparse(self.path).path

        try:
            if path in ["/v1/embeddings", "/embeddings"]:
                self._handle_embeddings(request_data)
            elif path in ["/v1/rerank", "/rerank"]:
                self._handle_rerank(request_data)
            elif path in ["/v1/messages", "/messages"]:
                self._handle_messages(request_data)
            else:
                self._send_error_response(404, f"Not found: {path}")
        except Exception as e:
            logger.exception("Handler error")
            self._send_error_response(500, f"Internal error: {str(e)}")

    def _handle_embeddings(self, request_data: dict):
        """
        Handle embedding request.

        Request format:
        {
            "input": ["text1", "text2"],
            "model": "fake-embedding-model"
        }

        Response format:
        {
            "data": [
                {"embedding": [0.1, 0.2, 0.3, ...]},
                ...
            ],
            "model": "fake-embedding-model"
        }
        """
        # Check error mode
        if self.error_mode == "embed_401":
            self._send_error_response(401, "Unauthorized - fake 401 error")
            return

        if self.error_mode == "embed_malformed":
            # Send malformed response
            self._send_json_response(200, {"data": "not_an_array"})
            return

        # Parse input
        input_texts = request_data.get("input", [])
        if isinstance(input_texts, str):
            input_texts = [input_texts]

        if not input_texts:
            self._send_error_response(400, "Missing 'input' field")
            return

        # Generate fake embeddings (fixed dimension = 1024)
        dimension = 1024
        embeddings = []

        for i, text in enumerate(input_texts):
            # Use deterministic values based on text length and index
            base_value = 0.1 + (i * 0.01)
            embedding = [base_value + (j * 0.001) for j in range(dimension)]
            embeddings.append({
                "embedding": embedding,
                "index": i
            })

        response = {
            "data": embeddings,
            "model": request_data.get("model", "fake-embedding-model"),
            "usage": {
                "prompt_tokens": sum(len(t.split()) for t in input_texts),
                "total_tokens": sum(len(t.split()) for t in input_texts)
            }
        }

        self._send_json_response(200, response)

    def _handle_rerank(self, request_data: dict):
        """
        Handle reranker request.

        Request format:
        {
            "model": "fake-reranker-model",
            "query": "query text",
            "documents": ["doc1", "doc2", ...]
        }

        Response format:
        {
            "results": [
                {"index": 0, "relevance_score": 0.99},
                ...
            ]
        }
        """
        # Check error mode
        if self.error_mode == "rerank_500":
            self._send_error_response(500, "Internal server error - fake 500 error")
            return

        # Parse input
        query = request_data.get("query", "")
        documents = request_data.get("documents", [])

        if not documents:
            self._send_error_response(400, "Missing 'documents' field")
            return

        # Generate fake rerank scores (higher for first documents)
        results = []
        for i, doc in enumerate(documents):
            # Score decreases with index (first is most relevant)
            score = 0.99 - (i * 0.1)
            results.append({
                "index": i,
                "relevance_score": max(score, 0.1)
            })

        response = {
            "results": results,
            "model": request_data.get("model", "fake-reranker-model")
        }

        self._send_json_response(200, response)

    def _handle_messages(self, request_data: dict):
        """
        Handle LLM request (Anthropic Messages API format).

        Request format:
        {
            "model": "fake-llm-model",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "prompt"}
            ],
            "system": "system prompt"
        }

        Response format:
        {
            "content": [
                {"type": "text", "text": "fake response"}
            ],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20
            }
        }
        """
        # Check error mode
        if self.error_mode == "llm_500":
            self._send_error_response(500, "Internal server error - fake 500 error")
            return

        # Parse input
        messages = request_data.get("messages", [])
        if not messages:
            self._send_error_response(400, "Missing 'messages' field")
            return

        # Generate fake response
        user_message = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        response_text = f"Fake LLM response to: {user_message[:100]}"

        response = {
            "content": [
                {
                    "type": "text",
                    "text": response_text
                }
            ],
            "stop_reason": "end_turn",
            "model": request_data.get("model", "fake-llm-model"),
            "usage": {
                "input_tokens": len(user_message.split()),
                "output_tokens": len(response_text.split())
            }
        }

        self._send_json_response(200, response)


class FakeProviderServer:
    """
    Fake HTTP server for external providers.

    Usage:
        server = FakeProviderServer(port=19999)
        server.start()

        # Set environment variables to use fake server
        os.environ["EMBEDDING_BASE_URL"] = "http://127.0.0.1:19999"
        os.environ["RERANKER_BASE_URL"] = "http://127.0.0.1:19999"
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:19999"

        # ... run tests ...

        server.stop()
    """

    def __init__(self, port: int = 19999):
        """
        Initialize fake server.

        Args:
            port: Port to listen on (default: 19999)
        """
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Start the server in a background thread."""
        if self.server is not None:
            logger.warning("Server already running")
            return

        self.server = HTTPServer(("127.0.0.1", self.port), FakeProviderHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        logger.info("Fake provider server started on http://127.0.0.1:%d", self.port)
        logger.info("Endpoints:")
        logger.info("  POST http://127.0.0.1:%d/v1/embeddings (or /embeddings)", self.port)
        logger.info("  POST http://127.0.0.1:%d/v1/rerank (or /rerank)", self.port)
        logger.info("  POST http://127.0.0.1:%d/v1/messages (or /messages)", self.port)

        # Wait a bit for server to start
        time.sleep(0.1)

    def stop(self):
        """Stop the server."""
        if self.server is not None:
            self.server.shutdown()
            self.server = None
            logger.info("Fake provider server stopped")

    def set_error_mode(self, mode: str):
        """
        Set error mode for testing.

        Modes:
            - "normal": Normal responses (default)
            - "embed_401": Embedding endpoint returns 401
            - "embed_malformed": Embedding endpoint returns malformed response
            - "rerank_500": Reranker endpoint returns 500
            - "llm_500": LLM endpoint returns 500
        """
        FakeProviderHandler.error_mode = mode
        logger.info("Error mode set to: %s", mode)

    @property
    def base_url(self) -> str:
        """Get base URL for this server."""
        return f"http://127.0.0.1:{self.port}"


def create_fake_server(port: int = 19999) -> FakeProviderServer:
    """
    Create and start a fake provider server.

    This is a convenience function for tests.

    Args:
        port: Port to listen on

    Returns:
        FakeProviderServer instance (already started)
    """
    server = FakeProviderServer(port=port)
    server.start()
    return server


if __name__ == "__main__":
    # Run server directly for manual testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    server = create_fake_server(port=19999)

    print("\n" + "=" * 60)
    print("Fake Provider Server running on http://127.0.0.1:19999")
    print("=" * 60)
    print("\nAvailable endpoints:")
    print("  POST /embeddings (or /v1/embeddings) - Embedding API")
    print("  POST /rerank (or /v1/rerank)         - Reranker API")
    print("  POST /messages (or /v1/messages)     - LLM API (Anthropic-compatible)")
    print("\nAll endpoints require Authorization header (any value works)")
    print("\nPress Ctrl+C to stop...")
    print("=" * 60 + "\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()
        print("Server stopped.")