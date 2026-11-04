"""JSON-RPC wire binding for the live A2A story's local peer."""
import copy
import io
import json
from urllib.parse import quote, urlsplit


def envelope(handler, value):
    if hasattr(handler, "rpc_id"):
        return {"jsonrpc": "2.0", "id": handler.rpc_id, "result": value}
    return value


def dispatch(handler, peer):
    if peer.binding != "JSONRPC" or urlsplit(handler.path).path != "/a2a":
        return False
    if not handler.authenticated():
        return True
    request = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
    assert request["jsonrpc"] == "2.0" and isinstance(request["id"], str)
    handler.rpc_id = request["id"]
    method, params = request["method"], request["params"]
    with peer.lock:
        peer.rpc_methods.add(method)
    if method == "SubscribeToTask":
        with peer.lock:
            task = copy.deepcopy(peer.tasks[params["id"]])
            peer.rpc_resumed = True
        # GetTask already established this Artifact baseline. Mark the repeated
        # complete snapshot as final, not as an unsupported intermediate replacement.
        events = [{"artifactUpdate": {"taskId": task["id"], "contextId": task["contextId"],
                   "artifact": task["artifacts"][0], "append": False, "lastChunk": True}},
                  {"statusUpdate": {"taskId": task["id"], "contextId": task["contextId"],
                   "status": {"state": "TASK_STATE_COMPLETED"}}}]
        data = b"".join(("data:" + json.dumps(envelope(handler, event)) + "\n\n").encode()
                        for event in events)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True
    if method == "GetTask":
        # Force a real reconnect: query sees working, then subscribe supplies final output.
        if params["id"] == "peer-task-4" and not getattr(peer, "rpc_resumed", False):
            with peer.lock:
                task = copy.deepcopy(peer.tasks[params["id"]])
            task["status"]["state"] = "TASK_STATE_WORKING"
            handler.json_response(200, task)
        else:
            handler.path = "/a2a/tasks/" + quote(params["id"], safe="")
            handler.do_GET()
        return True
    paths = {"SendMessage": "/a2a/message:send", "SendStreamingMessage": "/a2a/message:stream"}
    if method == "CancelTask":
        handler.path = "/a2a/tasks/" + quote(params["id"], safe="") + ":cancel"
    else:
        handler.path = paths[method]
    body = json.dumps(params).encode()
    handler.rfile = io.BytesIO(body)
    handler.headers.replace_header("Content-Length", str(len(body)))
    handler.do_POST()
    return True
