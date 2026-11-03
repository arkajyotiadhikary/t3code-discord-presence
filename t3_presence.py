#!/usr/bin/env python3
"""Discord Rich Presence for T3 Code. Standard library only."""

import json
import logging
import math
import os
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
ACTIVE_RUN_STATUSES = ("preparing", "starting", "running", "waiting")
ACTIVE_SUBAGENT_STATUSES = ("pending", "running", "waiting")
DESKTOP_STATE_DIR = "userdata"
STATE_DIR_NAMES = (DESKTOP_STATE_DIR, "dev")

DEFAULT_CONFIG = {
    "client_id": "",
    "poll_seconds": 15,
    "show_project": True,
    "show_thread_title": False,
    "show_model": True,
    "show_agent_count": True,
    "show_agent_avatar": True,
    "image_base_url": "",
    "large_image": "t3code",
    "large_text": "T3 Code",
    "idle_text": "Idle",
    "process_pattern": "/T3 Code.*\\.app/Contents/MacOS/T3 Code",
    "t3_home": "",
}

log = logging.getLogger("t3-presence")


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text()))
    return config


class DiscordIPC:
    OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2

    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None

    @staticmethod
    def _socket_dirs():
        dirs = [os.environ.get("TMPDIR"), os.environ.get("XDG_RUNTIME_DIR")]
        result = run_quiet(["getconf", "DARWIN_USER_TEMP_DIR"])
        if result:
            dirs.append(result.stdout.strip())
        dirs.append("/tmp")
        return [d for d in dict.fromkeys(dirs) if d]

    def connect(self):
        for base in self._socket_dirs():
            for i in range(10):
                path = os.path.join(base, f"discord-ipc-{i}")
                if not os.path.exists(path):
                    continue
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    sock.connect(path)
                except OSError:
                    continue
                self.sock = sock
                self._send(self.OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                op, data = self._recv()
                if op == self.OP_CLOSE or data.get("evt") == "ERROR":
                    self.close()
                    raise ConnectionError(f"Discord refused the handshake: {data}")
                log.info("Connected to Discord as %s", data.get("data", {}).get("user", {}).get("username", "?"))
                return
        raise ConnectionError("Discord socket not found. Is the Discord app open?")

    def _send(self, op, payload):
        body = json.dumps(payload).encode()
        self.sock.sendall(struct.pack("<II", op, len(body)) + body)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length) or b"{}")

    def set_activity(self, activity):
        self._send(self.OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": str(uuid.uuid4()),
        })
        op, data = self._recv()
        if data.get("evt") == "ERROR":
            raise ValueError(f"Discord rejected the activity: {data.get('data')}")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


def t3_home(config):
    if config.get("t3_home"):
        home = Path(config["t3_home"]).expanduser()
        return home if home.is_absolute() else Path.home() / home
    if os.environ.get("T3CODE_HOME"):
        return Path(os.environ["T3CODE_HOME"]).expanduser().absolute()
    return Path.home() / ".t3"


START_TIME_SLACK_SECONDS = 600
LABELS_TRIMMED_FROM_MODEL = ("Claude",)
WHOLE_WORD_HINT_MAX = 4


_failed_commands = set()


def run_quiet(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        if args[0] not in _failed_commands:
            _failed_commands.add(args[0])
            log.warning("Could not run %s: %s", args[0], e)
        return None


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    return True


def process_start_time(pid):
    result = run_quiet(["ps", "-p", str(pid), "-o", "etime="])
    if not result or result.returncode != 0:
        return None
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", result.stdout.strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(v or 0) for v in match.groups())
    return time.time() - (((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def parse_started_at(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000 if value > 1e11 else float(value)
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), str(value).replace("Z", "+00:00"), count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def read_runtime(runtime_file):
    try:
        data = json.loads(runtime_file.read_text())
        return int(data["pid"]), parse_started_at(data["startedAt"])
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def t3_started_at(runtime_file):
    runtime = read_runtime(runtime_file)
    if not runtime or not pid_alive(runtime[0]):
        return None
    started = process_start_time(runtime[0])
    if started is None or not math.isfinite(runtime[1]) or abs(started - runtime[1]) > START_TIME_SLACK_SECONDS:
        return None
    return runtime[1]


def find_t3_state_dir(config):
    home = t3_home(config)
    for name in STATE_DIR_NAMES:
        started = t3_started_at(home / name / "server-runtime.json")
        if started is not None:
            return home / name, started
    result = run_quiet(["pgrep", "-f", config["process_pattern"]])
    return (home / DESKTOP_STATE_DIR, None) if result and result.returncode == 0 else (None, None)


_model_names_cache = {}


def model_names(state_dir):
    path = state_dir / "model-manifest.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _model_names_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    names = {}
    try:
        manifest = json.loads(path.read_text())["manifest"]
        for driver, provider in manifest.get("providers", {}).items():
            try:
                names[driver] = {
                    m["slug"]: m.get("shortName") or m["name"]
                    for m in provider.get("models", [])
                    if isinstance(m, dict) and m.get("slug") and m.get("name")
                }
            except (KeyError, TypeError, AttributeError):
                continue
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        names = cached[1] if cached else {}
    _model_names_cache[path] = (mtime, names)
    return names


CLAUDE_MODEL_PATTERNS = (
    re.compile(r"claude-(?P<family>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?(?:-\d{8})?"),
    re.compile(r"claude-(?P<major>\d+)(?:-(?P<minor>\d{1,2}))?-(?P<family>[a-z]+)(?:-\d{8})?"),
)


def claude_model_name(model):
    for pattern in CLAUDE_MODEL_PATTERNS:
        match = pattern.fullmatch(model)
        if match:
            version = match["major"] + (f".{match['minor']}" if match["minor"] else "")
            return f"Claude {match['family'].capitalize()} {version}"
    return None


def pretty_model(model, label=None, display_name=None):
    if not model:
        return None
    name = display_name or claude_model_name(model) or model
    if label in LABELS_TRIMMED_FROM_MODEL:
        rest = name[len(label) + 1:] if name.startswith(f"{label} ") else ""
        if rest[:1].isalpha():
            name = rest
    return name


def sessions_have_driver(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(orchestration_v2_projection_provider_sessions)")}
    return "driver" in columns


def read_t3_state(state_dir):
    db_path = state_dir / "statev2.sqlite"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        subagent_placeholders = ",".join("?" for _ in ACTIVE_SUBAGENT_STATUSES)
        active = conn.execute(
            f"""
            WITH active_threads AS (
              SELECT DISTINCT r.thread_id
              FROM orchestration_v2_projection_runs r
              JOIN orchestration_v2_projection_threads t USING (thread_id)
              WHERE r.status IN ({placeholders})
                AND t.deleted_at IS NULL
                AND r.requested_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-12 hours')
            ),
            active_subagents AS (
              SELECT s.subagent_id
              FROM orchestration_v2_projection_subagents s
              JOIN active_threads a ON a.thread_id = s.thread_id
              WHERE s.status IN ({subagent_placeholders})
                AND s.completed_at IS NULL
                AND COALESCE(s.started_at, s.updated_at) > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-12 hours')
                AND (s.child_thread_id IS NULL
                     OR s.child_thread_id NOT IN (SELECT thread_id FROM active_threads))
            )
            SELECT (SELECT COUNT(*) FROM active_threads) + (SELECT COUNT(*) FROM active_subagents) AS n
            """,
            ACTIVE_RUN_STATUSES + ACTIVE_SUBAGENT_STATUSES,
        ).fetchone()["n"]
        latest = conn.execute(
            f"""
            SELECT t.thread_id, t.title AS thread_title, p.title AS project_title,
                   t.default_provider AS provider
            FROM orchestration_v2_projection_threads t
            LEFT JOIN projection_projects p ON p.project_id = t.project_id
            LEFT JOIN orchestration_v2_projection_runs r
              ON r.thread_id = t.thread_id AND r.status IN ({placeholders})
            WHERE t.deleted_at IS NULL AND t.archived_at IS NULL
            ORDER BY (r.run_id IS NOT NULL) DESC, COALESCE(r.requested_at, t.updated_at) DESC
            LIMIT 1
            """,
            ACTIVE_RUN_STATUSES,
        ).fetchone()
        model, driver = None, None
        if latest:
            agent_column = "COALESCE(driver, provider)" if sessions_have_driver(conn) else "provider"
            newest = conn.execute(
                f"""
                SELECT {agent_column} AS driver, model
                FROM orchestration_v2_projection_provider_sessions
                WHERE thread_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (latest["thread_id"],),
            ).fetchone()
            if newest:
                driver, model = newest["driver"], newest["model"]
                if model is None:
                    row = conn.execute(
                        f"""
                        SELECT model FROM orchestration_v2_projection_provider_sessions
                        WHERE thread_id = ? AND model IS NOT NULL AND {agent_column} IS ?
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (latest["thread_id"], driver),
                    ).fetchone()
                    model = row["model"] if row else None
        state = {
            "active_agents": active,
            "project": latest["project_title"] if latest else None,
            "thread": latest["thread_title"] if latest else None,
            "model": model,
            "provider": driver or (latest["provider"] if latest else None),
        }
    finally:
        conn.close()
    state["model_name"] = model_names(state_dir).get(driver, {}).get(model) if driver and model else None
    return state


def clip(text, limit=128):
    text = (text or "").strip()
    if len(text) < 2:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


AGENTS = (
    ("claude", "Claude", ("claudeAgent", "claude", "claudeCode")),
    ("codex", "Codex", ("codex", "openai")),
    ("cursor", "Cursor", ("cursor",)),
    ("opencode", "OpenCode", ("opencode",)),
    ("grok", "Grok", ("grok",)),
    ("antigravity", "Antigravity", ("antigravity",)),
    ("antigravity", "Gemini", ("gemini",)),
    ("kiro", "Kiro", ("kiro",)),
    ("muse", "Muse Code", ("muse",)),
    ("pi", "Pi", ("pi",)),
    ("fx", "fx", ("fx",)),
    ("acp", "ACP Registry", ("acpRegistry", "acp")),
)
AGENT_NAMES = tuple((kind, label, tuple(n.lower() for n in names)) for kind, label, names in AGENTS)


def agent_kind(provider):
    raw = provider or ""
    lowered = raw.lower()
    words = [(m.start(), m.group().lower()) for m in re.finditer(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", raw)]
    for kind, label, names in AGENT_NAMES:
        if lowered in names:
            return kind, label
    best = None
    for kind, label, names in AGENT_NAMES:
        for name in names:
            hits = [pos for pos, word in words if word == name]
            if len(name) > WHOLE_WORD_HINT_MAX and name in lowered:
                hits.append(lowered.index(name))
            if hits and (best is None or min(hits) < best[0]):
                best = (min(hits), kind, label)
    if best:
        return best[1], best[2]
    return "other", "Agent"


def agent_image(config, kind):
    key = f"agent_{kind}"
    if not config.get("image_base_url") and key not in asset_urls(config["client_id"]):
        key = "agent_other"
    return image(config, key)


_asset_urls = {"fetched_at": 0, "urls": {}}


def asset_urls(client_id):
    if time.time() - _asset_urls["fetched_at"] < 600:
        return _asset_urls["urls"]
    _asset_urls["fetched_at"] = time.time()
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/oauth2/applications/{client_id}/assets?t={int(time.time())}",
            headers={"User-Agent": "t3code-discord-presence"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assets = json.load(resp)
        _asset_urls["urls"] = {
            a["name"]: f"https://cdn.discordapp.com/app-assets/{client_id}/{a['id']}.png" for a in assets
        }
    except (OSError, ValueError, KeyError) as e:
        log.warning("Could not load art list from Discord: %s", e)
        _asset_urls["fetched_at"] = time.time() - 540
    return _asset_urls["urls"]


def image(config, key):
    if key.startswith("http"):
        return key
    base = config.get("image_base_url")
    if base:
        return f"{base.rstrip('/')}/{key}.png"
    return asset_urls(config["client_id"]).get(key, key)


def build_activity(config, state, started_at):
    state = state or {}
    if config["show_thread_title"] and state.get("thread"):
        details = state["thread"]
    elif config["show_project"] and state.get("project"):
        details = f"Working on {state['project']}"
    else:
        details = "Coding with agents"

    kind, label = agent_kind(state.get("provider"))
    model = pretty_model(state.get("model"), label, state.get("model_name"))

    parts = []
    n = state.get("active_agents", 0)
    if config["show_agent_count"]:
        parts.append(f"{n} agent{'s' if n != 1 else ''} running" if n else config["idle_text"])
    if config["show_thread_title"] and config["show_project"] and state.get("project"):
        parts.insert(0, state["project"])
    if config["show_model"] and model:
        parts.append(model)

    assets = {"large_image": image(config, config["large_image"]), "large_text": config["large_text"]}
    if config["show_agent_avatar"]:
        same_name = model and (model == label or model.startswith(f"{label} "))
        who = model if same_name else f"{label} · {model}" if model else label
        assets["small_image"] = agent_image(config, kind) if n else image(config, "agent_idle")
        assets["small_text"] = f"{who} is cooking" if n else f"{who} is chilling"

    activity = {
        "details": clip(details),
        "state": clip(" · ".join(parts)),
        "timestamps": {"start": started_at},
        "assets": assets,
    }
    return {k: v for k, v in activity.items() if v}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = load_config()
    if not config["client_id"]:
        log.error("Set client_id in %s first. See README.md.", CONFIG_PATH)
        sys.exit(1)

    ipc = DiscordIPC(config["client_id"])
    started_at = None
    last_activity = None

    while True:
        try:
            state_dir, t3_started = find_t3_state_dir(config)
            if state_dir:
                started_at = int(t3_started) if t3_started is not None else started_at or int(time.time())
                activity = build_activity(config, read_t3_state(state_dir), started_at)
            else:
                started_at = None
                activity = None

            if ipc.sock is None:
                ipc.connect()
            ipc.set_activity(activity)
            if activity != last_activity:
                log.info("Presence: %s", activity or "cleared")
                last_activity = activity
            delay = config["poll_seconds"]
        except (ConnectionError, OSError, struct.error, json.JSONDecodeError) as e:
            if ipc.sock is not None or last_activity is not None:
                log.warning("%s. Retrying.", e)
            ipc.close()
            last_activity = None
            delay = 5
        except (sqlite3.Error, ValueError) as e:
            log.warning("%s", e)
            delay = config["poll_seconds"]
        time.sleep(delay)


if __name__ == "__main__":
    main()
