#!/usr/bin/env python3
import os
from colorama import Fore, Style, init
from tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool
from agent import CodingAgent

# Initialize colorama
init(autoreset=True)


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

    # Interactive loop
    print(f"{Fore.GREEN}Coding Agent initialized in: {workspace}")
    print(f"{Fore.CYAN}Type 'exit' to quit")
    print(f"{Fore.CYAN}Debug logs: {agent.debug_file}\n")

    while True:
        user_input = input(f"{Fore.CYAN}You: {Style.RESET_ALL}").strip()

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
