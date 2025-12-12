"""Custom hatch build hook for git-based versioning."""
import subprocess
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Base: last commit before 0.3.x (so first commit after = 0.3.0)
BASE_COMMIT = "6495f31"
BASE_MINOR = 3
BASE_OFFSET = -1  # First commit after base is 0.3.0

def get_version():
    """Calculate version from git commit count since base.

    Formula: 0.{BASE_MINOR}.{count + offset}
    - 1 commit after base → 0.3.0
    - 2 commits → 0.3.1
    - 12 commits → 0.3.11
    """
    try:
        count = int(subprocess.check_output(
            ["git", "rev-list", "--count", f"{BASE_COMMIT}..HEAD"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()) + BASE_OFFSET
        return f"0.{BASE_MINOR}.{count}"
    except Exception:
        return "0.3.0"

class CustomBuildHook(BuildHookInterface):
    """Build hook that generates _version.py."""
    
    def initialize(self, version, build_data):
        version = get_version()
        version_file = Path(self.root) / "src" / "ntn" / "_version.py"
        version_file.write_text(f'__version__ = "{version}"\n')
        build_data["force_include"][str(version_file)] = "ntn/_version.py"
