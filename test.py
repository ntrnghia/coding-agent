#!/usr/bin/env python3
"""Simple test to verify the agent code is working"""

print("Testing imports...")

try:
    from tools import TerminalTool, WebSearchTool, FetchWebTool
    print("✓ Tools imported successfully")
except ImportError as e:
    print(f"✗ Failed to import tools: {e}")
    exit(1)

try:
    from agent import CodingAgent
    print("✓ Agent imported successfully")
except ImportError as e:
    print(f"✗ Failed to import agent: {e}")
    exit(1)

print("\nTesting tool schemas...")

# Test tool schema generation
terminal = TerminalTool("/tmp")
schema = terminal.get_schema()
assert schema["name"] == "execute_command"
print("✓ TerminalTool schema valid")

web_search = WebSearchTool()
schema = web_search.get_schema()
assert schema["name"] == "web_search"
print("✓ WebSearchTool schema valid")

fetch_web = FetchWebTool()
schema = fetch_web.get_schema()
assert schema["name"] == "fetch_webpage"
print("✓ FetchWebTool schema valid")

print("\nTesting command allowlist...")
# Test allowlist enforcement
result = terminal.execute("ls")
assert "error" not in result or "allowlist" not in result["error"]
print("✓ Allowed command works")

result = terminal.execute("malicious_command")
assert "error" in result and "allowlist" in result["error"]
print("✓ Disallowed command blocked")

print("\n" + "="*50)
print("All basic tests passed! ✓")
print("="*50)
print("\nTo run the agent:")
print("1. Set ANTHROPIC_API_KEY environment variable")
print("2. Run: python run.py")
