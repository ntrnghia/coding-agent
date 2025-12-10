# Coding Agent with Claude Opus 4.5

A minimal AI agent that uses Claude API to help with coding tasks in a workspace.

## Features

- **Terminal execution**: Run shell commands in your workspace
- **Web search**: Search using DuckDuckGo (ddgs package)
- **Web fetching**: Fetch and read webpage content
- **Docker sandbox**: Safely explore external directories in isolated containers
- **Command denylist**: Dangerous commands require user confirmation
- **Colored output**: Easy-to-read console with color-coded messages
- **Debug logging**: Full JSON logs saved to `debug/` folder
- **Resume sessions**: Continue previous conversations with `-r` flag
- **Auto-compact**: Automatically summarizes context when approaching token limit

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set your Anthropic API key:
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

Or create a `.env` file (copy from `.env.example`):
```bash
cp .env.example .env
# Edit .env and add your API key
```

3. (Optional) Install Docker for sandbox functionality.

## Usage

Run the agent:
```bash
python run.py
```

Resume a previous session:
```bash
# Resume most recent session
python run.py -r

# Resume specific session
python run.py -r debug/debug_20251210_120000.txt
```

**Input controls:**
- `Shift+Enter` - New line (shows `\`)
- `Enter` - Submit message
- `exit` - Quit the agent

Example prompts:
- "Create a new Python project with main.py and tests/"
- "Search for PyTorch distributed training docs"
- "List all Python files in this directory"
- "Run pytest on my tests"
- "Tell me what the code in D:\Downloads\some-project does" (uses Docker sandbox)

## Project Structure

```
coding_agent/
├── agent.py           # Main agent with auto-compact and resume support
├── tools.py           # Tool implementations (Terminal, Web, Docker)
├── run.py             # Entry point with CLI arguments
├── requirements.txt   # Dependencies
└── debug/             # Debug logs (gitignored)
```

## Tools

### Terminal Tool
Executes shell commands in your workspace. Dangerous commands (rm, sudo, curl, etc.) require user confirmation before execution.

### Web Search Tool
Searches the web using DuckDuckGo, returns top 10 results.

### Fetch Web Tool
Fetches and extracts text content from URLs.

### Docker Sandbox Tool
Safely explore external directories in isolated Docker containers:
- Mounts directories at `/workspace` with read-write access
- Containers persist across prompts (no restart needed)
- Multiple directories can be mounted simultaneously
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

Sessions are logged to `debug/debug_<timestamp>.txt`. To resume:

```bash
# Resume most recent session
python run.py -r

# Resume specific session  
python run.py -r debug/debug_20251210_120000.txt
```

On resume:
- Previous conversation is displayed
- Context is restored (including any compacted summaries)
- New messages append to the same debug file

## Output Format

The agent uses colored output for readability:
- 🟢 **Green**: Agent messages
- 🟡 **Yellow**: Tool operations (📂 List files, 📄 Read file, 🐳 Docker, etc.)
- 🔵 **Cyan**: User prompts
- 🔴 **Red**: Errors

Full JSON input/output is logged to `debug/debug_<timestamp>.txt` for debugging.

## Security Notes

- Commands run without timeout (for long-running processes)
- Dangerous commands require explicit user confirmation
- Docker sandbox provides isolated environment for external directories
- All commands run in the specified workspace directory
- Never commit API keys to version control
