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

print("\nTesting command denylist...")
# Test denylist - safe commands should work
result = terminal.execute("echo hello")
assert "error" not in result or "rejected" not in result.get("error", "").lower()
print("✓ Safe command works")

# Test that dangerous commands trigger confirmation (we're not testing the actual block here
# because that requires user interaction). Just verify the tool is initialized.
assert hasattr(terminal, 'DANGEROUS_COMMANDS')
assert 'rm' in terminal.DANGEROUS_COMMANDS
print("✓ Denylist configured correctly")

print("\n" + "="*50)
print("All basic tests passed! ✓")
print("="*50)
print("\nTo run the agent:")
print("1. Set ANTHROPIC_API_KEY environment variable")
print("2. Run: python run.py")
