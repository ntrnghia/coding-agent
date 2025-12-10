#!/usr/bin/env python3
import os
from tools import TerminalTool, WebSearchTool, FetchWebTool
from agent import CodingAgent

def main():
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set!")
        print("Set it with: export ANTHROPIC_API_KEY='your-key-here'")
        print("Or create a .env file with: ANTHROPIC_API_KEY=your-key-here")
        return

    # Set workspace directory
    workspace = os.getcwd()  # or specify custom path

    # Initialize tools
    terminal = TerminalTool(workspace)
    web_search = WebSearchTool()
    fetch_web = FetchWebTool()

    # Create agent
    agent = CodingAgent(
        tools=[terminal, web_search, fetch_web],
        workspace_dir=workspace
    )

    # Interactive loop
    print(f"Coding Agent initialized in: {workspace}")
    print("Type 'exit' to quit\n")

    while True:
        user_input = input("\nYou: ").strip()

        if user_input.lower() == 'exit':
            break

        if not user_input:
            continue

        try:
            agent.run(user_input)
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()
