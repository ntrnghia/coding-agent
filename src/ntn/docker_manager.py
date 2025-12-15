"""Docker container management for the agent.

Extracted from agent.py to keep CodingAgent focused on orchestration.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import config


@dataclass(frozen=True)
class DockerResult:
    stdout: str
    stderr: str
    returncode: int


class ContainerManager:
    """Manages a persistent Docker container for the agent session."""

    DEFAULT_IMAGE = config.docker.default_image

    def __init__(self, container_name: str, working_dirs: list[str] | None = None):
        self.container_name = container_name
        self.working_dirs = working_dirs or []
        self.image = self.DEFAULT_IMAGE

    @staticmethod
    def convert_path(win_path: str, lowercase: bool = False) -> str:
        """Convert Windows path to Unix-style path.

        Example:
            D:\\Downloads\\coding_agent -> /d/downloads/coding_agent

        Args:
            win_path: Windows path to convert
            lowercase: If True, lowercase entire path (for mount targets)
        """
        path = os.path.normpath(win_path).replace('\\', '/')
        if len(path) > 1 and path[1] == ':':
            path = '/' + path[0].lower() + path[2:]
        return path.lower() if lowercase else path

    def is_path_covered(self, new_path: str) -> bool:
        """Check if a path is already covered by an existing mount."""
        new_norm = os.path.normpath(new_path).lower()
        for existing in self.working_dirs:
            existing_norm = os.path.normpath(existing).lower()
            if new_norm.startswith(existing_norm + os.sep) or new_norm == existing_norm:
                return True
        return False

    def add_working_dir(self, path: str) -> bool:
        """Add a working directory.

        Returns:
            True if added (requires restart), False if already covered.
        """
        norm_path = os.path.normpath(path)
        if self.is_path_covered(norm_path):
            return False
        self.working_dirs.append(norm_path)
        return True

    def _build_mount_args(self) -> list[str]:
        """Build Docker -v mount arguments for all working directories."""
        args: list[str] = []
        for path in self.working_dirs:
            docker_path = self.convert_path(path)
            mount_path = self.convert_path(path, lowercase=True)
            args.extend(["-v", f"{docker_path}:{mount_path}"])
        return args

    def get_mount_info(self) -> str:
        """Get mount information for system prompt."""
        return "\n".join(
            f"  - {path} → {self.convert_path(path, lowercase=True)}" for path in self.working_dirs
        )

    def _docker(self, *args: str, text: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
        )

    def container_exists(self) -> bool:
        """Check if container exists (running or stopped)."""
        try:
            result = self._docker("inspect", self.container_name)
            return result.returncode == 0
        except Exception:
            return False

    def container_running(self) -> bool:
        """Check if container is currently running."""
        try:
            result = self._docker("inspect", "-f", "{{.State.Running}}", self.container_name)
            return result.returncode == 0 and result.stdout.strip() == "true"
        except Exception:
            return False

    def start(self) -> dict[str, Any]:
        """Start the container (create if doesn't exist, start if stopped)."""
        if self.container_running():
            return {"status": "already_running", "container": self.container_name}

        if self.container_exists():
            result = self._docker("start", self.container_name)
            if result.returncode == 0:
                return {"status": "started", "container": self.container_name}
            # Container might be corrupted, remove and recreate
            self._docker("rm", "-f", self.container_name)

        return self._create_container()

    def _create_container(self) -> dict[str, Any]:
        """Create a new container with all mounts."""
        if not self.working_dirs:
            return {"error": "No working directories to mount"}

        try:
            self._docker("pull", self.image)

            cmd = [
                "run",
                "-d",
                "--name",
                self.container_name,
                *self._build_mount_args(),
                self.image,
                "tail",
                "-f",
                "/dev/null",
            ]
            result = self._docker(*cmd)
            if result.returncode != 0:
                return {"error": f"Failed to create container: {result.stderr}"}
            return {"status": "created", "container": self.container_name}

        except FileNotFoundError:
            return {"error": "Docker is not installed or not in PATH"}
        except Exception as e:
            return {"error": str(e)}

    def restart_with_new_mounts(self) -> dict[str, Any]:
        """Stop, remove, and recreate container with updated mounts."""
        self._docker("stop", self.container_name)
        self._docker("rm", self.container_name)
        return self._create_container()

    def exec(self, command: str) -> dict[str, Any]:
        """Execute command in the container. Auto-recovers if container not running."""
        try:
            result = self._docker("exec", self.container_name, "sh", "-c", command)

            if result.returncode != 0 and "No such container" in (result.stderr or ""):
                if self.working_dirs:
                    start_result = self.start()
                    if start_result.get("error"):
                        return {
                            "error": f"Container not running and failed to start: {start_result['error']}"
                        }
                    result = self._docker("exec", self.container_name, "sh", "-c", command)
                else:
                    return {
                        "error": "Container not running. Use action='start' to mount a directory first."
                    }

            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except Exception as e:
            return {"error": str(e)}

    def stop(self) -> dict[str, Any]:
        """Stop the container (don't remove it)."""
        if not self.container_exists():
            return {"status": "not_found"}

        result = self._docker("stop", self.container_name)
        return {"status": "stopped" if result.returncode == 0 else "error"}

    def remove(self) -> dict[str, Any]:
        """Remove the container completely."""
        self._docker("rm", "-f", self.container_name)
        return {"status": "removed"}
