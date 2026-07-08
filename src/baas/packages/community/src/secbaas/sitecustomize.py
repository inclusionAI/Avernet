import sys

# Fix uvicorn "Bad file descriptor" on macOS spawn.
# Setting sys.stdin to None makes sys.stdin.fileno() raise AttributeError,
# which uvicorn's get_subprocess() catches and handles by setting
# stdin_fileno=None, skipping the os.fdopen(0) that fails in spawned children.
sys.stdin = None
