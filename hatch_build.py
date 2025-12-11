"""Custom hatch build hook for git-based versioning."""
import os
import subprocess
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Base commit: "Convert to pip package" - this is version 0.1.0
BASE_COMMIT = "5d77a0a"

def get_version():
    """Calculate version from git commit count since base.
    
    Formula: 0.{1 + commits//10}.{commits%10}
    - 0 commits → 0.1.0
    - 1 commit  → 0.1.1
    - 10 commits → 0.2.0
    - 15 commits → 0.2.5
    """
    try:
        count = int(subprocess.check_output(
            ["git", "rev-list", "--count", f"{BASE_COMMIT}..HEAD"],
            stderr=subprocess.DEVNULL, text=True
        ).strip())
        return f"0.{1 + count // 10}.{count % 10}"
    except Exception:
        return "0.1.0"

class CustomBuildHook(BuildHookInterface):
    """Build hook that generates _version.py."""
    
    def initialize(self, version, build_data):
        version = get_version()
        version_file = Path(self.root) / "src" / "ntn" / "_version.py"
        version_file.write_text(f'__version__ = "{version}"\n')
        build_data["force_include"][str(version_file)] = "ntn/_version.py"
