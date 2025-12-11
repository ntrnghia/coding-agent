# NTN - Coding Agent with Claude Opus 4.5

A minimal AI agent that uses Claude API to help with coding tasks in a workspace.

## Features

- **Docker-first file operations**: All file operations run in a Docker container with Unix tools
- **Web search**: Search using DuckDuckGo (ddgs package)
- **Web fetching**: Fetch and read webpage content
- **Terminal execution**: Run Windows commands when needed
- **Persistent container**: Single container per session, auto-starts on launch
- **Command denylist**: Dangerous commands require user confirmation
- **Colored output**: Easy-to-read console with color-coded messages
- **Debug logging**: Incremental logging to `debug/` folder (crash-resilient)
- **Resume sessions**: Continue previous conversations with `-r` flag
- **Mid-turn resume**: Automatically recovers from crashes mid-tool-execution
- **Auto-compact**: Automatically summarizes context when approaching token limit
- **Auto-cleanup**: Empty conversations (no user messages) are automatically deleted
- **Rate limit handling**: Automatically waits and retries using `retry-after` header
- **Prompt caching**: System prompt and tools are cached to reduce rate limit usage
- **Streaming output**: Real-time response display (always enabled)
- **Cost tracking**: Shows per-request and session costs with token usage
- **Extended thinking**: Enable deep reasoning for complex tasks with `-t` flag

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

Set your Anthropic API key:
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

Or create a `.env` file:
```bash
echo "ANTHROPIC_API_KEY=your-api-key-here" > .env
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

Enable extended thinking (better for complex reasoning):
```bash
ntn -t
```

Combine flags:
```bash
ntn -t -r  # Resume with extended thinking
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

The agent uses Claude Opus 4.5 with a 200K token context window. When the context approaches the limit:

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

## Debug Log Format

Debug files use an incremental format for crash resilience:
```
=== TURN 1 ===
--- USER ---
<user message>
--- ASSISTANT ---
<JSON response>
--- TOOL_RESULT ---
<JSON tool results>
--- END_TURN ---
```

Each block is written immediately, so even if the agent crashes, the debug file contains all completed operations.

## Output Format

The agent uses colored output for readability:
- 🟢 **Green**: Agent messages
- 🟡 **Yellow**: Tool operations (📂 List files, 📄 Read file, ✏️ Edit file, 🐳 Docker, etc.)
- 🔵 **Cyan**: User prompts
- 🔴 **Red**: Errors

Full JSON input/output is logged to `debug/debug_<timestamp>.txt` for debugging.

## Security Notes

- Commands run without timeout (for long-running processes)
- Dangerous commands require explicit user confirmation
- Docker sandbox provides isolated environment for external directories
- All commands run in the specified workspace directory
- Never commit API keys to version control
