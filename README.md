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

Example commands:
- "Create a new Python project with main.py and tests/"
- "Search for PyTorch distributed training docs"
- "List all Python files in this directory"
- "Run pytest on my tests"
- "Tell me what the code in D:\Downloads\some-project does" (uses Docker sandbox)

Type `exit` to quit.

## Project Structure

```
coding_agent/
├── agent.py           # Main agent loop with colored output
├── tools.py           # Tool implementations (Terminal, Web, Docker)
├── run.py             # Entry point
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
- Mounts directories as read-only at `/workspace`
- Containers persist across prompts (no restart needed)
- Multiple directories can be mounted simultaneously
- Uses `python:3.11-slim` image by default

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
- Docker sandbox mounts external directories as read-only
- All commands run in the specified workspace directory
- Never commit API keys to version control
