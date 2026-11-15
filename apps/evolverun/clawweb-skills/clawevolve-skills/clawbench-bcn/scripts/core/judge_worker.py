"""Execute reviewed Case grading code in a separate process, NOT a security sandbox."""
import json
import sys

if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    namespace = {}
    exec(compile(request["code"], "<reviewed-case-judge>", "exec"), namespace)
    scores = namespace["grade"](request.get("transcript", []), request["workspace"])
    print(json.dumps(scores, allow_nan=False))
