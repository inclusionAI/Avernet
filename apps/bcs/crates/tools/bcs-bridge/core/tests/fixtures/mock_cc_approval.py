#!/usr/bin/env python3
"""Claude stream-json permission peer; validate both sides of the control protocol."""

import json
import sys


def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def require(condition, reason):
    if not condition:
        send({"type": "result", "subtype": "error_" + reason})
        sys.exit(1)


args = sys.argv[1:]
require(any(args[i:i + 2] == ["--permission-prompt-tool", "stdio"]
            for i in range(len(args))), "missing_permission_control_channel")
user = json.loads(sys.stdin.readline())
require(user.get("type") == "user", "expected_user_message")
scenario = user["message"]["content"][0]["text"]
if scenario == "mcp":
    tool = "mcp__coordination__bcs_assign_task"
    tool_input = {"target_bot": "worker-1", "message": "请做简短自我介绍。"}
elif scenario == "secret":
    tool = "AskUserQuestion"
    tool_input = {"questions": [{"question": "Secret?", "secret": True}]}
else:
    tool = "Bash"
    tool_input = {"command": "printf approved"}

send({"type": "control_request", "request_id": "req-1", "request": {
    "subtype": "can_use_tool", "tool_name": tool, "input": tool_input,
    "tool_use_id": "tool-1",
}})
reply = json.loads(sys.stdin.readline())
require(reply.get("type") == "control_response", "expected_control_response")
response = reply.get("response", {})
require(response.get("request_id") == "req-1", "wrong_request_id")
require(response.get("subtype") == "success", "missing_response_subtype")
permission = response.get("response", {})
behavior = permission.get("behavior")
if behavior == "allow":
    require(permission.get("updatedInput") == tool_input, "lost_tool_input")
    result = "approved"
else:
    require(behavior == "deny", "invalid_behavior")
    require(isinstance(permission.get("message"), str) and permission["message"],
            "missing_denial_message")
    require("updatedInput" not in permission, "denial_has_updated_input")
    result = "denied"
send({"type": "result", "subtype": "success", "result": result})
