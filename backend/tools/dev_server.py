"""Development server with hot restart.

Why not `uvicorn --reload`: uvicorn restarts its worker on Windows by sending a
CTRL_C_EVENT to the worker's process id, which requires that worker to live in
its own process group. Python 3.14's multiprocessing spawn no longer creates one
(creationflags=0), so that event reaches nobody, `process.join()` blocks forever
and --reload stops working after the very first file change. This supervisor
watches the source tree and restarts the whole uvicorn process instead.

Usage:  .venv\\Scripts\\python.exe tools\\dev_server.py [port]
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
WATCH = BACKEND
SKIP_DIRS = {".venv", ".pytest_cache", "__pycache__", "data", ".git"}
SUFFIXES = {".py", ".sql"}
POLL_SECONDS = 0.7


def python_exe() -> str:
    venv = BACKEND / ".venv" / "Scripts" / "python.exe"
    return str(venv if venv.exists() else sys.executable)


def snapshot() -> dict[str, int]:
    seen: dict[str, int] = {}
    for path in WATCH.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        try:
            seen[str(path)] = path.stat().st_mtime_ns
        except OSError:
            pass
    return seen


def main(argv: list[str]) -> int:
    port = argv[1] if len(argv) > 1 else "8000"
    cmd = [python_exe(), "-m", "uvicorn", "app.main:app", "--port", port]

    def start() -> subprocess.Popen:
        proc = subprocess.Popen(cmd, cwd=str(BACKEND))
        print(f"[dev] uvicorn started (pid {proc.pid}) on port {port}", flush=True)
        return proc

    def stop(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    state = snapshot()
    proc = start()
    print(f"[dev] watching {WATCH} ({len(state)} files); save a file to restart", flush=True)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            if proc.poll() is not None:
                print("[dev] server exited on its own; restarting", flush=True)
                time.sleep(1)
                state = snapshot()
                proc = start()
                continue
            now = snapshot()
            if now != state:
                changed = sorted(
                    pathlib.Path(key).name
                    for key in set(state) | set(now)
                    if state.get(key) != now.get(key)
                )
                state = now
                print(f"[dev] changed: {', '.join(changed)} -> restarting", flush=True)
                stop(proc)
                proc = start()
    except KeyboardInterrupt:
        print("[dev] stopping", flush=True)
    finally:
        stop(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
