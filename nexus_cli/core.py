"""Config, HTTP and streaming: everything that doesn't draw on the screen."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("NEXUS_HOME", Path.home() / ".nexus"))
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_MODEL = "nexus-local"


class NexusError(Exception):
    pass


# ── config ──────────────────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def resolve(args) -> dict:
    """Flags beat environment variables, which beat the saved config."""
    cfg = load_config()
    url = getattr(args, "url", None) or os.environ.get("NEXUS_URL") or cfg.get("url")
    key = getattr(args, "key", None) or os.environ.get("NEXUS_API_KEY") or cfg.get("api_key")
    if not url:
        raise NexusError("No server set. Run `nexus setup` first (or pass --url).")
    return {
        "url": normalize_url(url),
        "key": key,
        "model": getattr(args, "model", None) or cfg.get("model") or DEFAULT_MODEL,
    }


# ── HTTP ────────────────────────────────────────────────────────────────────
def request(conf: dict, method: str, path: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if conf.get("key"):
        headers["Authorization"] = "Bearer " + conf["key"]
    base = conf["url"][: -len("/v1")] if path == "/health" else conf["url"]
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        if e.code == 401:
            raise NexusError(f"Wrong or missing API key (HTTP 401). {detail}") from None
        raise NexusError(f"Server error HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise NexusError(
            f"Can't reach {base}: {reason}\n"
            "  Is the NEXUS server running in LAN mode, and are both devices on the same Wi-Fi?"
        ) from None


def get_json(conf: dict, path: str, timeout: float = 15):
    with request(conf, "GET", path, timeout=timeout) as resp:
        return json.loads(resp.read())


def list_models(conf: dict) -> list:
    return get_json(conf, "/models").get("data", [])


def model_info(conf: dict) -> dict:
    """The `nexus` block for the selected model (display name, context length...)."""
    try:
        for m in list_models(conf):
            if m["id"] == conf["model"]:
                return {"id": m["id"], **(m.get("nexus") or {})}
    except NexusError:
        pass
    return {"id": conf["model"]}


# ── streaming ───────────────────────────────────────────────────────────────
class ThinkSplitter:
    """Splits a Qwen3 stream into ("think", text) and ("text", text) parts."""

    def __init__(self):
        self.in_think = False
        self.buf = ""
        self.started = False

    def feed(self, piece: str):
        self.buf += piece
        out = []
        while self.buf:
            tag = "</think>" if self.in_think else "<think>"
            idx = self.buf.find(tag)
            if idx >= 0:
                out.append(self._part(self.buf[:idx]))
                self.buf = self.buf[idx + len(tag):]
                self.in_think = not self.in_think
                continue
            # hold back a half-received tag until the next piece
            keep = next((k for k in range(len(tag) - 1, 0, -1) if self.buf.endswith(tag[:k])), 0)
            out.append(self._part(self.buf[: len(self.buf) - keep]))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return [p for p in out if p]

    def flush(self):
        part = self._part(self.buf)
        self.buf = ""
        return [part] if part else []

    def _part(self, text: str):
        if not text:
            return None
        if self.in_think:
            return ("think", text)
        if not self.started:
            text = text.lstrip()  # Qwen3 leaves blank lines after its think block
            if not text:
                return None
            self.started = True
        return ("text", text)


def chat_events(conf: dict, messages: list, opts: dict, result: dict, stream: bool = True):
    """Yields ("think"|"text", str). Fills result with finish_reason and usage."""
    splitter = ThinkSplitter()
    body = {"model": conf["model"], "messages": messages, "stream": stream, **opts}
    if stream:
        body["stream_options"] = {"include_usage": True}
    with request(conf, "POST", "/chat/completions", body, timeout=900) as resp:
        if not stream:
            data = json.loads(resp.read())
            choice = data["choices"][0]
            result["finish_reason"] = choice.get("finish_reason")
            result["usage"] = data.get("usage")
            yield from splitter.feed(choice["message"].get("content") or "")
            yield from splitter.flush()
            return
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if "error" in chunk:
                err = chunk["error"]
                raise NexusError(str(err.get("message", err) if isinstance(err, dict) else err))
            if chunk.get("usage"):
                result["usage"] = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("finish_reason"):
                    result["finish_reason"] = choice["finish_reason"]
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    result["chunks"] = result.get("chunks", 0) + 1
                    yield from splitter.feed(piece)
        yield from splitter.flush()
    result["still_thinking"] = splitter.in_think
