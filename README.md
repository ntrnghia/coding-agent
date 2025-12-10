# Coding Agent with Claude Opus 4.5

A minimal AI agent that uses Claude API to help with coding tasks in a workspace.

## Features

- **Terminal execution**: Run shell commands in your workspace
- **Web search**: Search using DuckDuckGo (ddgs package)
- **Web fetching**: Fetch and read webpage content
- **Command allowlist**: Only allows safe coding-related commands

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

Type `exit` to quit.

## Project Structure

```
coding_agent/
├── agent.py           # Main agent loop
├── tools.py           # Tool implementations
├── run.py            # Entry point
└── requirements.txt  # Dependencies
```

## Allowed Commands

The terminal tool uses an allowlist including:
- File operations: ls, cat, mkdir, touch, cp, mv, rm, etc.
- Version control: git commands
- Python: python, pip, pytest, black, ruff, mypy
- Build tools: make, npm, cargo, gcc, etc.

## Security Notes

- Commands run without timeout (for long-running processes)
- Only allowlisted commands can execute
- All commands run in the specified workspace directory
- Never commit API keys to version control
