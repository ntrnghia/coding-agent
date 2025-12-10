import subprocess
import json
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup

class TerminalTool:
    """Execute shell commands in workspace"""

    # Command allowlist for coding
    ALLOWED_COMMANDS = {
        'ls', 'cat', 'head', 'tail', 'wc', 'find', 'grep', 'less', 
        'mkdir', 'touch', 'cp', 'mv', 'rm', 'pwd', 'echo', 'tee',
        'git', 'python', 'python3', 'pip', 'pip3', 'pytest', 'black',
        'npm', 'yarn', 'node', 'make', 'gcc', 'g++', 'diff', 'tree',
        'ps', 'kill', 'which', 'chmod', 'sed', 'awk', 'cargo', 'rustc',
        'ruff', 'mypy', 'pylint'
    }

    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir

    def get_schema(self):
        return {
            "name": "execute_command",
            "description": "Execute shell command in the coding workspace. Use for file operations, running scripts, git commands, building projects, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                    }
                },
                "required": ["command"]
            }
        }

    def execute(self, command):
        """Execute command with no timeout"""
        # Check if command is allowed
        cmd_name = command.split()[0]
        if cmd_name not in self.ALLOWED_COMMANDS:
            return {"error": f"Command '{cmd_name}' not in allowlist"}

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                cwd=self.workspace_dir,
                text=True
                # No timeout parameter
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"error": str(e)}


class WebSearchTool:
    """Search web using ddgs"""

    def __init__(self):
        self.ddgs = DDGS()

    def get_schema(self):
        return {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. Use for finding documentation, packages, error messages, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    }
                },
                "required": ["query"]
            }
        }

    def execute(self, query):
        """Search with fixed max_results=10"""
        try:
            results = list(self.ddgs.text(
                query,
                region='wt-wt',
                max_results=10
            ))
            return {"results": results}
        except Exception as e:
            return {"error": str(e)}


class FetchWebTool:
    """Fetch webpage content"""

    def get_schema(self):
        return {
            "name": "fetch_webpage",
            "description": "Fetch and extract text content from a URL. Use to read documentation, README files, or error descriptions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch"
                    }
                },
                "required": ["url"]
            }
        }

    def execute(self, url):
        """Fetch webpage content"""
        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()

            text = soup.get_text()
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)

            # Limit to first 5000 chars
            return {"content": text[:5000]}
        except Exception as e:
            return {"error": str(e)}
