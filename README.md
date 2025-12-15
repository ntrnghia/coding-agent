# NTN - Minimal AI Coding Agent

A minimal AI agent that helps with coding tasks in a workspace. Supports multiple LLM providers (OpenAI GPT-5.2, Anthropic Claude).

## Features

- **Multi-provider support**: Claude Opus (default), GPT-5.2, Sonnet, Haiku
- **Docker-first file operations**: All file operations run in a Docker container with Unix tools
- **Web search**: Search using DuckDuckGo (ddgs package)
- **Web fetching**: Fetch and read webpage content
- **Terminal execution**: Run Windows commands when needed
- **Persistent container**: Single container per session, auto-starts on launch
- **Command denylist**: Dangerous commands require user confirmation
- **Two-color tool display**: Tool descriptions in yellow, paths in cyan for better readability
- **Smart command detection**: Recognizes common patterns (python -c, inline scripts) for better descriptions
- **Colored output**: Easy-to-read console with color-coded messages
- **Debug logging**: Incremental logging to `debug/` folder (crash-resilient)
- **Resume sessions**: Continue previous conversations with `-r` flag
- **Mid-turn resume**: Automatically recovers from crashes mid-tool-execution
- **Auto-compact**: Automatically summarizes context when approaching token limit
- **Auto-cleanup**: Empty conversations (no user messages) are automatically deleted
- **Rate limit handling**: Automatically waits and retries using `retry-after` header
- **Prompt caching**: System prompt and tools are cached to reduce costs
- **Model selection**: Choose between Claude and GPT models with `-m` flag
- **Streaming output**: Real-time response display (always enabled)
- **Cost tracking**: Shows per-request and session costs with token usage
- **Extended thinking**: Deep reasoning enabled by default, disable with `-nt` flag

## Installation

Install from PyPI:
```bash
pip install ntn
```

Or install from source:
```bash
git clone https://github.com/ntrnghia/coding-agent.git
cd ntn
pip install -e .
```

## Setup

Set your API key based on the model you want to use:

**For Claude models (default):**
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

**For GPT-5.2:**
```bash
export OPENAI_API_KEY='your-api-key-here'
```

(Optional) Install Docker for sandbox functionality.

## Usage

Run the agent:
```bash
ntn
```

Resume a previous session:
```bash
# Resume most recent session
ntn -r

# Resume specific session
ntn -r debug/debug_20251210_120000.txt
```

Disable extended thinking (enabled by default):
```bash
ntn -nt
```

Use a different model:
```bash
ntn -m opus    # Use Claude Opus 4.5 (default)
ntn -m sonnet  # Use Claude Sonnet 4.5
ntn -m haiku   # Use Claude Haiku 4.5
ntn -m gpt     # Use GPT-5.2
```

Combine flags:
```bash
ntn -nt -r          # Resume without extended thinking
ntn -m gpt -nt      # GPT without extended thinking
```

Alternative: Run as Python module:
```bash
python -m ntn
```

**Input controls:**
- `Shift+Enter` - New line (shows `\`)
- `Enter` - Submit message
- `Ctrl+C` - Exit the agent

Example prompts:
- "Create a new Python project with main.py and tests/"
- "Search for PyTorch distributed training docs"
- "List all Python files in this directory"
- "Run pytest on my tests"
- "Tell me what the code in D:\Downloads\some-project does" (uses Docker sandbox)

## Package Structure

```
ntn/
├── src/ntn/
│   ├── __init__.py    # Package exports
│   ├── __main__.py    # Entry for `python -m ntn`
│   ├── agent.py       # Main agent with auto-compact and resume support
│   ├── tools.py       # Tool implementations (Terminal, Web, Docker)
│   ├── providers.py   # LLM provider abstraction (OpenAI, Anthropic)
│   ├── config.py      # Configuration loader
│   ├── config.yaml    # Configuration values
│   └── cli.py         # CLI entry point
├── pyproject.toml     # Package configuration
├── LICENSE            # MIT License
└── README.md          # This file
```

## Tools

### Terminal Tool
Executes shell commands in your workspace. Dangerous commands (rm, sudo, curl, etc.) require user confirmation before execution.

### Web Search Tool
Searches the web using DuckDuckGo, returns top 10 results.

### Fetch Web Tool
Fetches and extracts text content from URLs.

### Docker Sandbox Tool
All file operations run in a Docker container for consistent Unix environment:
- **Auto-starts on launch** with workspace pre-mounted
- **Single persistent container** per session (named `agent_<timestamp>`)
- Directories mounted at Unix-style paths: `D:\Downloads\project` → `/d/downloads/project`
- Read-write access to all mounted directories
- Multiple directories can be mounted dynamically
- Container persists across prompts and survives resume
- **Lazy recovery**: If container stops, auto-restarts on next command
- Uses `python:slim` image by default

## Context Management

The agent automatically manages context when approaching token limits:

1. **Auto-compact triggers**: Summarizes older conversation turns
2. **Preserves current task**: Summary includes your current question
3. **Seamless continuation**: You won't notice the compaction

Debug file shows compaction events:
```
=== COMPACTION EVENT ===
Reason: Exceeded context (180000 tokens attempted)
Removed turns: 1-3
Summary content: [condensed conversation]
```

## Resume Sessions

Sessions are logged incrementally to `debug/debug_<timestamp>.txt`. To resume:

```bash
# Resume most recent session
ntn -r

# Resume specific session
ntn -r debug/debug_20251210_120000.txt
```

On resume:
- Previous conversation is displayed (including tool operations)
- Context is restored (including any compacted summaries)
- Container state is restored (mounts preserved)
- New messages append to the same debug file
- **Crash recovery**: If the agent crashed mid-turn, it will automatically continue from where it left off
- **Multi-model support**: Can resume with a different model than originally used

## Debug Log Format

Debug files use an incremental format for crash resilience:
```
=== TURN 1 ===
--- USER ---
<user message>
--- ASSISTANT ---
<JSON response>
--- USAGE: {"model": "gpt", "input": 1000, "output": 50, ...} ---
--- TOOL_RESULT ---
<JSON tool results>
--- END_TURN ---
```

Each block is written immediately, so even if the agent crashes, the debug file contains all completed operations.

## Output Format

The agent uses colored output for readability:
- 🟢 **Green**: Agent messages
- 🟡 **Yellow**: Tool descriptions (📂 List files, 📄 Read file, ✏️ Edit file, 🐳 Docker, etc.)
- 🔵 **Cyan**: Working directory paths `(In /path/to/dir)`, system messages, user prompts
- 🟣 **Magenta**: Thinking indicator (extended thinking enabled by default)
- 🔴 **Red**: Errors

**Tool Display Example:**
```
🐍 Run inline Python (In /d/downloads/coding-agent)
    ^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    Yellow           Cyan
```

Smart command detection automatically shows meaningful descriptions:
- `python -c "..."` → "🐍 Run inline Python"
- Long commands are truncated for readability

Full JSON input/output is logged to `debug/debug_<timestamp>.txt` for debugging.

## Security Notes

- Commands run without timeout (for long-running processes)
- Dangerous commands require explicit user confirmation
- Docker sandbox provides isolated environment for external directories
- All commands run in the specified workspace directory
- Never commit API keys to version control

## Multi-line input (Shift+Enter)

This CLI uses `prompt-toolkit`.

**Important:** Many terminals (including **VS Code integrated terminal** and **Windows Terminal**) do not pass a distinct `Shift+Enter` key event to terminal applications. To make `Shift+Enter` insert a newline reliably, configure your terminal to translate `Shift+Enter` into the sequence **Esc** then **Enter** (`\u001b\r`).

The CLI binds:
- `Enter`  submit
- `Esc` then `Enter`  insert newline

### VS Code (Windows / PowerShell)

Add this to your VS Code `keybindings.json`:

```json
{
  "key": "shift+enter",
  "command": "workbench.action.terminal.sendSequence",
  "args": {
    "text": "\u001b\r"
  },
  "when": "terminalFocus"
}
```

### Windows Terminal (Windows / PowerShell)

In Windows Terminal `settings.json`, add an action that sends `Esc`+`Enter` and bind it to `shift+enter`.

Example (schema varies slightly by Windows Terminal version):

```json
{
  "keys": "shift+enter",
  "command": {
    "action": "sendInput",
    "input": "\u001b\r"
  }
}
```

If you already created a `sendInput` action with an `id`, you can bind `shift+enter` to that action instead.
