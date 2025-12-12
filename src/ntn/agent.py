import anthropic
import json
import os
import re
import subprocess
import time
from datetime import datetime
from colorama import Fore, Style, init
from .tools import get_tool_description
from .prompts import get_system_prompt, get_mount_section_text, get_no_mount_section_text

# Initialize colorama
init(autoreset=True)

# UI constants
DIVIDER_WIDTH = 80
DIVIDER_LINE = '─' * DIVIDER_WIDTH

def print_divider():
    """Print a horizontal divider line"""
    print(f"{Style.DIM}{DIVIDER_LINE}{Style.RESET_ALL}")


class ContainerManager:
    """Manages a persistent Docker container for the agent session"""
    
    DEFAULT_IMAGE = "python:slim"
    
    def __init__(self, container_name, working_dirs=None):
        self.container_name = container_name
        self.working_dirs = working_dirs or []
        self.image = self.DEFAULT_IMAGE
    
    @staticmethod
    def convert_path(win_path, lowercase=False):
        """Convert Windows path to Unix-style path
        D:\\Downloads\\coding_agent → /d/downloads/coding_agent
        
        Args:
            win_path: Windows path to convert
            lowercase: If True, lowercase entire path (for mount targets)
        """
        path = os.path.normpath(win_path).replace('\\', '/')
        if len(path) > 1 and path[1] == ':':
            path = '/' + path[0].lower() + path[2:]
        return path.lower() if lowercase else path
    
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
            docker_path = self.convert_path(path)
            mount_path = self.convert_path(path, lowercase=True)
            args.extend(["-v", f"{docker_path}:{mount_path}"])
        return args
    
    def get_mount_info(self):
        """Get mount information for system prompt"""
        return "\n".join(
            f"  - {path} → {self.convert_path(path, lowercase=True)}"
            for path in self.working_dirs
        )
    
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
        """Execute command in the container. Auto-recovers if container not running."""
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "sh", "-c", command],
                capture_output=True,
                text=True
            )
            
            # Check if container doesn't exist or isn't running
            if result.returncode != 0 and "No such container" in result.stderr:
                # Try to start/create container and retry once
                if self.working_dirs:
                    start_result = self.start()
                    if start_result.get("error"):
                        return {"error": f"Container not running and failed to start: {start_result['error']}"}
                    # Retry the command
                    result = subprocess.run(
                        ["docker", "exec", self.container_name, "sh", "-c", command],
                        capture_output=True,
                        text=True
                    )
                else:
                    return {"error": "Container not running. Use action='start' to mount a directory first."}
            
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
    
    # Model configurations: short name -> full model ID
    MODELS = {
        "opus": "claude-opus-4-5",
        "sonnet": "claude-sonnet-4-5",
        "haiku": "claude-haiku-4-5",
    }
    
    # Context limits by model
    MAX_CONTEXT_TOKENS = 200000
    MAX_OUTPUT_TOKENS = 64000  # Conservative output limit
    
    # Pricing per 1M tokens (USD)
    MODEL_PRICING = {
        "claude-opus-4-5": {"input": 15, "output": 75, "cache_write": 18.75, "cache_read": 1.50},
        "claude-sonnet-4-5": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30},
        "claude-haiku-4-5": {"input": 0.80, "output": 4, "cache_write": 1.0, "cache_read": 0.08},
    }
    
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

    def __init__(self, tools, workspace_dir, debug_file=None, container_info=None, stream=False, think=False, model="opus"):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        # Resolve model short name to full model ID
        self.model = self.MODELS.get(model, self.MODELS["opus"])
        self.workspace_dir = workspace_dir
        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}
        self.display_history = []  # Store original user inputs for resume display
        self.stream = stream  # Enable streaming output
        self.think = think  # Enable extended thinking
        
        # Extended thinking budget (min 1024, we use 16K for good reasoning)
        self.thinking_budget = 64000 - 1
        
        # Rate limit tracking (populated after first API call)
        self.rate_limit_info = None  # Will store input/output/request limits and remaining
        self.context_tokens = 0  # Current context size from last response
        
        # Cost tracking
        self.session_cost = 0.0  # Total cost of entire conversation (including before resume)
        self.run_cost = 0.0  # Cost since current run started (resets on resume)
        self.last_req_cost = 0.0  # Cost of last API request
        self.last_usage = None  # Last API usage tokens (for logging)
        
        # Setup debug log file and container
        if debug_file:
            # Resume: use existing debug file
            self.debug_file = debug_file
            
            # Get container name from stored info (resilient to filename changes)
            if container_info and container_info.get("container_name"):
                container_name = container_info["container_name"]
                working_dirs = container_info.get("working_dirs", [])
            else:
                # Fallback: derive from filename
                debug_basename = os.path.basename(debug_file)
                session_name = debug_basename.replace("debug_", "").replace(".txt", "")
                container_name = f"agent_{session_name}"
                working_dirs = []
            
            # Ensure workspace is in working_dirs for resume
            if workspace_dir not in working_dirs:
                working_dirs.append(workspace_dir)
            
            self.container_manager = ContainerManager(
                container_name=container_name,
                working_dirs=working_dirs
            )
            # Track what was already logged (from previous session)
            self._last_logged_container_info = container_info
            self._log_resume()
        else:
            # New session: create new debug file
            debug_dir = os.path.join(workspace_dir, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.debug_file = os.path.join(debug_dir, f"debug_{timestamp}.txt")
            
            # Create new container manager with workspace pre-mounted
            self.container_manager = ContainerManager(
                container_name=f"agent_{timestamp}",
                working_dirs=[workspace_dir]  # Auto-mount workspace
            )
            # Track what was logged (None = nothing yet)
            self._last_logged_container_info = None
            self._log_session_start()
        
        # Start container with workspace mounted
        self._ensure_container_started()
        
        # Build system message with mount info
        self._update_system_message()
    
    def _ensure_container_started(self):
        """Ensure the container is started with all mounts"""
        if self.container_manager.working_dirs:
            print(f"{Fore.YELLOW}🐳 Starting Docker container...{Style.RESET_ALL}")
            result = self.container_manager.start()
            if "error" in result:
                print(f"{Fore.RED}⚠️  Docker error: {result['error']}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}   File operations will use Windows commands as fallback.{Style.RESET_ALL}")
            elif result.get("status") == "already_running":
                print(f"{Fore.GREEN}🐳 Container already running: {self.container_manager.container_name}{Style.RESET_ALL}")
            else:
                print(f"{Fore.GREEN}🐳 Container ready: {self.container_manager.container_name}{Style.RESET_ALL}")
                self._log_container_info()
    
    def _update_system_message(self):
        """Update system message with current mount mappings.
        
        The system prompt is designed to reach ~4500 tokens when combined with tools (~1113 tokens)
        to meet Opus 4.5's 4096 minimum cacheable token requirement.
        """
        mount_info = self.container_manager.get_mount_info()
        
        if mount_info:
            mount_section = get_mount_section_text(
                self.container_manager.container_name,
                mount_info
            )
        else:
            mount_section = get_no_mount_section_text()
        
        self.system_prompt = get_system_prompt(self.workspace_dir, mount_section)
    
    def add_working_directory(self, path):
        """Add a working directory to the container. Returns status message."""
        needs_restart = self.container_manager.add_working_dir(path)
        mount_path = ContainerManager.convert_path(path, lowercase=True)
        
        if not needs_restart:
            return {"status": "already_mounted", "mount_path": mount_path}
        
        # Need to restart container with new mount
        result = (self.container_manager.restart_with_new_mounts() 
                  if self.container_manager.container_running() 
                  else self.container_manager.start())
        
        # Update system message and log container info
        self._update_system_message()
        self._log_container_info()
        
        result["mount_path"] = mount_path
        return result
    
    def _log_container_info(self):
        """Log container info to debug file only if changed since last log"""
        info = {
            "container_name": self.container_manager.container_name,
            "working_dirs": self.container_manager.working_dirs
        }
        # Only log if different from last logged info
        if info != self._last_logged_container_info:
            self._log_raw(f"\n=== CONTAINER INFO ===")
            self._log_raw(json.dumps(info))
            self._last_logged_container_info = info
    
    def cleanup_empty_session(self):
        """Clean up if no user messages. Returns True if cleaned up."""
        # Check if there are any user messages in the conversation
        has_user_message = any(
            msg.get("role") == "user" and isinstance(msg.get("content"), str)
            for msg in self.messages
        )
        
        if has_user_message:
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

    def _log_resume(self):
        """Log resume marker"""
        self._log_raw(f"\n=== RESUME ===")
        self._log_raw(f"Timestamp: {datetime.now().isoformat()}")

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

    def _log_req_cost(self):
        """Log request usage tokens immediately after API call (for cost calculation on resume)"""
        if self.last_usage:
            self._log_raw(f"--- USAGE: {json.dumps(self.last_usage)} ---")

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

    def _get_tool_schemas(self):
        """Get tool schemas with cache_control on the last tool for prompt caching."""
        schemas = [tool.get_schema() for tool in self.tools]
        if schemas:
            # Add cache_control to the last tool for caching
            schemas[-1] = {**schemas[-1], "cache_control": {"type": "ephemeral"}}
        return schemas
    
    def _get_system_with_cache(self):
        """Get system message formatted for prompt caching.
        
        Returns system as an array with cache_control on the last block.
        Cached tokens don't count toward rate limits!
        """
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]

    def _count_tokens(self, messages):
        """Count tokens for given messages using Anthropic API"""
        try:
            kwargs = {
                "model": self.model,
                "system": self._get_system_with_cache(),
                "tools": self._get_tool_schemas(),
                "messages": messages
            }
            # Include thinking config if enabled - affects token counting
            if self.think:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.thinking_budget
                }
            response = self.client.messages.count_tokens(**kwargs)
            return response.input_tokens
        except Exception as e:
            # Fallback: estimate ~4 chars per token
            total_chars = len(self.system_prompt)
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
                    # Call API with candidate messages to get summary (with caching)
                    summary_response = self.client.messages.create(
                        model=self.model,
                        max_tokens=self.MAX_OUTPUT_TOKENS,
                        system=self._get_system_with_cache(),
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
    
    def _call_api(self, print_text=True):
        """Call Claude API with current messages. Does NOT modify self.messages.
        
        Implements retry using retry-after header for rate limit errors.
        Supports streaming and extended thinking based on instance flags.
        
        Args:
            print_text: Whether to print text responses (True for run(), False for chat())
        
        Returns:
            tuple: (response, content_list) where content_list is serializable format
        """
        max_retries = 3
        
        # Build API kwargs with prompt caching
        # Cached tokens don't count toward rate limits!
        api_kwargs = {
            "model": self.model,
            "max_tokens": self.MAX_OUTPUT_TOKENS,
            "system": self._get_system_with_cache(),
            "tools": self._get_tool_schemas(),
            "messages": self.messages
        }
        
        # Add extended thinking if enabled
        if self.think:
            api_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget
            }
            # Extended thinking requires temperature=1 (default, so we don't set it)
        
        for attempt in range(max_retries + 1):
            try:
                if self.stream:
                    return self._call_api_streaming(api_kwargs, print_text)
                else:
                    response = self.client.messages.create(**api_kwargs)
                    break  # Success, exit retry loop
            except anthropic.RateLimitError as e:
                if attempt == max_retries:
                    print(f"{Fore.RED}Rate limit exceeded after {max_retries + 1} attempts{Style.RESET_ALL}")
                    raise
                # Get retry-after from response headers, fallback to 30s
                retry_after = None
                if hasattr(e, 'response') and e.response is not None:
                    retry_after = e.response.headers.get("retry-after")
                delay = int(retry_after) if retry_after else 30
                print(f"{Fore.YELLOW}⏳ Rate limited, waiting {delay}s before retry ({attempt + 1}/{max_retries})...{Style.RESET_ALL}")
                time.sleep(delay)

        # Capture rate limit info from response headers
        self._capture_rate_limit_info(response)
        
        # Convert response.content to serializable format for storage
        content_list = self._response_to_content_list(response, print_text)
        return response, content_list
    
    def _call_api_streaming(self, api_kwargs, print_text=True):
        """Handle streaming API call with real-time text output.
        
        Text is streamed character-by-character.
        Tool use blocks are accumulated and shown when complete.
        Thinking blocks just show "🧠 Thinking..." indicator.
        """
        content_list = []
        current_text = ""
        current_thinking = ""  # Accumulate thinking content
        current_thinking_signature = ""  # Accumulate signature
        current_tool_use = None
        tool_input_json = ""
        thinking_shown = False
        text_started = False
        
        with self.client.messages.stream(**api_kwargs) as stream:
            for event in stream:
                # Handle different event types
                if event.type == "content_block_start":
                    if hasattr(event.content_block, 'type'):
                        if event.content_block.type == "thinking":
                            current_thinking = ""  # Start new thinking block
                            current_thinking_signature = ""
                            if not thinking_shown and print_text:
                                print(f"{Fore.MAGENTA}🧠 Thinking...{Style.RESET_ALL}")
                                thinking_shown = True
                        elif event.content_block.type == "text":
                            if print_text:
                                print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} ", end="", flush=True)
                            text_started = True
                        elif event.content_block.type == "tool_use":
                            current_tool_use = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input": {}
                            }
                            tool_input_json = ""
                
                elif event.type == "content_block_delta":
                    if hasattr(event.delta, 'type'):
                        if event.delta.type == "thinking_delta":
                            # Accumulate thinking content (don't print)
                            current_thinking += event.delta.thinking
                        elif event.delta.type == "signature_delta":
                            # Capture the signature for the thinking block
                            current_thinking_signature += event.delta.signature
                        elif event.delta.type == "text_delta":
                            text = event.delta.text
                            current_text += text
                            if print_text:
                                print(f"{Fore.GREEN}{text}", end="", flush=True)
                        elif event.delta.type == "input_json_delta":
                            tool_input_json += event.delta.partial_json
                
                elif event.type == "content_block_stop":
                    # Store thinking block if we accumulated any (with signature)
                    if current_thinking or current_thinking_signature:
                        thinking_block = {"type": "thinking", "thinking": current_thinking}
                        if current_thinking_signature:
                            thinking_block["signature"] = current_thinking_signature
                        content_list.append(thinking_block)
                        current_thinking = ""
                        current_thinking_signature = ""
                    if current_text:
                        content_list.append({"type": "text", "text": current_text})
                        if print_text and text_started:
                            print(Style.RESET_ALL)  # Reset color and new line after text
                        current_text = ""
                        text_started = False
                    if current_tool_use:
                        # Parse accumulated JSON
                        try:
                            current_tool_use["input"] = json.loads(tool_input_json) if tool_input_json else {}
                        except json.JSONDecodeError:
                            current_tool_use["input"] = {}
                        content_list.append({
                            "type": "tool_use",
                            "id": current_tool_use["id"],
                            "name": current_tool_use["name"],
                            "input": current_tool_use["input"]
                        })
                        current_tool_use = None
                        tool_input_json = ""
            
            # Get final response for stop_reason
            final_response = stream.get_final_message()
        
        # Capture rate limit info from streaming response headers
        if hasattr(stream, 'response') and stream.response:
            self._capture_rate_limit_info_from_headers(stream.response.headers, final_response)
        
        return final_response, content_list
    
    def _capture_rate_limit_info(self, response):
        """Capture rate limit info from API response headers and usage"""
        # Get headers from the HTTP response
        if hasattr(response, '_raw_response') and response._raw_response:
            headers = response._raw_response.headers
        else:
            headers = {}
        
        self._capture_rate_limit_info_from_headers(headers, response)
    
    def _capture_rate_limit_info_from_headers(self, headers, response):
        """Extract rate limit info from headers dict and response usage, calculate cost"""
        def get_int(key):
            val = headers.get(key)
            return int(val) if val else None
        
        self.rate_limit_info = {
            "request_limit": get_int("anthropic-ratelimit-requests-limit"),
            "request_remaining": get_int("anthropic-ratelimit-requests-remaining"),
            "input_limit": get_int("anthropic-ratelimit-input-tokens-limit"),
            "input_remaining": get_int("anthropic-ratelimit-input-tokens-remaining"),
            "output_limit": get_int("anthropic-ratelimit-output-tokens-limit"),
            "output_remaining": get_int("anthropic-ratelimit-output-tokens-remaining"),
        }
        
        # Track context usage and calculate cost from response
        if hasattr(response, 'usage'):
            usage = response.usage
            # Total context = all input tokens used (not output - that has separate limit)
            input_tokens = getattr(usage, 'input_tokens', 0) or 0
            cache_creation = getattr(usage, 'cache_creation_input_tokens', 0) or 0
            cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
            
            # Context is total input tokens (what counts toward 200K limit)
            self.context_tokens = input_tokens + cache_creation + cache_read
            
            # Calculate cost
            self._calculate_cost(usage)
    
    @staticmethod
    def calculate_cost_from_usage(usage, model="claude-opus-4-5-20251101"):
        """Calculate cost from usage dict. Usage keys: input, output, cache_write, cache_read"""
        pricing = CodingAgent.MODEL_PRICING.get(model, {})
        if not pricing:
            return 0.0
        return sum(usage.get(k, 0) * pricing.get(k, 0) / 1_000_000 for k in pricing)
    
    def _calculate_cost(self, usage):
        """Calculate cost from API usage and update session totals"""
        self.last_usage = {
            "input": getattr(usage, 'input_tokens', 0) or 0,
            "output": getattr(usage, 'output_tokens', 0) or 0,
            "cache_write": getattr(usage, 'cache_creation_input_tokens', 0) or 0,
            "cache_read": getattr(usage, 'cache_read_input_tokens', 0) or 0
        }
        self.last_req_cost = self.calculate_cost_from_usage(self.last_usage, self.model)
        self.run_cost += self.last_req_cost
        self.session_cost += self.last_req_cost
    
    def _format_token_count(self, count):
        """Format token count with K suffix for large numbers"""
        if count >= 1000:
            return f"{count/1000:.0f}K"
        return str(count)
    
    def print_status(self):
        """Print rate limit, cost, and context usage status with visual separator"""
        inner_width = DIVIDER_WIDTH - 2  # Account for side borders
        
        # Cost line: run_cost = since this run started, session_cost = total conversation
        cost_line = f"💰 Cost: ${self.run_cost:.4f} (total: ${self.session_cost:.4f})"
        
        # Requests rate limit
        if self.rate_limit_info and self.rate_limit_info.get("request_limit") and self.rate_limit_info.get("request_remaining") is not None:
            rl = self.rate_limit_info
            req_used = rl["request_limit"] - rl["request_remaining"]
            req_pct = (req_used / rl["request_limit"]) * 100
            req_str = f"{req_used}/{rl['request_limit']} ({req_pct:.0f}%)"
        else:
            req_str = "N/A"
        
        # Input rate limit (show used/limit with K suffix)
        if self.rate_limit_info and self.rate_limit_info.get("input_limit") and self.rate_limit_info.get("input_remaining") is not None:
            rl = self.rate_limit_info
            input_used = rl["input_limit"] - rl["input_remaining"]
            input_pct = (input_used / rl["input_limit"]) * 100
            input_str = f"{self._format_token_count(input_used)}/{self._format_token_count(rl['input_limit'])} ({input_pct:.0f}%)"
        else:
            input_str = "N/A"
        
        # Output rate limit (show used/limit with K suffix)
        if self.rate_limit_info and self.rate_limit_info.get("output_limit") and self.rate_limit_info.get("output_remaining") is not None:
            rl = self.rate_limit_info
            output_used = rl["output_limit"] - rl["output_remaining"]
            output_pct = (output_used / rl["output_limit"]) * 100
            output_str = f"{self._format_token_count(output_used)}/{self._format_token_count(rl['output_limit'])} ({output_pct:.0f}%)"
        else:
            output_str = "N/A"
        
        rate_line = f"📊 Requests: {req_str} | Input: {input_str} | Output: {output_str}"
        
        # Context usage - always calculate current tokens for accurate display
        if self.messages:
            self.context_tokens = self._count_tokens(self.messages)
        
        context_pct = (self.context_tokens / self.MAX_CONTEXT_TOKENS) * 100
        context_line = f"🧠 Context: {self.context_tokens:,}/{self.MAX_CONTEXT_TOKENS:,} ({context_pct:.0f}%)"
        
        # Print separator and box
        print()  # Blank line for spacing
        print_divider()
        print(f"{Style.DIM}╭{'─' * inner_width}╮{Style.RESET_ALL}")
        print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {cost_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
        print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {rate_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
        print(f"{Style.DIM}│{Style.RESET_ALL}{Fore.CYAN} {context_line.ljust(inner_width - 1)}{Style.DIM}│{Style.RESET_ALL}")
        print(f"{Style.DIM}╰{'─' * inner_width}╯{Style.RESET_ALL}")
        print()  # Blank line before user prompt
    
    def _response_to_content_list(self, response, print_text=True):
        """Convert response.content to serializable format and optionally print text."""
        content_list = []
        for block in response.content:
            if block.type == "thinking":
                # Store thinking block with signature for resume capability
                thinking_block = {"type": "thinking", "thinking": block.thinking}
                if hasattr(block, 'signature') and block.signature:
                    thinking_block["signature"] = block.signature
                content_list.append(thinking_block)
                if print_text:
                    print(f"{Fore.MAGENTA}🧠 Thinking...{Style.RESET_ALL}")
            elif hasattr(block, 'text'):
                content_list.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content_list.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
        return content_list
    
    def _ensure_thinking_blocks(self):
        """Ensure thinking blocks are valid for API submission.
        
        When resuming with thinking enabled, thinking blocks must have valid signatures.
        This method removes any thinking blocks that lack signatures (from old non-thinking
        sessions or corrupted data). The API accepts messages without thinking blocks
        for non-tool-result user messages.
        """
        for msg in self.messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    # Filter out thinking blocks without valid signatures
                    msg["content"] = [
                        block for block in content
                        if block.get("type") != "thinking" or block.get("signature")
                    ]

    def _execute_tools(self, response, prefix=""):
        """Execute tool calls from response and return tool_results.
        
        Args:
            response: API response containing tool_use blocks
            prefix: Optional prefix for display (e.g., "(Executing) " for resume)
        
        Returns:
            list: Tool results ready to be added to messages
        """
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input
                
                description = get_tool_description(tool_name, tool_input)
                print(f"{Fore.YELLOW}{prefix}{description}{Style.RESET_ALL}")
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
        return tool_results

    def _process_response(self, response, content_list):
        """Process response content - print if not streaming, update display history.
        
        Args:
            response: API response object
            content_list: Serialized content list from response
        
        Returns:
            list: Text blocks from the response
        """
        agent_texts = []
        for block in content_list:
            if block.get("type") == "thinking":
                self.display_history.append(("thinking", "🧠 Thinking..."))
                if not self.stream:
                    print(f"{Fore.MAGENTA}🧠 Thinking...{Style.RESET_ALL}")
            elif block.get("type") == "text":
                if not self.stream:
                    print(f"{Fore.GREEN}Agent: {block['text']}{Style.RESET_ALL}")
                agent_texts.append(block["text"])
        return agent_texts

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
        
        # If thinking is enabled, ensure all assistant messages have thinking blocks
        if self.think and self.messages:
            self._ensure_thinking_blocks()
        
        # Check and compact if needed before processing
        if self.messages and not self._check_and_compact_if_needed(user_input):
            print(f"{Fore.RED}Failed to process due to context limit{Style.RESET_ALL}")
            return None
        
        turn_num = len([h for h in self.display_history if h[0] == "user"])
        self._log_turn_start(turn_num, user_input)
        
        return self._agent_loop(user_input, max_turns)

    def _agent_loop(self, initial_input, max_turns):
        """Core agent loop for processing messages and tool calls.
        
        Args:
            initial_input: User message or tool_results to start with (None to skip first chat)
            max_turns: Maximum iterations
        
        Returns:
            Final API response
        """
        current_input = initial_input
        
        for _ in range(max_turns):
            # Call API
            if current_input is not None:
                response = self.chat(current_input)
                content_list = self.messages[-1]["content"]  # chat() already appended
            else:
                # Direct API call (for continue_incomplete_turn)
                response, content_list = self._call_api()
                self.messages.append({"role": "assistant", "content": content_list})
            
            # Log and process response
            self._log_assistant(content_list)
            self._log_req_cost()
            agent_texts = self._process_response(response, content_list)
            
            if response.stop_reason == "tool_use":
                tool_results = self._execute_tools(response)
                self._log_tool_results(tool_results)
                current_input = tool_results
            else:
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
            # Execute tools that weren't run before crash
            tool_results = []
            for tool_use in incomplete_turn["tool_uses"]:
                description = get_tool_description(tool_use["name"], tool_use["input"])
                print(f"{Fore.YELLOW}(Resuming) {description}{Style.RESET_ALL}")
                
                result = self.tool_map[tool_use["name"]].execute(**tool_use["input"])
                if "error" in result:
                    print(f"{Fore.RED}  ✗ Error: {result['error']}{Style.RESET_ALL}")
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": json.dumps(result)
                })
            
            self.messages.append({"role": "user", "content": tool_results})
            self._log_tool_results(tool_results)
            
        elif incomplete_turn["type"] != "continue":
            print(f"{Fore.RED}Unknown incomplete turn type: {incomplete_turn['type']}{Style.RESET_ALL}")
            return None
        
        # Ensure thinking blocks are valid
        if self.think:
            self._ensure_thinking_blocks()
        
        # Continue with agent loop (pass None to skip first chat call)
        return self._agent_loop(None, max_turns)

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
