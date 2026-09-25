"""Report a failed Runner startup through the existing Step result endpoint."""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clawweb-url", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--phase", choices=("bootstrap", "launcher"), required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    base = args.clawweb_url.rstrip("/")
    url = urllib.parse.urlsplit(base)
    local = url.hostname in {"localhost", "127.0.0.1"}
    if (url.scheme not in ({"http", "https"} if local else {"https"})
        or not url.hostname or url.username or url.password
        or url.path or url.query or url.fragment
        or not all(re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value)
                   for value in (args.task_id, args.step_id))):
        print("startup failure report rejected: invalid callback context", file=sys.stderr)
        return 1
    code, summary = (
        ("RUNNER_BOOTSTRAP_FAILED", "Runner 启动失败") if args.phase == "bootstrap"
        else ("OPENCLAW_RUNTIME_MAINTENANCE_FAILED", "OpenClaw运行时准备失败")
    )
    payload = json.dumps({
        "status": "failed", "summary": summary,
        "error": {"code": code, "retryable": True,
                  "message": f"{args.phase} exited with status {args.exit_code} before Stage handoff; see run.log"},
    }, ensure_ascii=False).encode("utf-8")
    target = f"{base}/api/evolve/internal/tasks/{args.task_id}/steps/{args.step_id}/report"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for attempt in range(3):
        request = urllib.request.Request(target, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with opener.open(request, timeout=10) as response:
                print(json.dumps({"startup_failure_report_status": response.status}))
            return 0
        except Exception as exc:
            # Exception text and request URLs can contain credentials. Record only
            # the category/status; the original startup failure stays in run.log.
            detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            print(f"startup failure report not acknowledged: {detail}; attempt={attempt + 1}/3", file=sys.stderr)
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                return 1
            if attempt < 2:
                time.sleep(attempt + 1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
