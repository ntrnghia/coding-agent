import anthropic
import json
import os
import re
import subprocess
from datetime import datetime
from colorama import Fore, Style, init
from tools import get_tool_description

# Initialize colorama
init(autoreset=True)


class ContainerManager:
    """Manages a persistent Docker container for the agent session"""
    
    DEFAULT_IMAGE = "python:slim"
    
    def __init__(self, container_name, working_dirs=None):
        self.container_name = container_name
        self.working_dirs = working_dirs or []
        self.image = self.DEFAULT_IMAGE
    
    @staticmethod
    def windows_to_mount_path(win_path):
        """Convert Windows path to container mount path
        D:\\Downloads\\coding_agent → /d/downloads/coding_agent
        """
        path = os.path.normpath(win_path).replace('\\', '/')
        if len(path) > 1 and path[1] == ':':
            path = '/' + path[0].lower() + path[2:]
        return path.lower()
    
    @staticmethod
    def windows_to_docker_path(win_path):
        """Convert Windows path to Docker -v compatible format
        D:\\Downloads\\coding_agent → /d/downloads/coding_agent (for Docker on Windows)
        """
        path = win_path.replace('\\', '/')
        if len(path) > 1 and path[1] == ':':
            path = '/' + path[0].lower() + path[2:]
        return path
    
    def is_path_covered(self, new_path):
        """Check if a path is already covered by an existing mount"""
        new_norm = os.path.normpath(new_path).lower()
        for existing in self.working_dirs:
            existing_norm = os.path.normpath(existing).lower()
            if new_norm.startswith(existing_norm + os.sep) or new_norm == existing_norm:
                return True
        return False
    
    def add_working_dir(self, path):
        """Add a working directory. Returns True if added (requires restart), False if already covered."""
        norm_path = os.path.normpath(path)
        if self.is_path_covered(norm_path):
            return False
        self.working_dirs.append(norm_path)
        return True
    
    def _build_mount_args(self):
        """Build Docker -v mount arguments for all working directories"""
        args = []
        for path in self.working_dirs:
            docker_path = self.windows_to_docker_path(path)
            mount_path = self.windows_to_mount_path(path)
            args.extend(["-v", f"{docker_path}:{mount_path}"])
        return args
    
    def get_mount_info(self):
        """Get mount information for system prompt"""
        info = []
        for path in self.working_dirs:
            mount_path = self.windows_to_mount_path(path)
            info.append(f"  - {path} → {mount_path}")
        return "\n".join(info)
    
    def container_exists(self):
        """Check if container exists (running or stopped)"""
        try:
            result = subprocess.run(
                ["docker", "inspect", self.container_name],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except:
            return False
    
    def container_running(self):
        """Check if container is currently running"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                capture_output=True,
                text=True
            )
            return result.returncode == 0 and result.stdout.strip() == "true"
        except:
            return False
    
    def start(self):
        """Start the container (create if doesn't exist, start if stopped)"""
        if self.container_running():
            return {"status": "already_running", "container": self.container_name}
        
        if self.container_exists():
            # Container exists but stopped - start it
            result = subprocess.run(
                ["docker", "start", self.container_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return {"status": "started", "container": self.container_name}
            else:
                # Container might be corrupted, remove and recreate
                subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True)
        
        # Create new container
        return self._create_container()
    
    def _create_container(self):
        """Create a new container with all mounts"""
        if not self.working_dirs:
            return {"error": "No working directories to mount"}
        
        try:
            # Pull image first
            subprocess.run(["docker", "pull", self.image], capture_output=True, text=True)
            
            # Build command
            cmd = ["docker", "run", "-d", "--name", self.container_name]
            cmd.extend(self._build_mount_args())
            cmd.extend([self.image, "tail", "-f", "/dev/null"])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                return {"error": f"Failed to create container: {result.stderr}"}
            
            return {"status": "created", "container": self.container_name}
        
        except FileNotFoundError:
            return {"error": "Docker is not installed or not in PATH"}
        except Exception as e:
            return {"error": str(e)}
    
    def restart_with_new_mounts(self):
        """Stop, remove, and recreate container with updated mounts"""
        # Stop and remove existing
        subprocess.run(["docker", "stop", self.container_name], capture_output=True)
        subprocess.run(["docker", "rm", self.container_name], capture_output=True)
        
        # Create with new mounts
        return self._create_container()
    
    def exec(self, command):
        """Execute command in the container"""
        if not self.container_running():
            return {"error": "Container not running"}
        
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "sh", "-c", command],
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
    
    def stop(self):
        """Stop the container (don't remove it)"""
        if not self.container_exists():
            return {"status": "not_found"}
        
        result = subprocess.run(
            ["docker", "stop", self.container_name],
            capture_output=True,
            text=True
        )
        return {"status": "stopped" if result.returncode == 0 else "error"}
    
    def remove(self):
        """Remove the container completely"""
        subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True)
        return {"status": "removed"}


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

    def __init__(self, tools, workspace_dir, debug_file=None, container_info=None):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-opus-4-5-20251101"
        self.workspace_dir = workspace_dir
        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}
        self.display_history = []  # Store original user inputs for resume display
        self.has_interaction = False  # Track if agent had successful response
        
        # Setup debug log file and container
        if debug_file:
            # Resume: use existing debug file
            self.debug_file = debug_file
            # Extract session name from debug file (debug_YYYYMMDD_HHMMSS.txt)
            debug_basename = os.path.basename(debug_file)
            session_name = debug_basename.replace("debug_", "").replace(".txt", "")
            
            # Restore or recreate container
            if container_info:
                working_dirs = container_info.get("working_dirs", [])
            else:
                working_dirs = []
            
            self.container_manager = ContainerManager(
                container_name=f"agent_{session_name}",
                working_dirs=working_dirs
            )
            self._log_resume()
        else:
            # New session: create new debug file
            debug_dir = os.path.join(workspace_dir, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.debug_file = os.path.join(debug_dir, f"debug_{timestamp}.txt")
            
            # Create new container manager
            self.container_manager = ContainerManager(
                container_name=f"agent_{timestamp}"
            )
            self._log_session_start()
        
        # Build system message with mount info
        self._update_system_message()
    
    def _update_system_message(self):
        """Update system message with current mount mappings"""
        mount_info = self.container_manager.get_mount_info()
        
        if mount_info:
            mount_section = f"""
Current mounted directories (container: {self.container_manager.container_name}):
{mount_info}

Use these paths directly in docker_sandbox with action="exec"."""
        else:
            mount_section = """
No directories are currently mounted. Use docker_sandbox with action="start" to mount a directory first."""
        
        self.system_message = f"""You are an AI coding assistant with access to a workspace at {self.workspace_dir}.
You can execute shell commands, search the web, and fetch documentation.

IMPORTANT: When asked to work with directories OUTSIDE your workspace:
1. Use the docker_sandbox tool to create a container environment
2. First call docker_sandbox with action="start" and mount_path="<the external path>"
3. Then use action="exec" with commands like "ls -la /d/path/to/dir", etc.
4. Directories are mounted at their Unix-style path (D:\\path → /d/path)
5. To create files, use heredoc syntax: cat > /d/path/file.py << 'EOF'\n...content...\nEOF
{mount_section}

Container behavior:
- A single container persists for the entire session
- All directories are mounted in the same container
- Adding a new directory may require a container restart (you'll be notified)
- Only call action="stop" when completely finished with all work

This provides a consistent Linux environment for all file operations.

Always verify your actions and explain what you're doing."""
    
    def add_working_directory(self, path):
        """Add a working directory to the container. Returns status message."""
        needs_restart = self.container_manager.add_working_dir(path)
        
        if not needs_restart:
            mount_path = ContainerManager.windows_to_mount_path(path)
            return {"status": "already_mounted", "mount_path": mount_path}
        
        # Need to restart container with new mount
        if self.container_manager.container_running():
            print(f"{Fore.YELLOW}🔄 Restarting container to add new mount...{Style.RESET_ALL}")
            result = self.container_manager.restart_with_new_mounts()
        else:
            result = self.container_manager.start()
        
        # Update system message with new mount info
        self._update_system_message()
        
        # Log container info for resume
        self._log_container_info()
        
        mount_path = ContainerManager.windows_to_mount_path(path)
        result["mount_path"] = mount_path
        return result
    
    def _log_container_info(self):
        """Log container info to debug file for resume"""
        info = {
            "container_name": self.container_manager.container_name,
            "working_dirs": self.container_manager.working_dirs
        }
        self._log_raw(f"\n=== CONTAINER INFO ===")
        self._log_raw(json.dumps(info))
    
    def cleanup_empty_session(self):
        """Clean up if no interaction occurred. Returns True if cleaned up."""
        if self.has_interaction:
            return False
        
        # Remove container
        self.container_manager.remove()
        
        # Remove debug file
        if os.path.exists(self.debug_file):
            os.remove(self.debug_file)
            print(f"{Fore.YELLOW}🧹 Cleaned up empty session{Style.RESET_ALL}")
        
        return True
    
    def stop_container(self):
        """Stop the container gracefully"""
        if self.container_manager.container_exists():
            self.container_manager.stop()
            print(f"{Fore.CYAN}🐳 Container stopped: {self.container_manager.container_name}{Style.RESET_ALL}")

    def _log_session_start(self):
        """Log session start with metadata"""
        self._log_raw(f"=== SESSION START ===")
        self._log_raw(f"Timestamp: {datetime.now().isoformat()}")
        self._log_raw(f"Workspace: {self.workspace_dir}")
        self._log_raw(f"Model: {self.model}")
        self._log_raw(f"Container: {self.container_manager.container_name}")
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

    def _log_turn_start(self, turn_num, user_input):
        """Log start of a new turn with user input"""
        self._log_raw(f"\n=== TURN {turn_num} ===")
        self._log_raw(f"--- USER ---")
        if isinstance(user_input, str):
            self._log_raw(user_input)
        else:
            # Tool results
            self._log_raw(json.dumps(user_input, indent=2, ensure_ascii=False))

    def _log_assistant(self, content_list):
        """Log assistant response immediately after API call"""
        self._log_raw(f"\n--- ASSISTANT ---")
        self._log_raw(json.dumps(content_list, indent=2, ensure_ascii=False))

    def _log_tool_results(self, tool_results):
        """Log tool results immediately after execution"""
        self._log_raw(f"\n--- TOOL_RESULT ---")
        self._log_raw(json.dumps(tool_results, indent=2, ensure_ascii=False))

    def _log_end_turn(self):
        """Mark turn as complete"""
        self._log_raw(f"\n--- END_TURN ---")

    def _log_compaction(self, reason, removed_turns, summary_content):
        """Log a compaction event"""
        self._log_raw(f"\n=== COMPACTION EVENT ===")
        self._log_raw(f"Reason: {reason}")
        self._log_raw(f"Removed turns: {removed_turns}")
        self._log_raw(f"Summary content:\n{summary_content}")
        self._log_raw("")

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
        """Send message to Claude and update messages array"""
        self.messages.append({"role": "user", "content": message})
        response, content_list = self._call_api()
        self.messages.append({"role": "assistant", "content": content_list})
        return response
    
    def _call_api(self):
        """Call Claude API with current messages. Does NOT modify self.messages.
        
        Returns:
            tuple: (response, content_list) where content_list is serializable format
        """
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

        return response, content_list

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
        is_first_sub_turn = True

        for i in range(max_turns):
            # Log user input / tool results at start of sub-turn
            if is_first_sub_turn:
                self._log_turn_start(turn_num, current_input)
                is_first_sub_turn = False
            else:
                # Continuing turn with tool results - already logged in previous iteration
                pass

            response = self.chat(current_input)

            # Convert response to serializable format and log immediately
            content_list = []
            agent_texts = []
            for block in response.content:
                if hasattr(block, 'text'):
                    content_list.append({"type": "text", "text": block.text})
                    print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {block.text}")
                    agent_texts.append(block.text)
                elif block.type == "tool_use":
                    content_list.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })

            # Log assistant response immediately
            self._log_assistant(content_list)

            # Handle tool use
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        # Print concise description to console
                        description = get_tool_description(tool_name, tool_input)
                        print(f"{Fore.YELLOW}{description}{Style.RESET_ALL}")
                        self.display_history.append(("tool", description))

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

                # Log tool results immediately
                self._log_tool_results(tool_results)
                current_input = tool_results
            else:
                # Agent finished - mark as having interaction
                self.has_interaction = True
                
                # Log end of turn
                self._log_end_turn()
                
                if agent_texts:
                    self.display_history.append(("assistant", "\n".join(agent_texts)))
                
                return response

        print(f"{Fore.RED}Max turns reached{Style.RESET_ALL}")
        self._log_raw("Max turns reached")
        return response

    def continue_incomplete_turn(self, incomplete_turn, max_turns=100):
        """Continue an incomplete turn from a crash
        
        Args:
            incomplete_turn: dict with 'type' and relevant data
                - {"type": "continue"} - messages already has tool_results, just call API
                - {"type": "execute_tools", "tool_uses": [...]} - execute tools, add to messages, then call API
            max_turns: Maximum remaining tool-use iterations
        """
        if incomplete_turn["type"] == "execute_tools":
            # Need to execute tools that weren't run before crash
            tool_uses = incomplete_turn["tool_uses"]
            tool_results = []
            
            for tool_use in tool_uses:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                
                description = get_tool_description(tool_name, tool_input)
                print(f"{Fore.YELLOW}(Executing) {description}{Style.RESET_ALL}")
                
                tool = self.tool_map[tool_name]
                result = tool.execute(**tool_input)
                
                if "error" in result:
                    print(f"{Fore.RED}  ✗ Error: {result['error']}{Style.RESET_ALL}")
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": json.dumps(result)
                })
            
            # Add tool results to messages and log
            self.messages.append({"role": "user", "content": tool_results})
            self._log_tool_results(tool_results)
            
        elif incomplete_turn["type"] == "continue":
            # Messages already has everything (including tool_results)
            # Just need to call API to continue generation
            pass
        else:
            print(f"{Fore.RED}Unknown incomplete turn type: {incomplete_turn['type']}{Style.RESET_ALL}")
            return None
        
        # Continue the agent loop - call API with current messages
        for i in range(max_turns):
            # Call API without modifying messages first (they're already set up)
            response, content_list = self._call_api()
            
            # Add assistant response to messages
            self.messages.append({"role": "assistant", "content": content_list})
            
            # Extract text and log
            agent_texts = [b["text"] for b in content_list if b.get("type") == "text"]
            for text in agent_texts:
                print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {text}")
            
            # Log assistant response immediately
            self._log_assistant(content_list)
            
            if response.stop_reason == "tool_use":
                tool_results = []
                
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        
                        description = get_tool_description(tool_name, tool_input)
                        print(f"{Fore.YELLOW}{description}{Style.RESET_ALL}")
                        self.display_history.append(("tool", description))
                        
                        tool = self.tool_map[tool_name]
                        result = tool.execute(**tool_input)
                        
                        if "error" in result:
                            print(f"{Fore.RED}  ✗ Error: {result['error']}{Style.RESET_ALL}")
                        
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })
                
                # Add tool results to messages and log
                self.messages.append({"role": "user", "content": tool_results})
                self._log_tool_results(tool_results)
            else:
                self.has_interaction = True
                self._log_end_turn()
                
                if agent_texts:
                    self.display_history.append(("assistant", "\n".join(agent_texts)))
                
                return response
        
        print(f"{Fore.RED}Max turns reached{Style.RESET_ALL}")
        return response

    def get_state_for_resume(self):
        """Get current state for saving/resuming"""
        return {
            "messages": self.messages,
            "display_history": self.display_history,
            "container_info": {
                "container_name": self.container_manager.container_name,
                "working_dirs": self.container_manager.working_dirs
            }
        }
