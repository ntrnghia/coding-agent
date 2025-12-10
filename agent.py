import anthropic
import json
import os
from datetime import datetime
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)


class CodingAgent:
    """Minimal AI agent for coding workspace"""

    def __init__(self, tools, workspace_dir):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-opus-4-5"
        self.system_message = f"""You are an AI coding assistant with access to a workspace at {workspace_dir}.
You can execute shell commands, search the web, and fetch documentation.

IMPORTANT: When asked to explore or analyze directories OUTSIDE your workspace:
1. Use the docker_sandbox tool to create a safe container environment
2. First call docker_sandbox with action="start" and mount_path="<the external path>"
3. Then use action="exec" with commands like "ls -la /workspace", "cat /workspace/file.py", etc.
4. The external directory is mounted read-only at /workspace inside the container
5. When done, call action="stop" to clean up

This keeps the host system safe while allowing full exploration of external code.

Always verify your actions and explain what you're doing."""

        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}
        self.workspace_dir = workspace_dir
        
        # Setup debug log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.debug_file = os.path.join(workspace_dir, f"debug_{timestamp}.txt")
        self._log_debug(f"Session started at {datetime.now().isoformat()}")
        self._log_debug(f"Workspace: {workspace_dir}")

    def _log_debug(self, message):
        """Write debug message to log file"""
        with open(self.debug_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def _get_tool_description(self, tool_name, tool_input):
        """Convert tool call to human-readable description"""
        if tool_name == "docker_sandbox":
            action = tool_input.get("action", "")
            if action == "start":
                path = tool_input.get("mount_path", "")
                return f"🐳 Start container: {path}"
            elif action == "exec":
                cmd = tool_input.get("command", "")
                if cmd.startswith("ls"):
                    return "📂 List files"
                elif cmd.startswith("cat "):
                    # Extract filename from path
                    parts = cmd.split()
                    if len(parts) > 1:
                        filename = parts[-1].split("/")[-1]
                        return f"📄 Read {filename}"
                    return "📄 Read file"
                elif cmd.startswith("head ") or cmd.startswith("tail "):
                    parts = cmd.split()
                    if len(parts) > 1:
                        filename = parts[-1].split("/")[-1]
                        return f"📄 Read {filename}"
                    return "📄 Read file"
                elif cmd.startswith("find "):
                    return "🔎 Find files"
                elif cmd.startswith("grep "):
                    return "🔎 Search in files"
                else:
                    # Truncate long commands
                    display_cmd = cmd[:50] + "..." if len(cmd) > 50 else cmd
                    return f"⚡ Run: {display_cmd}"
            elif action == "stop":
                return "🐳 Stop container"
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
            # Truncate long URLs
            display_url = url[:50] + "..." if len(url) > 50 else url
            return f"🌐 Fetch: {display_url}"
        
        return f"🔧 {tool_name}"

    def _get_tool_schemas(self):
        return [tool.get_schema() for tool in self.tools]

    def chat(self, message):
        """Send message to Claude"""
        self.messages.append({"role": "user", "content": message})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_message,
            tools=self._get_tool_schemas(),
            messages=self.messages
        )

        self.messages.append({"role": "assistant", "content": response.content})
        return response

    def run(self, user_input, max_turns=100):
        """Main agent loop"""
        current_input = user_input
        self._log_debug(f"\n{'='*60}")
        self._log_debug(f"User input: {user_input}")

        for i in range(max_turns):
            self._log_debug(f"\n--- Turn {i+1} ---")

            response = self.chat(current_input)

            # Print text responses
            for block in response.content:
                if hasattr(block, 'text'):
                    print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {block.text}")
                    self._log_debug(f"Agent: {block.text}")

            # Handle tool use
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        # Log full details to debug file
                        self._log_debug(f"\n[Tool: {tool_name}]")
                        self._log_debug(f"Input: {json.dumps(tool_input, indent=2)}")

                        # Print concise description to console
                        description = self._get_tool_description(tool_name, tool_input)
                        print(f"{Fore.YELLOW}{description}{Style.RESET_ALL}")

                        # Execute tool
                        tool = self.tool_map[tool_name]
                        result = tool.execute(**tool_input)

                        # Log full output to debug file
                        self._log_debug(f"Output: {json.dumps(result, indent=2)}")

                        # Only print errors to console
                        if "error" in result:
                            print(f"{Fore.RED}  ✗ Error: {result['error']}{Style.RESET_ALL}")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })

                current_input = tool_results
            else:
                # Agent finished
                return response

        print(f"{Fore.RED}Max turns reached{Style.RESET_ALL}")
        self._log_debug("Max turns reached")
        return response
