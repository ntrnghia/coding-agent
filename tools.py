import subprocess
import os
import re
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup


def get_tool_description(tool_name, tool_input):
    """Convert tool call to human-readable description for display"""
    if tool_name == "docker_sandbox":
        action = tool_input.get("action", "")
        if action == "start":
            path = tool_input.get("mount_path", "")
            return f"🐳 Mount directory: {path}"
        elif action == "exec":
            cmd = tool_input.get("command", "")
            return _parse_exec_command(cmd)
        elif action == "stop":
            path = tool_input.get("mount_path", "")
            if path:
                return f"🐳 Stop container: {path}"
            return "🐳 Stop all containers"
        return f"🐳 Docker: {action}"
    
    elif tool_name == "execute_command":
        cmd = tool_input.get("command", "")
        display_cmd = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return f"⚡ Run: {display_cmd}"
    
    elif tool_name == "web_search":
        query = tool_input.get("query", "")
        return f"🔍 Search: {query}"
    
    elif tool_name == "fetch_webpage":
        url = tool_input.get("url", "")
        display_url = url[:50] + "..." if len(url) > 50 else url
        return f"🌐 Fetch: {display_url}"
    
    return f"🔧 {tool_name}"


def _parse_exec_command(cmd):
    """Parse exec command into human-readable description"""
    # Handle piped commands: split by | and analyze
    parts = [p.strip() for p in cmd.split('|')]
    main_cmd = parts[0]
    
    # Check for line limiting in pipe (head/tail)
    line_info = ""
    for part in parts[1:]:
        head_match = re.match(r'head\s+(?:-n\s*)?(\d+)', part)
        tail_match = re.match(r'tail\s+(?:-n\s*)?(\d+)', part)
        if head_match:
            line_info = f" (first {head_match.group(1)} lines)"
        elif tail_match:
            line_info = f" (last {tail_match.group(1)} lines)"
    
    # Parse main command
    if main_cmd.startswith("ls"):
        return "📂 List files"
    
    # Write/Edit file: cat > file.py or cat >> file.py
    elif main_cmd.startswith("cat >") or main_cmd.startswith("cat>>"):
        # Extract filename between > and << (or end)
        match = re.search(r'cat\s*>>?\s*([^\s<]+)', main_cmd)
        if match:
            filepath = match.group(1).strip()
            filename = filepath.split('/')[-1]
            if ">>" in main_cmd:
                return f"✏️ Append to {filename}"
            return f"✏️ Edit {filename}"
        return "✏️ Edit file"
    
    # Read file: cat file.py (no > redirect)
    elif main_cmd.startswith("cat ") and ">" not in main_cmd:
        match = re.search(r'cat\s+([^|]+)', main_cmd)
        if match:
            filepath = match.group(1).strip()
            filename = filepath.split('/')[-1]
            return f"📄 Read {filename}{line_info}"
        return f"📄 Read file{line_info}"
    
    elif main_cmd.startswith("head "):
        match = re.match(r'head\s+(?:-n\s*)?(-?\d+)?\s*(.+)?', main_cmd)
        if match:
            num = match.group(1)
            filepath = match.group(2)
            if filepath:
                filename = filepath.strip().split('/')[-1]
                if num:
                    return f"📄 Read {filename} (first {num.lstrip('-')} lines)"
                return f"📄 Read {filename}"
        return "📄 Read file"
    
    elif main_cmd.startswith("tail "):
        match = re.match(r'tail\s+(?:-n\s*)?(-?\d+)?\s*(.+)?', main_cmd)
        if match:
            num = match.group(1)
            filepath = match.group(2)
            if filepath:
                filename = filepath.strip().split('/')[-1]
                if num:
                    return f"📄 Read {filename} (last {num.lstrip('-')} lines)"
                return f"📄 Read {filename}"
        return "📄 Read file"
    
    elif main_cmd.startswith("find "):
        return "🔎 Find files"
    
    elif main_cmd.startswith("grep "):
        return "🔎 Search in files"
    
    elif main_cmd.startswith("wc "):
        return "📊 Count lines/words"
    
    else:
        display_cmd = cmd[:50] + "..." if len(cmd) > 50 else cmd
        return f"⚡ Run: {display_cmd}"


class TerminalTool:
    """Execute shell commands in workspace"""

    # Dangerous commands that require user confirmation
    DANGEROUS_COMMANDS = {
        # System destructive
        'rm', 'rmdir', 'del', 'format', 'fdisk', 'mkfs',
        # System modification
        'shutdown', 'reboot', 'halt', 'poweroff', 'init',
        # Permission/ownership
        'chmod', 'chown', 'chgrp', 'sudo', 'su', 'runas',
        # Network dangerous
        'curl', 'wget', 'nc', 'netcat', 'ssh', 'scp', 'ftp', 'telnet',
        # Process control
        'kill', 'killall', 'pkill',
        # Disk operations
        'dd', 'mount', 'umount',
        # Registry/system config (Windows)
        'reg', 'regedit', 'bcdedit',
        # Package managers that install system-wide
        'apt', 'apt-get', 'yum', 'dnf', 'pacman', 'brew', 'choco',
        # Dangerous shell operations
        'eval', 'exec', 'source',
    }

    def __init__(self, workspace_dir, confirm_callback=None):
        self.workspace_dir = workspace_dir
        self.confirm_callback = confirm_callback or self._default_confirm

    def _default_confirm(self, command):
        """Default confirmation prompt"""
        print(f"\n⚠️  DANGEROUS COMMAND DETECTED: {command}")
        response = input("Allow execution? (y/N): ").strip().lower()
        return response == 'y'

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
        # Check if command is dangerous and needs confirmation
        cmd_name = command.split()[0].lower()
        
        # Check for dangerous commands (including piped commands)
        all_parts = command.replace('|', ' ').replace('&&', ' ').replace(';', ' ').split()
        dangerous_found = [p for p in all_parts if p.lower() in self.DANGEROUS_COMMANDS]
        
        if dangerous_found:
            if not self.confirm_callback(command):
                return {"error": f"Command rejected by user. Dangerous commands detected: {dangerous_found}"}

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


class DockerSandboxTool:
    """Execute commands in a Docker sandbox for safe exploration of external directories.
    
    Uses the agent's ContainerManager for persistent container with multiple mounts.
    """

    def __init__(self, agent_ref=None, confirm_callback=None):
        self.agent_ref = agent_ref  # Reference to CodingAgent for container management
        self.confirm_callback = confirm_callback or self._default_confirm

    def set_agent_ref(self, agent):
        """Set agent reference after initialization"""
        self.agent_ref = agent

    def _default_confirm(self, message):
        """Default confirmation prompt"""
        print(f"\n🐳 {message}")
        response = input("Proceed? (y/N): ").strip().lower()
        return response == 'y'

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
                        "description": "Action to perform: start, exec, or stop"
                    },
                    "mount_path": {
                        "type": "string",
                        "description": "Path to mount into container (required for 'start')"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute in container (required for 'exec' action)"
                    }
                },
                "required": ["action"]
            }
        }

    def execute(self, action, mount_path=None, command=None, **kwargs):
        """Execute docker sandbox action"""
        if not self.agent_ref:
            return {"error": "DockerSandboxTool not initialized with agent reference"}

        if action == "start":
            return self._start_or_add_mount(mount_path)
        elif action == "exec":
            return self._exec_command(command)
        elif action == "stop":
            return self._stop_container()
        else:
            return {"error": f"Unknown action: {action}"}

    def _start_or_add_mount(self, mount_path):
        """Start container or add new mount"""
        if not mount_path:
            return {"error": "mount_path is required for 'start' action"}

        # Use agent's add_working_directory method
        result = self.agent_ref.add_working_directory(mount_path)
        
        if result.get("status") == "already_mounted":
            return {
                "status": "Already mounted",
                "mount_path": result["mount_path"],
                "container": self.agent_ref.container_manager.container_name
            }
        elif result.get("error"):
            return {"error": result["error"]}
        else:
            return {
                "status": result.get("status", "ready"),
                "mount_path": result["mount_path"],
                "container": self.agent_ref.container_manager.container_name,
                "all_mounts": self.agent_ref.container_manager.get_mount_info()
            }

    def _exec_command(self, command):
        """Execute command in container"""
        if not command:
            return {"error": "command is required for 'exec' action"}

        cm = self.agent_ref.container_manager
        return cm.exec(command)

    def _stop_container(self):
        """Stop the container"""
        cm = self.agent_ref.container_manager
        result = cm.stop()
        return {"status": "stopped", "container": cm.container_name}
