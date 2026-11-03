"""Offline CLI process fixture; no network, account credentials or DWS installation."""

import json
import os
import signal
import sys
import time
from pathlib import Path

mode, args = sys.argv[1], sys.argv[2:]
Path("pid").write_text(str(os.getpid()))
with Path("argv.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")

if mode == "fail":
    print("private diagnostic must not escape", file=sys.stderr)
    sys.exit(2)
if mode == "invalid":
    sys.stdout.buffer.write(b"\xff\n")
    sys.exit(0)
if mode == "large":
    print("x" * 4096)
    sys.exit(0)
if mode == "output":
    for _ in range(100):
        print("x" * 32)
    sys.exit(0)
if mode == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
    sys.exit(0)
if mode == "env":
    print(json.dumps(dict(os.environ)))
    sys.exit(0)

if args[:2] == ["auth", "login"]:
    print("Device authorization", file=sys.stderr)
    print(
        "https://auth.example.invalid/login?user_code=TEST-CODE",
        file=sys.stderr,
        flush=True,
    )
    if mode == "auth-hang":
        time.sleep(60)
    print(
        json.dumps(
            dict(
                success=mode != "auth-fail",
                corp_id="org",
                user_id="other" if mode == "wrong-account" else "self",
            )
        )
    )
elif args[:2] == ["auth", "status"]:
    print(
        json.dumps(
            dict(
                success=True,
                authenticated=mode != "logged-out",
                corp_id="org",
                user_id="self",
            )
        )
    )
elif args[:2] == ["event", "consume"]:
    Path("consumer.pid").write_text(str(os.getpid()))
    print("[event] ready event_count=1 bus_pid=42", file=sys.stderr, flush=True)
    print(
        json.dumps(
            dict(
                type="wrong"
                if mode == "wrong-event"
                else "user_im_message_receive_o2o_all",
                conversation_id="chat",
                message_id="m1",
                create_time=1_800_000_001_000,
            )
        ),
        flush=True,
    )
    if mode != "ended":
        sys.stdin.read()
elif args[:2] == ["event", "status"]:
    pid = int(Path("consumer.pid").read_text()) if Path("consumer.pid").exists() else 0
    print(
        json.dumps(
            dict(
                bus={
                    "live": dict(
                        bus={"pid": 42},
                        source_state={"state": "connected", "reconnect_count": 0},
                        consumers=[{"pid": pid, "dropped": 0}],
                    )
                }
            )
        )
    )
elif args[:2] == ["chat", "+recent-conversations"]:
    print(
        json.dumps(
            dict(
                ok=True,
                outcome="success",
                data=dict(
                    complete=True,
                    unknownTypeCount=0,
                    conversations=[{"type": "direct", "conversationId": "chat"}],
                ),
            )
        )
    )
elif args[:2] == ["chat", "+chat-messages"]:
    print(
        json.dumps(
            dict(
                ok=True,
                outcome="success",
                data=dict(
                    contractVersion="im.message-list.v1",
                    complete=True,
                    hasMore=False,
                    paginationKnown=True,
                    failedCount=0,
                    truncated=False,
                    messages=[
                        dict(
                            messageId="m1",
                            conversationId="chat",
                            senderId="peer",
                            sender="Peer",
                            text="hello",
                            createTime=1_800_000_001_000,
                        )
                    ],
                ),
            )
        )
    )
elif args[:2] == ["chat", "+messages-send"]:
    if mode == "send-fail":
        print("private send failure", file=sys.stderr)
        sys.exit(2)
    if mode == "send-hang":
        time.sleep(60)
    if mode == "send-malformed":
        print("not-json")
        sys.exit(0)
    receipt = dict(
        contractVersion="im.message-send-receipt.v1",
        openTaskId="task",
        readyForMessageActions=False,
    )
    if mode == "send-direct":
        receipt.update(
            readyForMessageActions=True,
            messageRef=dict(openMessageId="delivered", openConversationId="chat"),
        )
    print(
        json.dumps(
            dict(
                ok=True,
                identity="user",
                tool="send_personal_message",
                result=dict(openTaskId="task"),
                sendReceipt=receipt,
            )
        )
    )
elif args[:2] == ["chat", "+messages-query-send-status"]:
    payload = dict(
        contractVersion="im.message-send-status.v1",
        openTaskId="task",
        readyForMessageActions=False,
        result=dict(status="PENDING"),
    )
    if mode != "poll-pending":
        payload.update(
            readyForMessageActions=True,
            messageRef=dict(openMessageId="delivered", openConversationId="chat"),
            result=dict(status="SUCCESS"),
        )
    print(json.dumps(payload))
else:
    raise SystemExit(3)
