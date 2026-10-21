"""Command-line entry point: setup / status / models / ask / chat."""
from __future__ import annotations

import argparse
import os
import stat
import sys
import time

from . import __version__
from .core import (CONFIG_PATH, DEFAULT_MODEL, NexusError, chat_events, get_json, list_models,
                   load_config, model_info, normalize_url, resolve, save_config)

if os.name == "nt":
    os.system("")  # turns on ANSI escape handling in the Windows console
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t): return _c("2", t)
def bold(t): return _c("1", t)
def green(t): return _c("32", t)
def red(t): return _c("31", t)


def gen_opts(args, info: dict | None = None) -> dict:
    opts = {}
    if args.temperature is not None:
        opts["temperature"] = args.temperature
    if args.max_tokens is not None:
        opts["max_tokens"] = args.max_tokens
    if args.think:
        opts["enable_thinking"] = True
        if "max_tokens" not in opts:
            # thinking alone often uses up the server's 512-token default
            ctx = (info or {}).get("context_length") or 4096
            opts["max_tokens"] = max(512, ctx * 3 // 4)
    return opts


# ── plain (non-interactive) commands ────────────────────────────────────────
def cmd_setup(args) -> int:
    cfg = load_config()
    print(bold("NEXUS setup") + dim(f"  (saved to {CONFIG_PATH})"))
    print(dim("The server address is shown in the NEXUS app, e.g. http://192.168.1.50:8080"))
    url = args.url or input(f"Server address [{cfg.get('url', '')}]: ").strip() or cfg.get("url", "")
    key = args.key or input("API key [keep current]: " if cfg.get("api_key") else "API key: ").strip() \
        or cfg.get("api_key", "")
    if not url:
        print(red("No address given, nothing saved."))
        return 1
    conf = {"url": normalize_url(url), "key": key, "model": cfg.get("model") or DEFAULT_MODEL}
    print(f"Testing {conf['url']} ...")
    try:
        health = get_json(conf, "/health")
        models = [m["id"] for m in list_models(conf)]
        print(green("✓ Connected.") + f" Model loaded: {health.get('model_loaded')}. Models: {', '.join(models)}")
    except NexusError as e:
        print(red(f"✗ {e}"))
        if input("Save anyway? [y/N]: ").strip().lower() != "y":
            return 1
    cfg.update({"url": conf["url"], "api_key": key})
    save_config(cfg)
    print(green("Saved.") + " Now try: " + bold("nexus"))
    return 0


def cmd_status(args) -> int:
    conf = resolve(args)
    print(f"Server: {conf['url']}")
    t0 = time.time()
    health = get_json(conf, "/health")
    ok = health.get("status") == "ok"
    print(f"Health: {green('ok') if ok else red(str(health))}  ({(time.time() - t0) * 1000:.0f} ms)")
    print(f"Model loaded: {health.get('model_loaded')}")
    for m in list_models(conf):
        n = m.get("nexus") or {}
        extra = f"  {n.get('display_name', '')}, context {n['context_length']}" if n.get("context_length") else ""
        print(f"  • {m['id']}{dim(extra)}")
    return 0 if ok else 1


def cmd_models(args) -> int:
    for m in list_models(resolve(args)):
        print(m["id"])
    return 0


def _print_events(events, show_thinking: bool) -> None:
    thinking = False
    for kind, text in events:
        if kind == "think":
            if show_thinking:
                sys.stdout.write(dim(text))
            thinking = True
        else:
            if thinking and show_thinking:
                sys.stdout.write("\n\n")
            thinking = False
            sys.stdout.write(text)
        sys.stdout.flush()


def _stdin_has_data() -> bool:
    """True only for real pipes/files, so `nexus ask` never waits on an idle stdin."""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError, AttributeError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISREG(mode)


def cmd_ask(args) -> int:
    conf = resolve(args)
    prompt = " ".join(args.prompt).strip()
    if _stdin_has_data():
        piped = sys.stdin.read().strip()
        prompt = f"{prompt}\n\n{piped}".strip() if piped else prompt
    if not prompt:
        raise NexusError('Nothing to ask. Example: nexus ask "What is the capital of Japan?"')
    messages = [{"role": "system", "content": args.system}] if args.system else []
    messages.append({"role": "user", "content": prompt})
    result: dict = {}
    info = model_info(conf) if args.think else {}
    _print_events(chat_events(conf, messages, gen_opts(args, info), result, not args.no_stream), args.think)
    print()
    if result.get("finish_reason") == "length":
        print(dim("[cut off: hit the length limit, raise it with --max-tokens]"), file=sys.stderr)
    return 0


def cmd_chat(args) -> int:
    conf = resolve(args)
    if sys.stdin.isatty() and sys.stdout.isatty() and not args.plain:
        try:
            from .tui import run_tui
        except ImportError as e:  # rich / prompt_toolkit missing
            print(dim(f"(fancy UI unavailable: {e}; using plain chat)"))
        else:
            return run_tui(conf, args)
    return plain_chat(conf, args)


def plain_chat(conf: dict, args) -> int:
    """Line-by-line chat for pipes, old terminals and --plain."""
    history: list = []
    info = model_info(conf)
    print(bold("NEXUS chat") + dim(f"  {conf['url']}  model={info.get('id')}  (type /exit to quit)"))
    while True:
        try:
            text = input("\nYou › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return 0
        if text == "/clear":
            history.clear()
            print(dim("Conversation cleared."))
            continue
        history.append({"role": "user", "content": text})
        messages = ([{"role": "system", "content": args.system}] if args.system else []) + history
        sys.stdout.write("AI  › ")
        reply: list = []

        def collect(events):
            for kind, t in events:
                if kind == "text":
                    reply.append(t)
                yield kind, t
        try:
            _print_events(collect(chat_events(conf, messages, gen_opts(args, info), {}, not args.no_stream)),
                          args.think)
        except NexusError as e:
            print(red(f"\n{e}"))
            history.pop()
            continue
        print()
        history.append({"role": "assistant", "content": "".join(reply).strip()})


# ── entry point ─────────────────────────────────────────────────────────────
COMMANDS = ("setup", "status", "models", "chat", "ask")


def build_parser() -> argparse.ArgumentParser:
    conn = argparse.ArgumentParser(add_help=False)
    conn.add_argument("--url", help="server address, e.g. http://192.168.1.50:8080 (env NEXUS_URL)")
    conn.add_argument("--key", help="API key (env NEXUS_API_KEY)")

    gen = argparse.ArgumentParser(add_help=False)
    gen.add_argument("-m", "--model", help=f"model id (default {DEFAULT_MODEL})")
    gen.add_argument("-s", "--system", help="system prompt")
    gen.add_argument("-t", "--temperature", type=float, help="0–2, default from server (0.7)")
    gen.add_argument("--max-tokens", type=int, help="limit reply length")
    gen.add_argument("--think", action="store_true", help="let Qwen3 reason first")
    gen.add_argument("--no-stream", action="store_true", help="wait for the full reply")

    p = argparse.ArgumentParser(prog="nexus", description="Chat with a NEXUS AI Server from the terminal. "
                                "Run `nexus` with no command to open the chat.")
    p.add_argument("-V", "--version", action="version", version=f"nexus {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("setup", parents=[conn], help="save server address and API key").set_defaults(func=cmd_setup)
    sub.add_parser("status", parents=[conn], help="check the server").set_defaults(func=cmd_status)
    sub.add_parser("models", parents=[conn], help="list models").set_defaults(func=cmd_models)
    chat = sub.add_parser("chat", parents=[conn, gen], help="interactive chat (default)")
    chat.add_argument("--plain", action="store_true", help="simple line-by-line chat, no fancy UI")
    chat.set_defaults(func=cmd_chat)
    ask = sub.add_parser("ask", parents=[conn, gen], help="ask one question")
    ask.add_argument("prompt", nargs="*", help="the question (stdin is appended if piped)")
    ask.set_defaults(func=cmd_ask)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a in COMMANDS or a in ("-h", "--help", "-V", "--version") for a in argv[:1]):
        argv.insert(0, "chat")  # plain `nexus` (with or without options) opens the chat
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NexusError as e:
        print(red(str(e)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    except BrokenPipeError:  # e.g. `nexus ask ... | head`
        sys.stderr.close()
        return 0
