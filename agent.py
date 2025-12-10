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

IMPORTANT: When asked to work with directories OUTSIDE your workspace:
1. Use the docker_sandbox tool to create a container environment
2. First call docker_sandbox with action="start" and mount_path="<the external path>"
3. Then use action="exec" with commands like "ls -la /workspace", "cat /workspace/file.py", etc.
4. The external directory is mounted at /workspace inside the container with READ-WRITE access
5. To create files, use heredoc syntax: cat > /workspace/file.py << 'EOF'\n...content...\nEOF

Container behavior:
- Containers PERSIST across prompts - do NOT stop unless explicitly asked or completely done
- Multiple directories can be mounted in separate containers simultaneously
- If a container is already running for a path, it will be reused automatically
- Only call action="stop" when you are completely finished with that directory

This provides a consistent Linux environment for all file operations.

Always verify your actions and explain what you're doing."""

        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}
        self.workspace_dir = workspace_dir
        
        # Setup debug log file in debug folder
        debug_dir = os.path.join(workspace_dir, "debug")
        os.makedirs(debug_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.debug_file = os.path.join(debug_dir, f"debug_{timestamp}.txt")
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
                return self._parse_exec_command(cmd)
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
            # Truncate long URLs
            display_url = url[:50] + "..." if len(url) > 50 else url
            return f"🌐 Fetch: {display_url}"
        
        return f"🔧 {tool_name}"

    def _parse_exec_command(self, cmd):
        """Parse exec command into human-readable description"""
        import re
        
        # Handle piped commands: split by | and analyze
        parts = [p.strip() for p in cmd.split('|')]
        
        # Get the main command (first part)
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
        
        elif main_cmd.startswith("cat "):
            # Extract filename: cat /workspace/path/to/file.py
            match = re.search(r'cat\s+([^|]+)', main_cmd)
            if match:
                filepath = match.group(1).strip()
                filename = filepath.split('/')[-1]
                return f"📄 Read {filename}{line_info}"
            return f"📄 Read file{line_info}"
        
        elif main_cmd.startswith("head "):
            # head -n 100 /path/file or head -100 /path/file
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
            # Truncate long commands
            display_cmd = cmd[:50] + "..." if len(cmd) > 50 else cmd
            return f"⚡ Run: {display_cmd}"

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
