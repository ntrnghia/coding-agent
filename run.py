#!/usr/bin/env python3
import os
from colorama import Fore, Style, init
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool
from agent import CodingAgent

# Initialize colorama
init(autoreset=True)


import time


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


def main():
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(f"{Fore.RED}ERROR: ANTHROPIC_API_KEY environment variable not set!")
        print(f"{Fore.YELLOW}Set it with: export ANTHROPIC_API_KEY='your-key-here'")
        print(f"{Fore.YELLOW}Or create a .env file with: ANTHROPIC_API_KEY=your-key-here")
        return

    # Set workspace directory
    workspace = os.getcwd()  # or specify custom path

    # Initialize tools
    terminal = TerminalTool(workspace)
    web_search = WebSearchTool()
    fetch_web = FetchWebTool()
    docker_sandbox = DockerSandboxTool()

    # Create agent
    agent = CodingAgent(
        tools=[terminal, web_search, fetch_web, docker_sandbox],
        workspace_dir=workspace
    )

    # Create key bindings and style
    bindings = create_key_bindings()
    prompt_style = PromptStyle.from_dict({
        'prompt': 'ansicyan bold',
    })

    # Interactive loop
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
