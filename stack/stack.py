#!/usr/bin/env python3
"""Start, stop and check the whole Omni stack: model server, heads, speaker check, voice agent,
chat page and Omni Studio, exactly as they ran when the snapshot was taken.

    ./stack.py start      start whatever is not running, in order, and wait until each answers
    ./stack.py stop       stop all services (children too, e.g. the vLLM stage processes)
    ./stack.py status     one line per service
    ./stack.py snapshot   record the running services again (after changing how one is started)

Each service is stored in services/<name>.json: command, folder, environment and log file,
copied from the running process by `snapshot`. Nothing is guessed.
"""
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICES = HERE / "services"

# start order; `ready` is checked after starting, `wait` is how long a cold start may take
STACK = [
    {"name": "asv", "port": 8613, "what": "speaker check (voice agent gate)", "wait": 120},
    {"name": "vllm", "port": 8410, "what": "vLLM mix87: thinker + talker (GPU 0)", "wait": 1200,
     "ready": "http://127.0.0.1:8410/v1/models"},
    {"name": "heads", "port": 8720, "what": "sidecar heads + Silero VAD (GPU 1)", "wait": 600,
     "ready": "http://127.0.0.1:8720/health"},
    {"name": "proxy", "port": 8401, "what": "chat proxy for Open WebUI", "wait": 60,
     "ready": "http://127.0.0.1:8401/health"},
    {"name": "voice", "port": 7912, "what": "Pipecat voice agent v5.3", "wait": 180},
    {"name": "webui", "port": 8080, "what": "Open WebUI chat page (https)", "wait": 300},
    {"name": "studio", "port": 8710, "what": "Omni Studio + assistant", "wait": 120,
     "ready": "https://127.0.0.1:8710/api/info"},
]
URLS = {"webui": "https://192.168.8.174:8080/", "studio": "https://192.168.8.174:8710/", "voice": "https://127.0.0.1:7912/"}
# environment variables of the shell the services happened to be started from, not of the services
DROP_ENV = ("CLAUDE", "OTEL_", "VSCODE_", "CURSOR_", "TERM_PROGRAM")

_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE


def listener_pid(port: int) -> int | None:
    out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[3].endswith(f":{port}") and "pid=" in line:
            return int(line.split("pid=")[1].split(",")[0])
    return None


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ready(svc: dict) -> bool:
    if not port_open(svc["port"]):
        return False
    url = svc.get("ready")
    if not url:
        return True
    try:
        with urllib.request.urlopen(url, timeout=3, context=_insecure if url.startswith("https") else None) as r:
            return r.status == 200
    except Exception:
        return False


def descendants(pid: int) -> list:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        try:
            kids = [int(c) for t in Path(f"/proc/{p}/task").iterdir()
                    for c in (t / "children").read_text().split()]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            kids = []
        out += kids
        todo += kids
    return out


# ----------------------------------------------------------------------------- commands
def snapshot() -> None:
    SERVICES.mkdir(exist_ok=True)
    for svc in STACK:
        pid = listener_pid(svc["port"])
        if not pid:
            print(f"  {svc['name']:7} port {svc['port']}: not running, kept the old record")
            continue
        # the listener can be a worker; record its top process of the same command family
        cmd = [a for a in Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0") if a]
        env = dict(kv.split("=", 1) for kv in Path(f"/proc/{pid}/environ").read_bytes().decode().split("\0") if "=" in kv)
        env = {k: v for k, v in env.items() if not k.startswith(DROP_ENV)}
        rec = {"name": svc["name"], "port": svc["port"], "cmd": cmd, "cwd": os.readlink(f"/proc/{pid}/cwd"),
               "log": os.readlink(f"/proc/{pid}/fd/1"), "env": env, "taken": time.strftime("%Y-%m-%d %H:%M:%S")}
        if not rec["log"].startswith("/"):
            rec["log"] = f"/tmp/omni_{svc['name']}.log"
        f = SERVICES / f"{svc['name']}.json"
        f.write_text(json.dumps(rec, indent=1))
        f.chmod(0o600)  # the environment can hold API keys
        print(f"  {svc['name']:7} port {svc['port']}: {' '.join(cmd)[:110]}")


def start(only=None) -> int:
    failed = 0
    started = []
    for svc in STACK:
        if only and svc["name"] not in only:
            continue
        if port_open(svc["port"]):
            print(f"  {svc['name']:7} already running on {svc['port']}")
            continue
        rec = json.loads((SERVICES / f"{svc['name']}.json").read_text())
        log = open(rec["log"], "a")
        log.write(f"\n===== started by omni-stack {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()
        subprocess.Popen(rec["cmd"], cwd=rec["cwd"], env=rec["env"], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        print(f"  {svc['name']:7} starting: {svc['what']}  (log {rec['log']})")
        started.append(svc)
        time.sleep(2)
    for svc in started:  # wait in start order; the model server is the slow one
        t0 = time.time()
        while not ready(svc) and time.time() - t0 < svc["wait"]:
            time.sleep(3)
        ok = ready(svc)
        failed += not ok
        print(f"  {svc['name']:7} {'ready' if ok else 'NOT READY'} after {time.time() - t0:.0f}s"
              + ("" if ok else f" — see {json.loads((SERVICES / (svc['name'] + '.json')).read_text())['log']}"))
    return failed


def stop(only=None) -> None:
    for svc in reversed(STACK):
        if only and svc["name"] not in only:
            continue
        pid = listener_pid(svc["port"])
        if not pid:
            print(f"  {svc['name']:7} not running")
            continue
        pids = [pid] + descendants(pid)
        for p in pids:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        t0 = time.time()
        while time.time() - t0 < 30 and any(Path(f"/proc/{p}").exists() for p in pids):
            time.sleep(0.5)
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        print(f"  {svc['name']:7} stopped ({len(pids)} process{'es' if len(pids) > 1 else ''})")


def status() -> int:
    down = 0
    for svc in STACK:
        ok = ready(svc)
        down += not ok
        print(f"  {'up  ' if ok else 'DOWN'}  {svc['name']:7} :{svc['port']:<5} {svc['what']}")
    print("\n  chat page:   " + URLS["webui"] + "\n  studio:      " + URLS["studio"])
    return down


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    names = sys.argv[2:] or None
    if what == "snapshot":
        snapshot()
    elif what == "start":
        bad = start(names)
        print()
        status()
        sys.exit(1 if bad else 0)
    elif what == "stop":
        stop(names)
    elif what == "restart":
        stop(names)
        time.sleep(2)
        sys.exit(1 if start(names) else 0)
    else:
        sys.exit(1 if status() else 0)
