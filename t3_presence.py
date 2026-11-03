#!/usr/bin/env python3
"""Discord Rich Presence for T3 Code. Standard library only."""

import json
import logging
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
DB_PATH = Path.home() / ".t3" / "userdata" / "statev2.sqlite"
ACTIVE_RUN_STATUSES = ("preparing", "starting", "running", "waiting")

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
        try:
            dirs.append(subprocess.run(
                ["getconf", "DARWIN_USER_TEMP_DIR"],
                capture_output=True, text=True, check=False,
            ).stdout.strip())
        except OSError:
            pass
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


def t3_is_running(pattern):
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, check=False)
    return result.returncode == 0


def pretty_model(model):
    if not model:
        return None
    match = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model)
    if match:
        family, major, minor = match.groups()
        return f"{family.capitalize()} {major}.{minor}"
    return model


def read_t3_state():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        active = conn.execute(
            f"""
            SELECT COUNT(DISTINCT r.thread_id) AS n
            FROM orchestration_v2_projection_runs r
            JOIN orchestration_v2_projection_threads t USING (thread_id)
            WHERE r.status IN ({placeholders})
              AND t.deleted_at IS NULL
              AND r.requested_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-12 hours')
            """,
            ACTIVE_RUN_STATUSES,
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
        model = None
        if latest:
            row = conn.execute(
                """
                SELECT model FROM orchestration_v2_projection_provider_sessions
                WHERE thread_id = ? AND model IS NOT NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (latest["thread_id"],),
            ).fetchone()
            model = row["model"] if row else None
        return {
            "active_agents": active,
            "project": latest["project_title"] if latest else None,
            "thread": latest["thread_title"] if latest else None,
            "model": model,
            "provider": latest["provider"] if latest else None,
        }
    finally:
        conn.close()


def clip(text, limit=128):
    text = (text or "").strip()
    if len(text) < 2:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def agent_kind(provider):
    provider = (provider or "").lower()
    if "claude" in provider:
        return "claude"
    if "codex" in provider:
        return "codex"
    return "other"


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

    parts = []
    n = state.get("active_agents", 0)
    if config["show_agent_count"]:
        parts.append(f"{n} agent{'s' if n != 1 else ''} running" if n else config["idle_text"])
    if config["show_thread_title"] and config["show_project"] and state.get("project"):
        parts.insert(0, state["project"])
    if config["show_model"] and (model := pretty_model(state.get("model"))):
        parts.append(model)

    assets = {"large_image": image(config, config["large_image"]), "large_text": config["large_text"]}
    if config["show_agent_avatar"]:
        kind = agent_kind(state.get("provider"))
        label = {"claude": "Claude", "codex": "Codex"}.get(kind, "Agent")
        model = pretty_model(state.get("model"))
        who = f"{label} · {model}" if model and model != label else label
        assets["small_image"] = image(config, f"agent_{kind}" if n else "agent_idle")
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
            if t3_is_running(config["process_pattern"]):
                started_at = started_at or int(time.time())
                activity = build_activity(config, read_t3_state(), started_at)
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
