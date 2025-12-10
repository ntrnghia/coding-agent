import subprocess
import json
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup

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
    """Execute commands in a Docker sandbox for safe exploration of external directories"""

    DEFAULT_IMAGE = "python:3.11-slim"

    def __init__(self, confirm_callback=None):
        self.container_id = None
        self.mounted_path = None
        self.confirm_callback = confirm_callback or self._default_confirm

    def _default_confirm(self, message):
        """Default confirmation prompt"""
        print(f"\n🐳 {message}")
        response = input("Proceed? (y/N): ").strip().lower()
        return response == 'y'

    def get_schema(self):
        return {
            "name": "docker_sandbox",
            "description": """Execute commands in a Docker sandbox. Use this for safely exploring external directories or running untrusted code.
            
Actions:
- 'start': Start a new container with a directory mounted at /workspace. Requires 'mount_path'.
- 'exec': Execute a command inside the running container. Requires 'command'.
- 'stop': Stop and remove the running container.

Examples:
- Start: {"action": "start", "mount_path": "D:\\\\Downloads\\\\some-project"}
- Execute: {"action": "exec", "command": "ls -la /workspace"}
- Execute: {"action": "exec", "command": "cat /workspace/main.py"}
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
                        "description": "Path to mount into container (required for 'start' action)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute in container (required for 'exec' action)"
                    },
                    "image": {
                        "type": "string",
                        "description": "Docker image to use (default: python:3.11-slim)"
                    }
                },
                "required": ["action"]
            }
        }

    def execute(self, action, mount_path=None, command=None, image=None):
        """Execute docker sandbox action"""
        image = image or self.DEFAULT_IMAGE

        if action == "start":
            return self._start_container(mount_path, image)
        elif action == "exec":
            return self._exec_command(command)
        elif action == "stop":
            return self._stop_container()
        else:
            return {"error": f"Unknown action: {action}"}

    def _start_container(self, mount_path, image):
        """Start a new Docker container with mounted directory"""
        if not mount_path:
            return {"error": "mount_path is required for 'start' action"}

        # Stop existing container if running
        if self.container_id:
            self._stop_container()

        # Convert Windows path to Docker-compatible format
        docker_path = mount_path.replace('\\', '/')
        if len(docker_path) > 1 and docker_path[1] == ':':
            # Convert D:\path to /d/path for Docker on Windows
            docker_path = '/' + docker_path[0].lower() + docker_path[2:]

        try:
            # Pull image if needed
            subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                text=True
            )

            # Start container with mount
            result = subprocess.run(
                [
                    "docker", "run", "-d", "--rm",
                    "-v", f"{docker_path}:/workspace:ro",
                    "-w", "/workspace",
                    image,
                    "tail", "-f", "/dev/null"  # Keep container running
                ],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return {"error": f"Failed to start container: {result.stderr}"}

            self.container_id = result.stdout.strip()[:12]
            self.mounted_path = mount_path

            return {
                "status": "Container started",
                "container_id": self.container_id,
                "mounted_path": mount_path,
                "workspace": "/workspace",
                "image": image,
                "tip": "Use action='exec' with command to run commands. The mounted directory is read-only at /workspace."
            }

        except FileNotFoundError:
            return {"error": "Docker is not installed or not in PATH"}
        except Exception as e:
            return {"error": str(e)}

    def _exec_command(self, command):
        """Execute command in running container"""
        if not self.container_id:
            return {"error": "No container running. Use action='start' first."}

        if not command:
            return {"error": "command is required for 'exec' action"}

        try:
            result = subprocess.run(
                ["docker", "exec", self.container_id, "sh", "-c", command],
                capture_output=True,
                text=True
            )

            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }

        except Exception as e:
            return {"error": str(e)}

    def _stop_container(self):
        """Stop and remove the running container"""
        if not self.container_id:
            return {"status": "No container running"}

        try:
            subprocess.run(
                ["docker", "stop", self.container_id],
                capture_output=True,
                text=True
            )

            old_id = self.container_id
            self.container_id = None
            self.mounted_path = None

            return {"status": "Container stopped", "container_id": old_id}

        except Exception as e:
            return {"error": str(e)}
