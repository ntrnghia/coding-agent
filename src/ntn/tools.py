import re
import subprocess

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from .config import config


def _split_cd_prefix(cmd: str) -> tuple[str | None, str]:
    """Extract (cwd, remainder) from `cd <cwd> && <remainder>` (or `;`)."""
    cmd = cmd.strip()
    m = re.match(r"^cd\s+([^\n]+?)\s*(?:&&|;)\s*(.+)$", cmd, flags=re.S)
    if not m:
        return None, cmd

    cwd = m.group(1).strip().strip('"').strip("'")
    remainder = m.group(2).strip()
    return cwd, remainder


def _describe_tool_exec(cmd: str) -> str:
    cwd, remainder = _split_cd_prefix(cmd)
    desc = _parse_exec_command(remainder)
    return f"{desc} (In {cwd})" if cwd else desc


def _describe_tool_exec_tuple(cmd: str) -> tuple[str, str | None]:
    """Helper for docker_sandbox exec - returns (description, path)."""
    cwd, remainder = _split_cd_prefix(cmd)
    desc = _parse_exec_command(remainder)
    return (desc, cwd)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "..."


def get_tool_description(tool_name, tool_input):
    """Convert tool call to human-readable description.

    Returns:
        tuple[str, str | None]: (description, path) where path is the working directory or None
    """
    if tool_name == "docker_sandbox":
        action = tool_input.get("action", "")
        if action == "start":
            return (f"🐳 Mount directory: {tool_input.get('mount_path', '')}", None)
        if action == "exec":
            return _describe_tool_exec_tuple(tool_input.get("command", ""))
        if action == "stop":
            path = tool_input.get("mount_path", "")
            desc = f"🐳 Stop container: {path}" if path else "🐳 Stop all containers"
            return (desc, None)
        return (f"🐳 Docker: {action}", None)

    if tool_name == "execute_command":
        cmd = tool_input.get("command", "")
        cwd, remainder = _split_cd_prefix(cmd)

        # Detect inline Python (heredoc or -c style)
        if re.search(r"^python\s+.*<<\s*['\"]?\w+['\"]?", remainder, flags=re.S):
            desc = "🐍 Run inline Python"
        elif re.match(r"^python\s+-c\s+", remainder):
            desc = "🐍 Run inline Python"
        else:
            desc = f"⚡ Run: {_short(remainder, 60)}"

        return (desc, cwd)

    if tool_name == "web_search":
        return (f"🔍 Search: {tool_input.get('query', '')}", None)

    if tool_name == "fetch_webpage":
        return (f"🌐 Fetch: {_short(tool_input.get('url', ''), 50)}", None)

    return (f"🔧 {tool_name}", None)


def _parse_exec_command(cmd: str) -> str:
    """Parse a container exec command into a short human-readable description."""
    parts = [p.strip() for p in cmd.split("|")]
    main_cmd = parts[0]

    if re.search(r"^python(3)?\s+.*<<\s*['\"]?\w+['\"]?", main_cmd, flags=re.S):
        return "🐍 Run inline Python"

    # Check for line limiting in pipe
    line_info = ""
    for part in parts[1:]:
        match = re.match(r"(head|tail)\s+(?:-n\s*)?(\d+)", part)
        if match:
            line_info = f" ({'first' if match.group(1) == 'head' else 'last'} {match.group(2)} lines)"

    # Simple command mappings
    for prefix, desc in (
        ("ls", "📂 List files"),
        ("find ", "🔎 Find files"),
        ("grep ", "🔎 Search in files"),
        ("wc ", "📊 Count lines/words"),
    ):
        if main_cmd.startswith(prefix):
            return desc

    # File read/write commands
    if main_cmd.startswith("cat"):
        if ">>" in main_cmd or main_cmd.startswith("cat >"):
            match = re.search(r"cat\s*>>?\s*([^\s<]+)", main_cmd)
            filename = match.group(1).split("/")[-1] if match else "file"
            return f"✏️  {'Append to' if '>>' in main_cmd else 'Edit'} {filename}"
        if ">" not in main_cmd:
            match = re.search(r"cat\s+([^|]+)", main_cmd)
            if match:
                return f"📄 Read {match.group(1).strip().split('/')[-1]}{line_info}"

    for cmd_name, direction in (("head", "first"), ("tail", "last")):
        if main_cmd.startswith(f"{cmd_name} "):
            match = re.match(rf"{cmd_name}\s+(?:-n\s*)?(-?\d+)?\s*(.+)?", main_cmd)
            if match and match.group(2):
                num, filepath = match.group(1), match.group(2)
                filename = filepath.strip().split("/")[-1]
                suffix = f" ({direction} {num.lstrip('-')} lines)" if num else ""
                return f"📄 Read {filename}{suffix}"
            return "📄 Read file"

    if main_cmd.startswith("sed "):
        match = re.match(r"sed\s+-n\s+['\"]?(\d+),(\d+)p['\"]?\s+(.+)", main_cmd)
        if match:
            start, end, filepath = match.groups()
            filename = filepath.strip().split("/")[-1]
            return f"📄 Read {filename} (lines {start}-{end})"

    return f"⚡ Run: {_short(cmd, 50)}"


class TerminalTool:
    """Execute shell commands in workspace."""

    DANGEROUS_COMMANDS = config.tools.dangerous_commands

    def __init__(self, workspace_dir, confirm_callback=None):
        self.workspace_dir = workspace_dir
        self.confirm_callback = confirm_callback or self._default_confirm

    def _default_confirm(self, command):
        print(f"\n⚠️  DANGEROUS COMMAND DETECTED: {command}")
        response = input("Allow execution? (y/N): ").strip().lower()
        return response == "y"

    def get_schema(self):
        return {
            "name": "execute_command",
            "description": "Execute shell command in the coding workspace. Use for file operations, running scripts, git commands, building projects, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"],
            },
        }

    def execute(self, command):
        cmd_name = command.split()[0].lower() if command.split() else ""
        _ = cmd_name  # keep behavior: previously computed but unused beyond legacy

        all_parts = command.replace("|", " ").replace("&&", " ").replace(";", " ").split()
        dangerous_found = [p for p in all_parts if p.lower() in self.DANGEROUS_COMMANDS]

        if dangerous_found and not self.confirm_callback(command):
            return {
                "error": f"Command rejected by user. Dangerous commands detected: {dangerous_found}"
            }

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                cwd=self.workspace_dir,
                text=True,
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except Exception as e:
            return {"error": str(e)}


class WebSearchTool:
    """Search web using ddgs."""

    def __init__(self):
        self.ddgs = DDGS()

    def get_schema(self):
        return {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. Use for finding documentation, packages, error messages, etc.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        }

    def execute(self, query):
        try:
            results = list(
                self.ddgs.text(
                    query,
                    region=config.tools.web_search.region,
                    max_results=config.tools.web_search.max_results,
                )
            )
            return {"results": results}
        except Exception as e:
            return {"error": str(e)}


class FetchWebTool:
    """Fetch webpage content."""

    def get_schema(self):
        return {
            "name": "fetch_webpage",
            "description": "Fetch and extract text content from a URL. Use to read documentation, README files, or error descriptions.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                "required": ["url"],
            },
        }

    def execute(self, url):
        try:
            response = requests.get(url, timeout=config.tools.web_fetch.timeout)
            soup = BeautifulSoup(response.content, "html.parser")

            for script in soup(["script", "style"]):
                script.decompose()

            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)

            return {"content": text[: config.tools.web_fetch.content_limit]}
        except Exception as e:
            return {"error": str(e)}


class DockerSandboxTool:
    """Execute commands in a Docker sandbox via the agent's ContainerManager."""

    def __init__(self, agent_ref=None, confirm_callback=None):
        self.agent_ref = agent_ref
        self.confirm_callback = confirm_callback or self._default_confirm

    def set_agent_ref(self, agent):
        self.agent_ref = agent

    def _default_confirm(self, message):
        print(f"\n🐳 {message}")
        response = input("Proceed? (y/N): ").strip().lower()
        return response == "y"

    def get_schema(self):
        return {
            "name": "docker_sandbox",
            "description": """Execute commands in a Docker sandbox. Use this for safely exploring external directories or running untrusted code.

A single container persists for the entire session. All directories are mounted in the same container.

Actions:
- 'start': Mount a directory and start container if not running. Path is converted to Unix format (D:\\path → /d/path).
- 'exec': Execute a command inside the container.
- 'stop': Stop the container.

Examples:
- Start: {"action": "start", "mount_path": "D:\\\\Downloads\\\\project"}  → mounted at /d/downloads/project
- Execute: {"action": "exec", "command": "ls -la /d/downloads/project"}
- Execute: {"action": "exec", "command": "cat /d/downloads/project/main.py"}
- Stop: {"action": "stop"}""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "exec", "stop"],
                        "description": "Action to perform: start, exec, or stop",
                    },
                    "mount_path": {
                        "type": "string",
                        "description": "Path to mount into container (required for 'start')",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute in container (required for 'exec' action",
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, action, mount_path=None, command=None, **kwargs):
        if not self.agent_ref:
            return {"error": "DockerSandboxTool not initialized with agent reference"}

        if action == "start":
            return self._start_or_add_mount(mount_path)
        if action == "exec":
            return self._exec_command(command)
        if action == "stop":
            return self._stop_container()
        return {"error": f"Unknown action: {action}"}

    def _start_or_add_mount(self, mount_path):
        if not mount_path:
            return {"error": "mount_path is required for 'start' action"}

        result = self.agent_ref.add_working_directory(mount_path)

        if result.get("status") == "already_mounted":
            return {
                "status": "Already mounted",
                "mount_path": result["mount_path"],
                "container": self.agent_ref.container_manager.container_name,
            }
        if result.get("error"):
            return {"error": result["error"]}
        return {
            "status": result.get("status", "ready"),
            "mount_path": result["mount_path"],
            "container": self.agent_ref.container_manager.container_name,
            "all_mounts": self.agent_ref.container_manager.get_mount_info(),
        }

    def _exec_command(self, command):
        if not command:
            return {"error": "command is required for 'exec' action"}
        return self.agent_ref.container_manager.exec(command)

    def _stop_container(self):
        cm = self.agent_ref.container_manager
        cm.stop()
        return {"status": "stopped", "container": cm.container_name}
