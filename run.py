#!/usr/bin/env python3
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
from tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool
from agent import CodingAgent

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
    """Parse debug file to extract messages and display history
    
    Returns:
        tuple: (messages, display_history) for resuming conversation
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    messages = []
    display_history = []
    
    # Split by turn markers
    turns = re.split(r'\n=== TURN \d+ ===\n', content)
    
    for turn in turns[1:]:  # Skip header before first turn
        # Extract user input
        user_match = re.search(r'--- USER INPUT ---\n(.*?)\n--- API MESSAGES ---', turn, re.DOTALL)
        if user_match:
            user_input = user_match.group(1).strip()
            display_history.append(("user", user_input))
        
        # Extract API messages
        api_match = re.search(r'--- API MESSAGES ---\n(.*?)\n--- API RESPONSE ---', turn, re.DOTALL)
        if api_match:
            try:
                messages = json.loads(api_match.group(1).strip())
            except json.JSONDecodeError:
                pass  # Keep previous messages if parsing fails
        
        # Extract agent response for display
        response_match = re.search(r'--- API RESPONSE ---\n(.*?)(?:\n--- TOOL CALLS ---|$)', turn, re.DOTALL)
        if response_match:
            try:
                response_data = json.loads(response_match.group(1).strip())
                if response_data.get("content"):
                    agent_text = "\n".join(response_data["content"]) if isinstance(response_data["content"], list) else str(response_data["content"])
                    display_history.append(("assistant", agent_text))
            except json.JSONDecodeError:
                pass
    
    # Also check for compaction events and update messages
    compaction_match = re.search(r'=== COMPACTION EVENT ===.*?Summary content:\n(.*?)(?:\n===|$)', content, re.DOTALL)
    if compaction_match:
        # If there was a compaction, the messages array should reflect the summarized state
        # The last API MESSAGES block should have this
        pass  # Messages already extracted from last turn
    
    return messages, display_history


def replay_display_history(display_history):
    """Print the conversation history for resume"""
    for role, content in display_history:
        if role == "user":
            print(f"{Fore.CYAN}You:{Style.RESET_ALL} {content}")
        elif role == "assistant":
            print(f"{Fore.GREEN}Agent:{Style.RESET_ALL} {content}")
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
        initial_messages, display_history = parse_debug_file(resume_file)
        
        if display_history:
            print(f"{Fore.CYAN}--- Previous conversation ---{Style.RESET_ALL}\n")
            replay_display_history(display_history)
            print(f"{Fore.CYAN}--- Resuming conversation ---{Style.RESET_ALL}\n")
        else:
            print(f"{Fore.YELLOW}No conversation history found in debug file")

    # Initialize tools
    terminal = TerminalTool(workspace)
    web_search = WebSearchTool()
    fetch_web = FetchWebTool()
    docker_sandbox = DockerSandboxTool()

    # Create agent (pass debug_file if resuming to append to same file)
    agent = CodingAgent(
        tools=[terminal, web_search, fetch_web, docker_sandbox],
        workspace_dir=workspace,
        debug_file=resume_file  # None for new session, path for resume
    )
    
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
    print(f"{Fore.CYAN}Shift+Enter for new line | Enter to submit | Type 'exit' to quit\n")

    while True:
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

        if user_input.lower() == 'exit':
            print(f"{Fore.GREEN}Goodbye!")
            break

        if not user_input:
            continue

        try:
            agent.run(user_input)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Interrupted by user")
        except Exception as e:
            print(f"{Fore.RED}Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()
