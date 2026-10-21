# nexus CLI

Chat with your [NEXUS AI Server] (a phone running a local LLM) from any computer's terminal,
in a Claude Code / Gemini CLI style interface.

## Install

**macOS / Linux / Android (Termux)**
```bash
curl -fsSL https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.sh | bash
```

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.ps1 | iex
```

That one command does everything: if the computer has no usable Python, it downloads a private
copy (via [uv](https://github.com/astral-sh/uv)); it installs into `~/.nexus/cli` so nothing else on
your computer is touched, puts `nexus` on your PATH, and the first time asks for the server address
and API key shown in the NEXUS app. After that:
```
nexus           # start chatting
nexus setup     # change server address / API key
```
Your computer and the phone must be on the same Wi-Fi, with the NEXUS server in **LAN** mode.

## Chat
- Replies stream in with Markdown and coloured code.
- `/` opens the command menu, `@path` adds a file, `!` (on an empty line) switches to shell mode.
- **Esc** stops a reply, **Esc then Enter** or **Ctrl+J** adds a new line, **Ctrl+C** twice quits.
- `/write page.html` saves the largest code block from the last reply.
- Old messages are dropped automatically when the chat outgrows the model's context.

| Command | What it does |
|---|---|
| `/help` | commands and shortcuts |
| `/clear` | new conversation |
| `/model [id]` | list or switch models |
| `/think` | Qwen3 reasoning on/off (`/thoughts` shows it) |
| `/system <text>` | system prompt |
| `/retry` | answer the last question again |
| `/write <file>` · `/copy` · `/save [file]` | keep the output |
| `/status` | server, model, context left |

## One-off use
```
nexus ask "What is the capital of Japan?"
cat notes.txt | nexus ask "Summarize this:"
nexus status
nexus chat --plain     # simple line-by-line chat
```
Settings live in `~/.nexus/config.json`; `--url`/`--key` or `NEXUS_URL`/`NEXUS_API_KEY` override them.

## Update / uninstall
Run the install command again to update. To remove: delete `~/.nexus/cli` and `~/.local/bin/nexus`
(Windows: `%USERPROFILE%\.nexus\cli` and `%USERPROFILE%\.nexus\bin`).

## From source
```
pip install .
```
