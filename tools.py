import subprocess
import os
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

    DEFAULT_IMAGE = "python:slim"

    def __init__(self, confirm_callback=None):
        self.containers = {}  # {mount_path: container_id}
        self.active_path = None  # Track last-used container
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

Containers persist across prompts - only stop when completely done with a directory.
Multiple directories can be mounted in separate containers simultaneously.
            
Actions:
- 'start': Start a container with a directory mounted at /workspace. If already running for that path, reuses it.
- 'exec': Execute a command inside the active container.
- 'stop': Stop container(s). Optionally specify mount_path to stop specific one, otherwise stops all.

Examples:
- Start: {"action": "start", "mount_path": "D:\\\\Downloads\\\\some-project"}
- Execute: {"action": "exec", "command": "ls -la /workspace"}
- Execute: {"action": "exec", "command": "cat /workspace/main.py"}
- Stop specific: {"action": "stop", "mount_path": "D:\\\\Downloads\\\\some-project"}
- Stop all: {"action": "stop"}""",
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
                        "description": "Path to mount into container (required for 'start', optional for 'stop' to stop specific container)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute in container (required for 'exec' action)"
                    },
                    "image": {
                        "type": "string",
                        "description": "Docker image to use (default: python:slim)"
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
            return self._stop_container(mount_path)
        else:
            return {"error": f"Unknown action: {action}"}

    def _start_container(self, mount_path, image):
        """Start a new Docker container with mounted directory"""
        if not mount_path:
            return {"error": "mount_path is required for 'start' action"}

        # Normalize path for comparison
        norm_path = os.path.normpath(mount_path)
        
        # Check if container already exists for this path
        if norm_path in self.containers:
            self.active_path = norm_path
            return {
                "status": "Container already running",
                "container_id": self.containers[norm_path],
                "mounted_path": mount_path,
                "workspace": "/workspace",
                "image": image
            }

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
                    "-v", f"{docker_path}:/workspace",
                    "-w", "/workspace",
                    image,
                    "tail", "-f", "/dev/null"  # Keep container running
                ],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return {"error": f"Failed to start container: {result.stderr}"}

            container_id = result.stdout.strip()[:12]
            self.containers[norm_path] = container_id
            self.active_path = norm_path

            return {
                "status": "Container started",
                "container_id": container_id,
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
        """Execute command in active container"""
        if not self.active_path or self.active_path not in self.containers:
            return {"error": "No container running. Use action='start' first."}

        if not command:
            return {"error": "command is required for 'exec' action"}

        container_id = self.containers[self.active_path]

        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "sh", "-c", command],
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

    def _stop_container(self, mount_path=None):
        """Stop container(s). If mount_path specified, stop only that one. Otherwise stop all."""
        if mount_path:
            # Stop specific container
            norm_path = os.path.normpath(mount_path)
            if norm_path not in self.containers:
                return {"status": "No container running for that path"}

            container_id = self.containers[norm_path]
            try:
                subprocess.run(
                    ["docker", "stop", container_id],
                    capture_output=True,
                    text=True
                )
                del self.containers[norm_path]
                if self.active_path == norm_path:
                    self.active_path = next(iter(self.containers), None)
                return {"status": "Container stopped", "container_id": container_id, "path": mount_path}
            except Exception as e:
                return {"error": str(e)}
        else:
            # Stop all containers
            if not self.containers:
                return {"status": "No containers running"}

            stopped = []
            for path, container_id in list(self.containers.items()):
                try:
                    subprocess.run(
                        ["docker", "stop", container_id],
                        capture_output=True,
                        text=True
                    )
                    stopped.append({"container_id": container_id, "path": path})
                except Exception:
                    pass
            
            self.containers.clear()
            self.active_path = None
            return {"status": "All containers stopped", "stopped": stopped}
