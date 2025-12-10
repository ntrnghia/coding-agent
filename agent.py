import anthropic
import json
import os
import re
from datetime import datetime
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)


class CodingAgent:
    """Minimal AI agent for coding workspace"""
    
    # Context limits for claude-opus-4-5-20251101
    MAX_CONTEXT_TOKENS = 200000
    MAX_OUTPUT_TOKENS = 8192  # Conservative output limit
    
    # Summarization request template
    SUMMARIZATION_TEMPLATE = """Summarize the conversation above into a concise context, then answer the current question.

Preserve in summary:
- Key decisions made and their rationale
- Important file changes and current state
- Any relevant context for the current question

Remove from summary:
- Verbose tool outputs (just note what was done)
- Failed attempts (unless informative)
- Exploratory discussions not relevant to current question

Current question to answer:
{current_question}

Provide your response in this format:
[SUMMARY]
(Your concise summary of the conversation)
[/SUMMARY]

[ANSWER]
(Your answer to the current question)
[/ANSWER]"""

    def __init__(self, tools, workspace_dir, debug_file=None):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-opus-4-5-20251101"
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
        self.display_history = []  # Store original user inputs for resume display
        
        # Setup debug log file
        if debug_file:
            # Resume: use existing debug file
            self.debug_file = debug_file
            self._log_resume()
        else:
            # New session: create new debug file
            debug_dir = os.path.join(workspace_dir, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.debug_file = os.path.join(debug_dir, f"debug_{timestamp}.txt")
            self._log_session_start()

    def _log_session_start(self):
        """Log session start with metadata"""
        self._log_raw(f"=== SESSION START ===")
        self._log_raw(f"Timestamp: {datetime.now().isoformat()}")
        self._log_raw(f"Workspace: {self.workspace_dir}")
        self._log_raw(f"Model: {self.model}")
        self._log_raw("")

    def _log_resume(self):
        """Log resume marker"""
        self._log_raw(f"\n=== RESUME ===")
        self._log_raw(f"Timestamp: {datetime.now().isoformat()}")
        self._log_raw("")

    def _log_raw(self, message):
        """Write raw message to log file"""
        with open(self.debug_file, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def _log_turn(self, turn_num, user_input, api_messages, api_response, tool_descriptions=None):
        """Log a complete turn with structured format"""
        self._log_raw(f"\n=== TURN {turn_num} ===")
        self._log_raw(f"--- USER INPUT ---")
        self._log_raw(user_input)
        self._log_raw(f"\n--- API MESSAGES ---")
        self._log_raw(json.dumps(api_messages, indent=2, ensure_ascii=False))
        self._log_raw(f"\n--- API RESPONSE ---")
        self._log_raw(json.dumps(api_response, indent=2, default=str, ensure_ascii=False))
        if tool_descriptions:
            self._log_raw(f"\n--- TOOL CALLS ---")
            for desc in tool_descriptions:
                self._log_raw(desc)

    def _log_compaction(self, reason, removed_turns, summary_content):
        """Log a compaction event"""
        self._log_raw(f"\n=== COMPACTION EVENT ===")
        self._log_raw(f"Reason: {reason}")
        self._log_raw(f"Removed turns: {removed_turns}")
        self._log_raw(f"Summary content:\n{summary_content}")
        self._log_raw("")

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

    def _count_tokens(self, messages):
        """Count tokens for given messages using Anthropic API"""
        try:
            response = self.client.messages.count_tokens(
                model=self.model,
                system=self.system_message,
                tools=self._get_tool_schemas(),
                messages=messages
            )
            return response.input_tokens
        except Exception as e:
            # Fallback: estimate ~4 chars per token
            total_chars = len(self.system_message)
            for msg in messages:
                if isinstance(msg.get("content"), str):
                    total_chars += len(msg["content"])
                elif isinstance(msg.get("content"), list):
                    for item in msg["content"]:
                        if isinstance(item, dict):
                            total_chars += len(json.dumps(item))
            return total_chars // 4

    def _get_turns(self):
        """Split messages into turns (each turn starts with a user message)"""
        turns = []
        current_turn = []
        
        for msg in self.messages:
            if msg["role"] == "user" and current_turn:
                # Check if this is a tool_result (part of current turn) or new user input
                content = msg.get("content", "")
                if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                    # Tool result - part of current turn
                    current_turn.append(msg)
                else:
                    # New user message - start new turn
                    turns.append(current_turn)
                    current_turn = [msg]
            else:
                current_turn.append(msg)
        
        if current_turn:
            turns.append(current_turn)
        
        return turns

    def _compact_context(self, new_user_input):
        """Compact context when exceeding token limit. Returns True if compaction succeeded."""
        turns = self._get_turns()
        
        if len(turns) < 2:
            # Can't compact with less than 2 turns
            print(f"{Fore.RED}⚠️ Cannot compact: conversation too short{Style.RESET_ALL}")
            return False
        
        # Build summarization request with current question
        sr = self.SUMMARIZATION_TEMPLATE.format(current_question=new_user_input)
        sr_message = {"role": "user", "content": sr}
        
        # Try removing oldest turns until SR fits
        for i in range(1, len(turns)):
            # Keep turns from index i onwards, plus SR
            remaining_turns = turns[i:]
            candidate_messages = []
            for turn in remaining_turns:
                candidate_messages.extend(turn)
            candidate_messages.append(sr_message)
            
            token_count = self._count_tokens(candidate_messages)
            
            if token_count <= self.MAX_CONTEXT_TOKENS - self.MAX_OUTPUT_TOKENS:
                # This fits! Get summary
                print(f"{Fore.YELLOW}⚠️ Context limit approaching. Compacting conversation history...{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}📊 Removing {i} oldest turn(s) and creating summary...{Style.RESET_ALL}")
                
                try:
                    # Call API with candidate messages to get summary
                    summary_response = self.client.messages.create(
                        model=self.model,
                        max_tokens=self.MAX_OUTPUT_TOKENS,
                        system=self.system_message,
                        messages=candidate_messages
                    )
                    
                    # Extract summary content
                    summary_text = ""
                    for block in summary_response.content:
                        if hasattr(block, 'text'):
                            summary_text += block.text
                    
                    # Log compaction
                    self._log_compaction(
                        f"Exceeded context ({token_count} tokens attempted)",
                        f"1-{i}",
                        summary_text
                    )
                    
                    # Replace messages with summary as single user message
                    self.messages = [{"role": "user", "content": summary_text}]
                    
                    # Add the assistant acknowledgment so conversation can continue
                    self.messages.append({
                        "role": "assistant", 
                        "content": [{"type": "text", "text": "I understand the context and will continue helping with your request."}]
                    })
                    
                    print(f"{Fore.GREEN}✓ Context compacted successfully{Style.RESET_ALL}")
                    return True
                    
                except Exception as e:
                    print(f"{Fore.RED}⚠️ Compaction failed: {e}{Style.RESET_ALL}")
                    self._log_raw(f"Compaction error: {e}")
                    continue
        
        print(f"{Fore.RED}⚠️ Cannot compact: all attempts exceeded context limit{Style.RESET_ALL}")
        return False

    def _check_and_compact_if_needed(self, new_user_input):
        """Check if adding new input would exceed context, compact if needed"""
        # Build candidate messages
        candidate_messages = self.messages.copy()
        candidate_messages.append({"role": "user", "content": new_user_input})
        
        token_count = self._count_tokens(candidate_messages)
        
        # Reserve space for output
        if token_count > self.MAX_CONTEXT_TOKENS - self.MAX_OUTPUT_TOKENS:
            return self._compact_context(new_user_input)
        
        return True  # No compaction needed

    def chat(self, message):
        """Send message to Claude"""
        self.messages.append({"role": "user", "content": message})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.MAX_OUTPUT_TOKENS,
            system=self.system_message,
            tools=self._get_tool_schemas(),
            messages=self.messages
        )

        # Convert response.content to serializable format for storage
        content_list = []
        for block in response.content:
            if hasattr(block, 'text'):
                content_list.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content_list.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        self.messages.append({"role": "assistant", "content": content_list})
        return response

    def run(self, user_input, max_turns=100, initial_messages=None, display_history=None):
        """Main agent loop
        
        Args:
            user_input: The user's input message
            max_turns: Maximum number of tool-use turns
            initial_messages: Optional messages to restore from a previous session
            display_history: Optional display history for resume functionality
        """
        # Initialize from previous session if provided
        if initial_messages is not None:
            self.messages = initial_messages
        if display_history is not None:
            self.display_history = display_history
        
        # Store original user input for display history
        self.display_history.append(("user", user_input))
        
        # Check and compact if needed before processing
        if self.messages:  # Only check if we have existing messages
            if not self._check_and_compact_if_needed(user_input):
                print(f"{Fore.RED}Failed to process due to context limit{Style.RESET_ALL}")
                return None
        
        current_input = user_input
        turn_num = len([h for h in self.display_history if h[0] == "user"])
        tool_descriptions = []

        for i in range(max_turns):
            response = self.chat(current_input)

            # Collect agent text responses
            agent_texts = []
            for block in response.content:
                if hasattr(block, 'text'):
                    print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {block.text}")
                    agent_texts.append(block.text)

            # Handle tool use
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        # Print concise description to console
                        description = self._get_tool_description(tool_name, tool_input)
                        print(f"{Fore.YELLOW}{description}{Style.RESET_ALL}")
                        tool_descriptions.append(description)

                        # Execute tool
                        tool = self.tool_map[tool_name]
                        result = tool.execute(**tool_input)

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
                # Agent finished - log the complete turn
                if agent_texts:
                    self.display_history.append(("assistant", "\n".join(agent_texts)))
                
                # Log turn to debug file
                self._log_turn(
                    turn_num,
                    user_input,
                    self.messages,
                    {"content": agent_texts, "stop_reason": response.stop_reason},
                    tool_descriptions
                )
                
                return response

        print(f"{Fore.RED}Max turns reached{Style.RESET_ALL}")
        self._log_raw("Max turns reached")
        return response

    def get_state_for_resume(self):
        """Get current state for saving/resuming"""
        return {
            "messages": self.messages,
            "display_history": self.display_history
        }
