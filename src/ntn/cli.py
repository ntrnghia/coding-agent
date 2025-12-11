#!/usr/bin/env python3
"""NTN CLI - Minimal AI coding agent powered by Claude"""
import os
import sys
import argparse
import glob
import json
import re
import time
from colorama import Fore, Style, init
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from .tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool, get_tool_description
from .agent import CodingAgent, print_divider

# Initialize colorama
init(autoreset=True)


def create_key_bindings():
    """Create key bindings: Enter=submit, Shift+Enter=newline (shows \\)"""
    bindings = KeyBindings()
    
    # Track backslash timing for Shift+Enter detection
    state = {'last_backslash_time': 0}
    SHIFT_ENTER_THRESHOLD = 0.05  # 50ms - if Enter comes within this time after \, it's Shift+Enter

    @bindings.add('enter')
    def handle_enter(event):
        """Submit on Enter, or newline if it immediately follows backslash (Shift+Enter)"""
        time_since_backslash = time.time() - state['last_backslash_time']
        if time_since_backslash < SHIFT_ENTER_THRESHOLD:
            # This Enter is part of Shift+Enter sequence - insert newline
            # The backslash was already inserted, so just add the newline
            event.current_buffer.insert_text('\n')
        else:
            # Regular Enter - submit
            event.current_buffer.validate_and_handle()

    @bindings.add('\\')
    def handle_backslash(event):
        """Insert backslash and record time (for Shift+Enter detection)"""
        state['last_backslash_time'] = time.time()
        event.current_buffer.insert_text('\\')

    @bindings.add('escape', 'enter')
    def handle_alt_enter(event):
        """Insert newline on Alt+Enter (fallback)"""
        event.current_buffer.insert_text('\n')

    @bindings.add('c-j')
    def handle_ctrl_j(event):
        """Insert newline on Ctrl+J (fallback)"""
        event.current_buffer.insert_text('\n')

    return bindings


def find_latest_debug_file(workspace):
    """Find the most recent debug file in the workspace"""
    debug_dir = os.path.join(workspace, "debug")
    pattern = os.path.join(debug_dir, "debug_*.txt")
    files = glob.glob(pattern)
    
    if not files:
        return None
    
    # Sort by modification time, get most recent
    return max(files, key=os.path.getmtime)


def parse_debug_file(filepath):
    """Parse debug file with incremental format to extract messages and display history
    
    New format uses:
    - --- USER --- : User input (first one is text, subsequent are in TOOL_RESULT)
    - --- ASSISTANT --- : Assistant response (JSON array)
    - --- TOOL_RESULT --- : Tool execution results
    - --- END_TURN --- : Marks turn completion
    
    Returns:
        tuple: (messages, display_history, incomplete_turn) 
               incomplete_turn is the pending input to continue from, or None if complete
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    messages = []
    display_history = []
    incomplete_turn = None
    
    # Split by turn markers
    turn_splits = re.split(r'\n=== TURN \d+ ===\n', content)
    
    for turn_content in turn_splits[1:]:  # Skip header before first turn
        turn_complete = '--- END_TURN ---' in turn_content
        
        # Find all blocks using findall to get ordered list of (type, content)
        # Match: --- TYPE ---\n followed by content until next --- or === or end
        block_pattern = r'--- (USER|ASSISTANT|TOOL_RESULT) ---\n(.*?)(?=\n--- |\n=== |$)'
        block_matches = re.findall(block_pattern, turn_content, re.DOTALL)
        
        last_assistant_content = None
        last_tool_results = None
        
        for block_type, block_data in block_matches:
            block_data = block_data.strip()
            
            if block_type == "USER":
                # First USER in a turn is the actual user input (plain text)
                messages.append({"role": "user", "content": block_data})
                display_history.append(("user", block_data))
                    
            elif block_type == "ASSISTANT":
                try:
                    assistant_content = json.loads(block_data)
                    messages.append({"role": "assistant", "content": assistant_content})
                    last_assistant_content = assistant_content
                    
                    # Extract thinking indicator for display
                    for b in assistant_content:
                        if b.get("type") == "thinking":
                            display_history.append(("thinking", "🧠 Thinking..."))
                    
                    # Extract text for display
                    texts = [b["text"] for b in assistant_content if b.get("type") == "text"]
                    if texts:
                        display_history.append(("assistant", "\n".join(texts)))
                    
                    # Extract tool descriptions for display
                    for b in assistant_content:
                        if b.get("type") == "tool_use":
                            desc = get_tool_description(b.get("name", ""), b.get("input", {}))
                            display_history.append(("tool", desc))
                except json.JSONDecodeError:
                    pass
                    
            elif block_type == "TOOL_RESULT":
                try:
                    tool_results = json.loads(block_data)
                    messages.append({"role": "user", "content": tool_results})
                    last_tool_results = tool_results
                except json.JSONDecodeError:
                    pass
        
        # Check if turn is incomplete
        if not turn_complete:
            # Determine what to resume with based on last block
            if last_tool_results is not None:
                # Crashed after tool execution - messages already has tool_results
                # Just need to call API to continue generation
                incomplete_turn = {"type": "continue"}
            elif last_assistant_content is not None:
                # Crashed after assistant response with tool_use but before tools ran
                tool_uses = [b for b in last_assistant_content if b.get("type") == "tool_use"]
                if tool_uses:
                    # Need to execute tools, then continue
                    incomplete_turn = {"type": "execute_tools", "tool_uses": tool_uses}
            elif messages and messages[-1].get("role") == "user":
                # Crashed after user input but before assistant responded
                # Need to call API to get response
                incomplete_turn = {"type": "continue"}
    
    return messages, display_history, incomplete_turn


def parse_container_info(filepath):
    """Parse container info from debug file
    
    Returns:
        dict: Container info with container_name and working_dirs, or None if not found
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find all CONTAINER INFO blocks - capture only the next line (the JSON)
    matches = list(re.finditer(r'=== CONTAINER INFO ===\n(.+)', content))
    
    if matches:
        try:
            return json.loads(matches[-1].group(1).strip())
        except json.JSONDecodeError:
            pass
    
    return None


def replay_display_history(display_history):
    """Print the conversation history for resume"""
    role_formats = {
        "user": (Style.RESET_ALL, "You:"),
        "thinking": (Fore.MAGENTA, ""),
        "assistant": (Fore.GREEN, "Agent:"),
        "tool": (Fore.YELLOW, "")
    }
    is_first_user = True
    for role, content in display_history:
        if role == "user":
            if not is_first_user:
                print(); print_divider(); print()
            is_first_user = False
        color, prefix = role_formats.get(role, (Style.RESET_ALL, ""))
        print(f"{color}{prefix + ' ' if prefix else ''}{content}{Style.RESET_ALL}")
        # Add divider after user message
        if role == "user":
            print(); print_divider(); print()
    print()


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='AI Coding Agent')
    parser.add_argument(
        '-r', '--resume',
        nargs='?',
        const='LATEST',
        metavar='DEBUG_FILE',
        help='Resume from a debug file. If no file specified, uses the most recent.'
    )
    parser.add_argument(
        '-t', '--think',
        action='store_true',
        help='Enable extended thinking for complex reasoning tasks'
    )
    parser.add_argument(
        '-s', '--stream',
        action='store_true',
        help='Enable streaming output for real-time response display'
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(f"{Fore.RED}ERROR: ANTHROPIC_API_KEY environment variable not set!")
        print(f"{Fore.YELLOW}Set it with: export ANTHROPIC_API_KEY='your-key-here'")
        print(f"{Fore.YELLOW}Or create a .env file with: ANTHROPIC_API_KEY=your-key-here")
        return

    # Set workspace directory
    workspace = os.getcwd()

    # Handle resume
    initial_messages = None
    display_history = None
    resume_file = None
    container_info = None
    incomplete_turn = None
    
    if args.resume:
        if args.resume == 'LATEST':
            resume_file = find_latest_debug_file(workspace)
            if not resume_file:
                print(f"{Fore.RED}No debug files found in {workspace}/debug/")
                return
        else:
            resume_file = args.resume
            if not os.path.exists(resume_file):
                print(f"{Fore.RED}Debug file not found: {resume_file}")
                return
        
        print(f"{Fore.CYAN}Resuming from: {resume_file}")
        initial_messages, display_history, incomplete_turn = parse_debug_file(resume_file)
        container_info = parse_container_info(resume_file)
        
        if display_history:
            print(f"{Fore.CYAN}--- Previous conversation ---{Style.RESET_ALL}\n")
            replay_display_history(display_history)
            print(f"{Fore.CYAN}--- Resuming conversation ---{Style.RESET_ALL}\n")
        else:
            print(f"{Fore.YELLOW}No conversation history found in debug file")
        
        if incomplete_turn:
            print(f"{Fore.YELLOW}⚠️  Detected incomplete turn - will continue from crash point{Style.RESET_ALL}")

    # Initialize tools
    terminal = TerminalTool(workspace)
    web_search = WebSearchTool()
    fetch_web = FetchWebTool()
    docker_sandbox = DockerSandboxTool()  # Agent ref will be set after agent creation

    # Create agent (pass debug_file if resuming to append to same file)
    agent = CodingAgent(
        tools=[terminal, web_search, fetch_web, docker_sandbox],
        workspace_dir=workspace,
        debug_file=resume_file,  # None for new session, path for resume
        container_info=container_info,  # None for new session, dict for resume
        stream=args.stream,
        think=args.think
    )
    
    # Wire up DockerSandboxTool with agent reference
    docker_sandbox.set_agent_ref(agent)
    
    # If resuming, initialize agent with previous state
    if initial_messages:
        agent.messages = initial_messages
    if display_history:
        agent.display_history = display_history

    # Create key bindings and style
    bindings = create_key_bindings()
    prompt_style = PromptStyle.from_dict({
        'prompt': 'ansicyan bold',
    })

    # Interactive loop
    if not args.resume:
        print(f"{Fore.GREEN}Coding Agent initialized in: {workspace}")
    print(f"{Fore.CYAN}Debug logs: {agent.debug_file}")
    print(f"{Fore.CYAN}Container: {agent.container_manager.container_name}")
    
    # Show enabled features
    features = []
    if args.stream:
        features.append("streaming")
    if args.think:
        features.append("extended thinking")
    if features:
        print(f"{Fore.CYAN}Features: {', '.join(features)}")
    
    print(f"{Fore.CYAN}Shift+Enter for new line | Enter to submit | Ctrl+C to exit")

    # If resuming with incomplete turn, continue it first
    if incomplete_turn:
        try:
            print(f"{Fore.YELLOW}Continuing interrupted turn...{Style.RESET_ALL}")
            agent.continue_incomplete_turn(incomplete_turn)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Interrupted by user")
            agent._log_end_turn()
        except Exception as e:
            print(f"{Fore.RED}Error continuing turn: {e}{Style.RESET_ALL}")

    try:
        while True:
            # Print status before user input (skip on first run)
            agent.print_status()
            
            try:
                user_input = prompt(
                    [('class:prompt', 'You: ')],
                    style=prompt_style,
                    key_bindings=bindings,
                    multiline=True,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{Fore.GREEN}Goodbye!")
                break

            if not user_input:
                continue

            print()
            print_divider()
            print()

            try:
                agent.run(user_input)
            except KeyboardInterrupt:
                print(f"\n{Fore.YELLOW}Interrupted by user")
                agent._log_end_turn()  # Mark turn as intentionally stopped
            except Exception as e:
                print(f"{Fore.RED}Error: {e}")
                import traceback
                traceback.print_exc()
    finally:
        # Cleanup: check if session was empty
        if agent.cleanup_empty_session():
            pass  # Cleanup message already printed
        else:
            # Stop container gracefully
            agent.stop_container()

if __name__ == "__main__":
    main()
