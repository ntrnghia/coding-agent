"""Custom hatch build hook for git-based versioning."""
import subprocess
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Base: parent of first 0.2.x commit (so 0.2.0 = 1 commit after this)
BASE_COMMIT = "22edcfe"
BASE_MINOR = 2
BASE_OFFSET = -1  # Subtract 1 so first commit is 0.2.0

def get_version():
    """Calculate version from git commit count since base.
    
    Formula: 0.{BASE_MINOR + (count+offset)//10}.{(count+offset)%10}
    - 1 commit after base → 0.2.0
    - 2 commits → 0.2.1
    - 11 commits → 0.3.0
    """
    try:
        count = int(subprocess.check_output(
            ["git", "rev-list", "--count", f"{BASE_COMMIT}..HEAD"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()) + BASE_OFFSET
        return f"0.{BASE_MINOR + count // 10}.{count % 10}"
    except Exception:
        return "0.2.0"

class CustomBuildHook(BuildHookInterface):
    """Build hook that generates _version.py."""
    
    def initialize(self, version, build_data):
        version = get_version()
        version_file = Path(self.root) / "src" / "ntn" / "_version.py"
        version_file.write_text(f'__version__ = "{version}"\n')
        build_data["force_include"][str(version_file)] = "ntn/_version.py"
