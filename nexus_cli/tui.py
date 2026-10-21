"""Full-colour interactive chat in the style of Claude Code / Gemini CLI."""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.segment import Segment, Segments
from rich.table import Table
from rich.text import Text

from . import __version__
from .core import CONFIG_DIR, NexusError, chat_events, get_json, list_models, model_info

# ── look ────────────────────────────────────────────────────────────────────
GRADIENT = ["#22d3ee", "#60a5fa", "#a78bfa", "#f472b6"]
ACCENT = "#a78bfa"
SHELL = "#f472b6"
DIM = "#6b7280"

LOGO = r"""
 ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
 ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
 ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
 ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
 ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
 ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝""".strip("\n")

SPINNER = "·✢✳✶✻✽✻✶✳✢"
VERBS = ["Thinking", "Pondering", "Computing", "Brewing", "Crunching", "Weaving", "Conjuring",
         "Cooking", "Mulling", "Processing", "Tinkering", "Noodling"]

COMMANDS = {
    "/help": "show commands and shortcuts",
    "/clear": "start a new conversation",
    "/model": "list models or switch: /model <id>",
    "/think": "turn Qwen3 thinking on/off",
    "/system": "set the system prompt: /system <text>",
    "/retry": "ask the last question again",
    "/thoughts": "show the model's last reasoning",
    "/write": "save the last code block: /write <file>",
    "/copy": "copy the last reply to the clipboard",
    "/save": "save the conversation: /save [file.md]",
    "/status": "server, model and context usage",
    "/exit": "quit",
}
NEEDS_ARG = {"/write"}

SHORTCUTS = [
    ("Enter", "send"),
    ("Esc then Enter / Ctrl+J", "new line"),
    ("↑ / ↓", "previous messages"),
    ("Tab / →", "accept suggestion"),
    ("Esc", "stop the reply"),
    ("Ctrl+C", "clear input · twice to quit"),
    ("@path", "add a file to your message"),
    ("!command", "run a shell command"),
    ("/", "commands"),
]


def _lerp(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def gradient_text(text: str, colors=GRADIENT) -> Text:
    lines = text.splitlines()
    width = max(len(l) for l in lines) or 1
    out = Text()
    for li, line in enumerate(lines):
        for i, ch in enumerate(line):
            pos = i / max(width - 1, 1) * (len(colors) - 1)
            k = min(int(pos), len(colors) - 2)
            out.append(ch, style=_lerp(colors[k], colors[k + 1], pos - k))
        if li < len(lines) - 1:
            out.append("\n")
    return out


def short_path(p: Path) -> str:
    try:
        return "~" + os.sep + str(p.relative_to(Path.home())) if p != Path.home() else "~"
    except ValueError:
        return str(p)


# ── input box ───────────────────────────────────────────────────────────────
class InputCompleter(Completer):
    """`/` → commands, `@` → file paths."""

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for cmd, desc in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)
            return
        m = re.search(r"@([^\s@]*)$", text)
        if not m:
            return
        typed = m.group(1)
        folder, _, prefix = typed.replace("\\", "/").rpartition("/")
        base = Path(folder or ".").expanduser()
        try:
            entries = sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        shown = 0
        for e in entries:
            if not e.name.lower().startswith(prefix.lower()) or (e.name.startswith(".") and not prefix.startswith(".")):
                continue
            rel = (folder + "/" if folder else "") + e.name + ("/" if e.is_dir() else "")
            yield Completion(rel, start_position=-len(typed), display=e.name + ("/" if e.is_dir() else ""),
                             display_meta="folder" if e.is_dir() else "")
            shown += 1
            if shown >= 50:
                return


def rounded_box(body, style_fn):
    def edge(char, **kw):
        return Window(char=char, style=lambda: style_fn(), **kw)
    return HSplit([
        VSplit([edge("╭", width=1, height=1), edge("─", height=1), edge("╮", width=1, height=1)]),
        VSplit([edge("│", width=1), Window(width=1), body, Window(width=1), edge("│", width=1)]),
        VSplit([edge("╰", width=1, height=1), edge("─", height=1), edge("╯", width=1, height=1)]),
    ])


# ── the app ─────────────────────────────────────────────────────────────────
class NexusTUI:
    def __init__(self, conf: dict, args):
        self.conf = conf
        self.args = args
        self.console = Console(highlight=False)
        self.history: list[dict] = []
        self.system = args.system
        self.think = args.think
        self.info = model_info(conf)
        self.last_usage: dict = {}
        self.last_thoughts = ""
        self.pending_context: list[str] = []
        self.exit_armed = False
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.prompt_history = FileHistory(str(CONFIG_DIR / "history"))

    # ── header ──
    def banner(self) -> None:
        c = self.console
        if c.width >= 46:
            c.print(gradient_text(LOGO))
        else:
            c.print(gradient_text("NEXUS"), style="bold")
        c.print()
        host = self.conf["url"].split("//", 1)[-1].removesuffix("/v1")
        name = self.info.get("display_name") or self.info.get("id")
        box = Table.grid(padding=(0, 1))
        box.add_row(Text("✻", style=ACCENT), Text.assemble(("Welcome to ", ""), ("NEXUS CLI", "bold"),
                                                           (f" v{__version__}", DIM)))
        box.add_row("", "")
        box.add_row("", Text("/help for help · ? for shortcuts", style=DIM))
        box.add_row("", "")
        box.add_row("", Text.assemble(("model: ", DIM), (str(name), ""), (f" on {host}", DIM)))
        box.add_row("", Text.assemble(("cwd:   ", DIM), short_path(Path.cwd())))
        from rich.panel import Panel
        c.print(Panel(box, border_style=ACCENT, expand=False, padding=(0, 2)))
        c.print(Text("Tips for getting started:", style="bold"))
        for i, tip in enumerate([
            "Ask questions, get help with code, or just chat.",
            "Add a file with @path, run a shell command with !command.",
            "Ask for a web page, then save it with /write page.html.",
        ], 1):
            c.print(Text(f"{i}. {tip}", style=DIM))
        c.print()

    # ── footer under the input box ──
    def context_left(self) -> int | None:
        ctx = self.info.get("context_length")
        used = self.last_usage.get("total_tokens")
        if not ctx or used is None:
            return None
        return max(0, round(100 * (1 - used / ctx)))

    def footer_left(self, text: str):
        if self.exit_armed:
            return [("#f59e0b", "  Press Ctrl+C again to exit")]
        if text.startswith("!"):
            return [(SHELL, "  ! shell mode"), (DIM, " · runs on this computer · backspace to leave")]
        if text == "?":
            return [(DIM, "  " + " · ".join(f"{k} {v}" for k, v in SHORTCUTS[:5]))]
        parts = [(DIM, "  ? for shortcuts")]
        if self.think:
            parts.append((ACCENT, " · ✻ thinking on"))
        if self.pending_context:
            parts.append((SHELL, f" · {len(self.pending_context)} shell result(s) attached"))
        return parts

    def footer_right(self):
        name = self.info.get("id", self.conf["model"])
        left = self.context_left()
        parts = [(DIM, name)]
        if left is not None:
            parts.append(("#f59e0b" if left < 25 else DIM, f" · {left}% context left"))
        return parts + [("", " ")]

    @staticmethod
    def menu_height(area):
        def height():
            state = area.buffer.complete_state
            return Dimension.exact(min(len(state.completions), 10) if state else 0)
        return height

    def read_input(self) -> str | None:
        """Shows the rounded input box; returns the text, or None to quit."""
        mode = {"shell": False}  # `!` on an empty line switches the box to shell mode
        area = TextArea(
            prompt=lambda: [(SHELL + " bold", "! ")] if mode["shell"] else [(ACCENT + " bold", "> ")],
            multiline=True, wrap_lines=True, completer=InputCompleter(), complete_while_typing=True,
            history=self.prompt_history, auto_suggest=AutoSuggestFromHistory(),
            height=Dimension(min=1, max=12), dont_extend_height=True,
        )
        area.buffer.on_text_changed += lambda _: setattr(self, "exit_armed", False)
        kb = KeyBindings()

        empty = Condition(lambda: not area.text)

        @kb.add("!", filter=empty & Condition(lambda: not mode["shell"]))
        def _(event):
            mode["shell"] = True

        @kb.add("backspace", filter=empty & Condition(lambda: mode["shell"]))
        def _(event):
            mode["shell"] = False

        def submit(event, text):
            event.app.exit(result=("!" + text) if mode["shell"] else text)

        @kb.add("enter", filter=has_completions)
        def _(event):
            buf = event.current_buffer
            state = buf.complete_state
            comp = state.current_completion or (state.completions[0] if state.completions else None)
            if comp:
                buf.apply_completion(comp)
            if buf.text.startswith("/") and " " not in buf.text and buf.text not in NEEDS_ARG:
                event.app.exit(result=buf.text)
            elif buf.text in NEEDS_ARG:
                buf.insert_text(" ")

        @kb.add("enter")
        def _(event):
            buf = event.current_buffer
            if buf.text.strip():
                buf.append_to_history()
                submit(event, buf.text)

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _(event):
            if event.current_buffer.text:
                event.current_buffer.reset()
                self.exit_armed = False
            elif self.exit_armed:
                event.app.exit(result=None)
            else:
                self.exit_armed = True

        @kb.add("c-d")
        def _(event):
            if not event.current_buffer.text:
                event.app.exit(result=None)

        @kb.add("right", filter=Condition(lambda: area.buffer.suggestion is not None
                                         and area.buffer.document.is_cursor_at_the_end))
        @kb.add("tab", filter=Condition(lambda: area.buffer.suggestion is not None
                                       and not area.buffer.complete_state))
        def _(event):
            buf = event.current_buffer
            buf.insert_text(buf.suggestion.text)

        border = lambda: SHELL if mode["shell"] else DIM  # noqa: E731
        footer = VSplit([
            Window(FormattedTextControl(lambda: FormattedText(
                self.footer_left("!" if mode["shell"] else area.text))), height=1),
            Window(FormattedTextControl(lambda: FormattedText(self.footer_right())), height=1,
                   align=WindowAlign.RIGHT),
        ])
        # suggestions sit under the box (like Claude Code) instead of floating over it
        root = HSplit([
            rounded_box(area, border),
            HSplit([CompletionsMenu(max_height=10, scroll_offset=1)], height=self.menu_height(area)),
            ConditionalContainer(footer, filter=~has_completions),
        ])
        style = Style.from_dict({
            "completion-menu": "bg:#1f2937 #d1d5db",
            "completion-menu.completion.current": f"bg:{ACCENT} #111827",
            "completion-menu.meta.completion": "bg:#1f2937 #9ca3af",
            "completion-menu.meta.completion.current": f"bg:{ACCENT} #111827",
            "auto-suggestion": DIM,
        })
        app = Application(layout=Layout(root, focused_element=area), key_bindings=kb, style=style,
                          full_screen=False, erase_when_done=True, mouse_support=False)
        return app.run()

    # ── main loop ──
    def run(self) -> int:
        self.banner()
        while True:
            try:
                text = self.read_input()
            except (EOFError, KeyboardInterrupt):
                text = None
            if text is None:
                self.console.print(Text("Bye 👋", style=DIM))
                return 0
            text = text.strip()
            if not text:
                continue
            if text == "?":
                self.show_help(commands=False)
            elif text.startswith("/"):
                if self.slash(text) == "exit":
                    self.console.print(Text("Bye 👋", style=DIM))
                    return 0
            elif text.startswith("!"):
                self.shell(text[1:].strip())
            else:
                self.send(text)

    # ── user messages ──
    def echo(self, text: str, style: str = "", mark: str = ">") -> None:
        t = Text()
        for i, line in enumerate(text.splitlines() or [""]):
            t.append((mark + " " if i == 0 else "  ") + line + "\n", style=style or "#9ca3af")
        self.console.print(t, end="")
        self.console.print()

    def expand_files(self, text: str) -> str:
        attached = []
        for m in re.finditer(r"@([^\s@]+)", text):
            path = Path(m.group(1)).expanduser()
            if not path.is_file():
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.note(f"Couldn't read {path} as text, skipped", "#f59e0b")
                continue
            limit = 40_000
            lines = len(body.splitlines())
            if len(body) > limit:
                body = body[:limit] + "\n… (truncated)"
            attached.append(f"Contents of {m.group(1)}:\n```\n{body}\n```")
            self.note(f"Read {m.group(1)} ({lines} lines)")
        return text + ("\n\n" + "\n\n".join(attached) if attached else "")

    def note(self, msg: str, style: str = DIM) -> None:
        self.console.print(Text.assemble(("  ⎿  ", DIM), (msg, style)))

    def send(self, text: str, echo: bool = True) -> None:
        if echo:
            self.echo(text)
        content = self.expand_files(text)
        if self.pending_context:
            content = "\n\n".join(self.pending_context) + "\n\n" + content
            self.pending_context.clear()
        self.history.append({"role": "user", "content": content})
        self.fit_context()
        self.generate()

    def fit_context(self) -> None:
        """Drops the oldest turns when the conversation won't fit in the model's memory."""
        ctx = self.info.get("context_length")
        if not ctx:
            return
        budget = ctx - self.reply_budget()
        dropped = 0
        def size():
            return sum(len(m["content"]) for m in self.messages()) / 3.2 + 8 * len(self.history)
        while len(self.history) > 1 and size() > budget:
            self.history.pop(0)
            if self.history and self.history[0]["role"] == "assistant":
                self.history.pop(0)
            dropped += 1
        if dropped:
            self.note(f"Dropped the oldest messages to fit the model's {ctx}-token memory", "#f59e0b")

    def reply_budget(self) -> int:
        if self.args.max_tokens:
            return self.args.max_tokens
        ctx = self.info.get("context_length") or 4096
        return max(512, ctx // 2) if self.think else 512

    def messages(self) -> list:
        return ([{"role": "system", "content": self.system}] if self.system else []) + self.history

    # ── generation ──
    def generate(self) -> None:
        opts = {"max_tokens": self.reply_budget()}
        if self.args.temperature is not None:
            opts["temperature"] = self.args.temperature
        if self.think:
            opts["enable_thinking"] = True
        result: dict = {}
        verb = random.choice(VERBS)
        start = time.time()
        think_start = None
        thoughts: list[str] = []
        answer = ""
        committed = 0  # chars of `answer` already printed for good
        printed_lines = 0  # rendered lines of answer[:committed] already on screen
        stop = threading.Event()

        def spinner_line():
            el = int(time.time() - start)
            frame = SPINNER[int(time.time() * 8) % len(SPINNER)]
            toks = result.get("chunks", 0)
            label = "Thinking" if think_start and not answer else verb
            line = Text.assemble((f"{frame} ", ACCENT), (f"{label}…", ACCENT),
                                 (f" ({el}s · ↓ {toks} tokens · esc to interrupt)", DIM))
            if thoughts and not answer:
                tail = "".join(thoughts)[-200:].replace("\n", " ").strip()
                width = max(self.console.width - 6, 20)
                line.append("\n  " + (tail[-width:] if len(tail) > width else tail), style=f"italic {DIM}")
            return line

        def block(md: str, first: bool):
            grid = Table.grid(padding=(0, 1))
            grid.add_column(width=1, no_wrap=True)
            grid.add_column(ratio=1)
            grid.add_row(Text("●", style="white") if first else Text(" "), Markdown(md, code_theme="monokai"))
            return grid

        def safe_split(text: str) -> int:
            """Index up to which `text` is complete paragraphs outside a code block."""
            best, pos = 0, 0
            while True:
                i = text.find("\n\n", pos)
                if i < 0:
                    return best
                if text[:i].count("```") % 2 == 0:
                    best = i + 2
                pos = i + 2

        def commit(console, upto: int) -> None:
            """Prints answer[:upto] rendered as one document, skipping lines already shown."""
            nonlocal printed_lines
            lines = console.render_lines(block(answer[:upto].rstrip(), True), console.options, pad=False)
            new = lines[printed_lines:]
            printed_lines = len(lines)
            if new:
                console.print(Segments([seg for line in new for seg in (*line, Segment.line())]), end="")

        def render():
            pending = answer[committed:]
            parts = []
            if pending.strip():
                parts.append(Padding(block(pending, not committed), (1 if committed else 0, 0, 0, 0)))
            parts.append(Padding(spinner_line(), (1 if parts else 0, 0, 0, 0)))
            return Group(*parts)

        interrupted = False
        with KeyWatcher(stop), Live(render(), console=self.console, refresh_per_second=12,
                                    transient=True) as live:
            try:
                events = chat_events(self.conf, self.messages(), opts, result, not self.args.no_stream)
                for kind, piece in events:
                    if stop.is_set():
                        interrupted = True
                        events.close()
                        break
                    if kind == "think":
                        think_start = think_start or time.time()
                        thoughts.append(piece)
                    else:
                        if not answer and think_start:
                            live.console.print(Text(f"✻ Thought for {time.time() - think_start:.0f}s"
                                                    " · /thoughts to read", style=f"italic {DIM}"))
                            live.console.print()
                        # print + redraw under Live's lock so its refresh thread can't paint stale text
                        with live._lock:
                            answer += piece
                            cut = committed + safe_split(answer[committed:])
                            if cut > committed and answer[committed:cut].strip():
                                commit(live.console, cut)
                                committed = cut
                            live.update(render())
                        continue
                    live.update(render())
                if stop.is_set():
                    interrupted = True
            except KeyboardInterrupt:
                interrupted = True
            except NexusError as e:
                live.stop()
                self.console.print(Text(f"● {e}", style="red"))
                self.console.print()
                self.history.pop()
                return
            live.update(Text(""))
        if answer[committed:].strip():
            commit(self.console, len(answer))
        elif not answer and not interrupted:
            self.console.print(block("*(empty reply)*", True))
        self.last_thoughts = "".join(thoughts).strip()

        secs = time.time() - start
        usage = result.get("usage") or {}
        if usage:
            self.last_usage = usage
        toks = usage.get("completion_tokens") or result.get("chunks", 0)
        stats = f"{toks} tokens · {toks / secs:.1f} tok/s · {secs:.1f}s" if secs > 0 else ""
        if interrupted:
            self.note("Interrupted · what should NEXUS do instead?", "#f59e0b")
        elif result.get("finish_reason") == "length":
            why = "ran out of room while thinking" if result.get("still_thinking") else "reply hit the length limit"
            self.note(f"Cut off: {why} · {stats}", "#f59e0b")
        else:
            self.note(stats)
        self.console.print()
        if answer.strip():
            self.history.append({"role": "assistant", "content": answer.strip()})
        elif self.history and self.history[-1]["role"] == "user":
            self.history.pop()

    # ── shell ──
    def shell(self, cmd: str) -> None:
        if not cmd:
            return
        self.echo(cmd, style=SHELL, mark="!")
        argv = (["powershell", "-NoProfile", "-Command", cmd] if os.name == "nt"
                else [os.environ.get("SHELL", "/bin/sh"), "-c", cmd])
        try:
            with self.console.status(Text(f"Running {cmd}", style=DIM), spinner="dots"):
                p = subprocess.run(argv, capture_output=True, text=True, timeout=300,
                                   encoding="utf-8", errors="replace")
            out = (p.stdout + p.stderr).rstrip()
            code = p.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            out, code = str(e), -1
        self.console.print(Text.assemble(("● ", SHELL), ("Shell", "bold"), (f"({cmd})", "")))
        lines = out.splitlines() or ["(no output)"]
        for i, line in enumerate(lines[:12]):
            self.console.print(Text.assemble(("  ⎿  " if i == 0 else "     ", DIM), (line, DIM)))
        if len(lines) > 12:
            self.console.print(Text(f"     … +{len(lines) - 12} lines (all shared with NEXUS)", style=DIM))
        if code:
            self.console.print(Text(f"     exit code {code}", style="red"))
        self.console.print()
        self.pending_context.append(
            f"I ran `{cmd}` in my shell (exit code {code}). Output:\n```\n{out[-6000:]}\n```")

    # ── slash commands ──
    def slash(self, text: str):
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("/exit", "/quit", "/q"):
            return "exit"
        self.echo(text, style=ACCENT)
        if cmd == "/help":
            self.show_help()
        elif cmd == "/clear":
            self.history.clear()
            self.pending_context.clear()
            self.last_usage = {}
            self.console.clear()
            self.banner()
            self.note("Started a new conversation")
        elif cmd == "/think":
            self.think = not self.think
            if self.think and not self.info.get("supports_thinking", True):
                self.note("This model may not support thinking", "#f59e0b")
            self.note(f"Thinking {'on: slower, but better at hard questions' if self.think else 'off'}")
        elif cmd == "/system":
            if arg:
                self.system = arg
                self.history.clear()
                self.note("System prompt set · conversation cleared")
            else:
                self.note(f"System prompt: {self.system or '(none)'} · set one with /system <text>")
        elif cmd == "/model":
            self.model_cmd(arg)
        elif cmd == "/retry":
            if self.history and self.history[-1]["role"] == "assistant":
                self.history.pop()
            if self.history and self.history[-1]["role"] == "user":
                self.generate()
            else:
                self.note("Nothing to retry yet")
        elif cmd == "/thoughts":
            if self.last_thoughts:
                self.console.print(Padding(Text(self.last_thoughts, style=f"italic {DIM}"), (0, 0, 0, 5)))
            else:
                self.note("No reasoning yet · turn it on with /think")
        elif cmd == "/write":
            self.write_cmd(arg)
        elif cmd == "/copy":
            self.copy_cmd()
        elif cmd == "/save":
            self.save_cmd(arg)
        elif cmd == "/status":
            self.status_cmd()
        else:
            self.note(f"Unknown command {cmd} · /help lists them", "red")
        self.console.print()

    def show_help(self, commands: bool = True) -> None:
        t = Table.grid(padding=(0, 3))
        t.add_column(style=ACCENT, no_wrap=True)
        t.add_column(style=DIM)
        if commands:
            self.console.print(Text("Commands", style="bold"))
            for c, d in COMMANDS.items():
                t.add_row("  " + c, d)
            self.console.print(t)
            self.console.print()
            t = Table.grid(padding=(0, 3))
            t.add_column(style=ACCENT, no_wrap=True)
            t.add_column(style=DIM)
        self.console.print(Text("Shortcuts", style="bold"))
        for k, d in SHORTCUTS:
            t.add_row("  " + k, d)
        self.console.print(t)
        self.console.print()

    def last_reply(self) -> str:
        return next((m["content"] for m in reversed(self.history) if m["role"] == "assistant"), "")

    def write_cmd(self, arg: str) -> None:
        if not arg:
            self.note("Usage: /write <file>, e.g. /write index.html", "#f59e0b")
            return
        blocks = re.findall(r"```[^\n]*\n(.*?)```", self.last_reply(), re.S)
        if not blocks:
            self.note("The last reply has no code block to save", "#f59e0b")
            return
        code = max(blocks, key=len)
        path = Path(arg).expanduser()
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        self.console.print(Text.assemble(("● ", "#34d399"), ("Write", "bold"), (f"({arg})", "")))
        self.note(f"{'Overwrote' if existed else 'Wrote'} {code.count(chr(10)) + 1} lines to {path.resolve()}")

    def copy_cmd(self) -> None:
        text = self.last_reply()
        if not text:
            self.note("Nothing to copy yet")
            return
        try:
            if os.name == "nt":
                subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
            self.note("Copied the last reply")
        except (OSError, subprocess.CalledProcessError):
            self.note("No clipboard tool found", "#f59e0b")

    def save_cmd(self, arg: str) -> None:
        path = Path(arg or f"nexus-chat-{time.strftime('%Y%m%d-%H%M%S')}.md").expanduser()
        lines = [f"## {'You' if m['role'] == 'user' else 'NEXUS'}\n\n{m['content']}\n" for m in self.history]
        path.write_text("\n".join(lines), encoding="utf-8")
        self.note(f"Saved {len(self.history)} messages to {path.resolve()}")

    def model_cmd(self, arg: str) -> None:
        try:
            models = list_models(self.conf)
        except NexusError as e:
            self.note(str(e), "red")
            return
        ids = [m["id"] for m in models]
        if not arg:
            for m in models:
                n = m.get("nexus") or {}
                mark = "●" if m["id"] == self.info.get("id") else " "
                extra = f"  {n.get('display_name', '')} · context {n.get('context_length', '?')}" \
                    if n.get("context_length") else ""
                self.console.print(Text.assemble((f"  {mark} ", ACCENT), m["id"], (extra, DIM)))
            self.note("Switch with /model <id>. The phone app decides which model is loaded.")
            return
        if arg not in ids:
            self.note(f"No model {arg!r}. Available: {', '.join(ids)}", "red")
            return
        self.conf["model"] = arg
        self.info = model_info(self.conf)
        self.note(f"Using {arg}")

    def status_cmd(self) -> None:
        try:
            t0 = time.time()
            health = get_json(self.conf, "/health")
            ms = (time.time() - t0) * 1000
            ok = Text(f"ok ({ms:.0f} ms)", style="#34d399") if health.get("status") == "ok" \
                else Text(str(health), style="red")
        except NexusError as e:
            ok = Text(str(e).splitlines()[0], style="red")
        t = Table.grid(padding=(0, 2))
        t.add_column(style=DIM)
        t.add_column()
        left = self.context_left()
        rows = [
            ("Server", self.conf["url"]), ("Health", ok),
            ("Model", f"{self.info.get('display_name', '')} ({self.info.get('id')})"),
            ("Context", f"{self.info.get('context_length', '?')} tokens"
                        + (f" · {left}% left" if left is not None else "")),
            ("Thinking", "on" if self.think else "off"),
            ("System prompt", self.system or "(none)"),
            ("Messages", str(len(self.history))),
        ]
        for k, v in rows:
            t.add_row("  " + k, v)
        self.console.print(t)


class KeyWatcher:
    """Sets `stop` when Esc is pressed while a reply is streaming."""

    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.done = threading.Event()
        self.thread = None
        self.saved = None

    def __enter__(self):
        if not sys.stdin.isatty():
            return self
        if os.name != "nt":
            import termios
            import tty
            fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()
        return self

    def _watch(self):
        if os.name == "nt":
            import msvcrt
            while not self.done.is_set():
                if msvcrt.kbhit() and msvcrt.getwch() == "\x1b":
                    self.stop.set()
                time.sleep(0.05)
        else:
            import select
            fd = sys.stdin.fileno()
            while not self.done.is_set():
                r, _, _ = select.select([fd], [], [], 0.05)
                if r and os.read(fd, 1) == b"\x1b":
                    self.stop.set()

    def __exit__(self, *exc):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)
        return False


def run_tui(conf: dict, args) -> int:
    return NexusTUI(conf, args).run()
