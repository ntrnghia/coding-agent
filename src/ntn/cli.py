#!/usr/bin/env python3
"""NTN CLI - Minimal AI coding agent"""
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
from .config import config, get_color
from .tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool, get_tool_description
from .agent import CodingAgent, print_divider

# Initialize colorama
init(autoreset=True)


def create_key_bindings():
    """Create key bindings.

    Notes:
        Most terminals (including VS Code integrated terminal and Windows Terminal
        on Windows/PowerShell) do not send a distinct key event for Shift+Enter.

        To support Shift+Enter for newline, we bind "Esc, Enter" to insert a
        newline and recommend configuring the terminal to translate Shift+Enter
        into the escape sequence: ESC + CR ("\u001b\r").

        See README for suggested keybinding snippets.
    """
    bindings = KeyBindings()

    @bindings.add('enter')
    def handle_enter(event):
        """Submit on Enter."""
        event.current_buffer.validate_and_handle()

    @bindings.add('escape', 'enter')
    def handle_escape_enter(event):
        """Insert newline on Esc+Enter.

        Terminals can be configured to translate Shift+Enter into this sequence.
        """
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
    """Parse debug file with incremental format to extract messages, display history, and session cost
    
    New format uses:
    - --- USER --- : User input (first one is text, subsequent are in TOOL_RESULT)
    - --- ASSISTANT --- : Assistant response (JSON array)
    - --- REQ_COST: X.XXXXXX --- : Cost of the request
    - --- TOOL_RESULT --- : Tool execution results
    - --- END_TURN --- : Marks turn completion
    
    Returns:
        tuple: (messages, display_history, incomplete_turn, session_cost) 
               incomplete_turn is the pending input to continue from, or None if complete
               session_cost is calculated from USAGE tokens or legacy REQ_COST values
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    messages = []
    display_history = []
    incomplete_turn = None
    session_cost = 0.0

    # Parse USAGE lines and calculate cost (model embedded in each usage record)
    for usage_str in re.findall(r'--- USAGE: ({.*?}) ---', content):
        try:
            usage_data = json.loads(usage_str)
            # Get model shorthand from usage record, resolve to full model ID
            model_short = usage_data.pop("model", None)
            if model_short:
                model = config.models.get_model_id(model_short)
            else:
                # Fallback: parse from session header (legacy format)
                model_match = re.search(r'Model: ([\w.-]+)', content)
                model = model_match.group(1) if model_match else "gpt-5.2"
            session_cost += CodingAgent.calculate_cost_from_usage(usage_data, model)
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: Parse legacy REQ_COST lines (old format)
    if session_cost == 0.0:
        for cost_str in re.findall(r'--- REQ_COST: ([\d.]+) ---', content):
            try:
                session_cost += float(cost_str)
            except ValueError:
                pass

    # Find all compaction events with their positions and info
    # Stop at TURN, RESUME, USAGE, or end of file
    compaction_pattern = r'=== COMPACTION EVENT ===\nReason: .*?\nRemoved turns: (\d+)-(\d+)\nSummary content:\n(.*?)(?=\n--- USAGE:|\n=== TURN|\n=== RESUME|\Z)'
    compaction_matches = list(re.finditer(compaction_pattern, content, re.DOTALL))
    latest_compaction = compaction_matches[-1] if compaction_matches else None
    latest_compaction_pos = latest_compaction.start() if latest_compaction else -1

    # Find all turn start positions
    turn_pattern = r'\n=== TURN (\d+) ===\n'
    turn_matches = list(re.finditer(turn_pattern, content))

    # If there was compaction, initialize messages with summary
    if latest_compaction:
        summary_text = latest_compaction.group(3).strip()
        messages = [
            {"role": "user", "content": summary_text},
            {"role": "assistant", "content": [{"type": "text", "text": "I understand the context and will continue helping with your request."}]}
        ]

    # Track which compactions we've added to display
    compactions_added = set()
    last_pos = 0

    # Process turns and insert compaction events at the right positions
    for i, turn_match in enumerate(turn_matches):
        turn_start = turn_match.start()

        # Check if any compaction events occurred before this turn
        for comp_idx, comp in enumerate(compaction_matches):
            if comp_idx not in compactions_added and last_pos <= comp.start() < turn_start:
                # Add compaction to display_history
                removed_start, removed_end = comp.group(1), comp.group(2)
                summary = comp.group(3).strip()
                display_history.append(("system", f"📦 Context compacted - turns {removed_start}-{removed_end} summarized"))
                if config.ui.show_compact_content:
                    display_history.append(("system", summary))
                compactions_added.add(comp_idx)

        # Find turn content (until next turn or end)
        turn_end = turn_matches[i + 1].start() if i + 1 < len(turn_matches) else len(content)
        turn_content = content[turn_match.end():turn_end]
        turn_complete = '--- END_TURN ---' in turn_content

        # Parse all markers in order (USER, ASSISTANT, TOOL_RESULT, DROP_TURN)
        # This ensures DROP_TURN appears in the correct position in display_history
        all_markers_pattern = r'--- (USER|ASSISTANT|TOOL_RESULT|DROP_TURN) ---'
        marker_matches = list(re.finditer(all_markers_pattern, turn_content))

        # Parse full block content for USER/ASSISTANT/TOOL_RESULT
        full_block_pattern = r'--- (USER|ASSISTANT|TOOL_RESULT) ---\n(.*?)(?=\n--- |\n=== |$)'
        full_block_matches = list(re.finditer(full_block_pattern, turn_content, re.DOTALL))

        last_assistant_content = None
        last_tool_results = None

        # Check if this turn is after the latest compaction (for messages)
        include_in_messages = turn_start > latest_compaction_pos

        # Build a map of position -> full block data
        block_data_map = {}
        for match in full_block_matches:
            block_data_map[match.start()] = (match.group(1), match.group(2).strip())

        # Process markers in order
        for marker_match in marker_matches:
            marker_type = marker_match.group(1)
            marker_pos = marker_match.start()

            # Handle DROP_TURN indicator
            if marker_type == "DROP_TURN":
                if config.ui.show_drop_indicator:
                    display_history.append(("system", "📦 Compacting..."))
                continue

            # Get full block data for this marker
            if marker_pos not in block_data_map:
                continue
            block_type, block_data = block_data_map[marker_pos]
            block_data = block_data.strip()

            if block_type == "USER":
                # Add to display_history always
                display_history.append(("user", block_data))
                # Add to messages only if after compaction
                if include_in_messages:
                    messages.append({"role": "user", "content": block_data})

            elif block_type == "ASSISTANT":
                try:
                    assistant_content = json.loads(block_data)
                    last_assistant_content = assistant_content

                    # Add to messages only if after compaction
                    if include_in_messages:
                        messages.append({"role": "assistant", "content": assistant_content})

                    # Extract thinking indicator for display (always)
                    for b in assistant_content:
                        if b.get("type") == "thinking":
                            display_history.append(("thinking", "🧠 Thinking..."))

                    # Extract text for display (always)
                    texts = [b["text"] for b in assistant_content if b.get("type") == "text"]
                    if texts:
                        display_history.append(("assistant", "\n".join(texts)))

                    # Extract tool descriptions for display (always)
                    for b in assistant_content:
                        if b.get("type") == "tool_use":
                            desc = get_tool_description(b.get("name", ""), b.get("input", {}))
                            display_history.append(("tool", desc))
                except json.JSONDecodeError:
                    pass

            elif block_type == "TOOL_RESULT":
                try:
                    tool_results = json.loads(block_data)
                    last_tool_results = tool_results
                    # Add to messages only if after compaction
                    if include_in_messages:
                        messages.append({"role": "user", "content": tool_results})
                except json.JSONDecodeError:
                    pass

        # Check if turn is incomplete (only matters for last turn)
        if not turn_complete and i == len(turn_matches) - 1:
            if last_tool_results is not None:
                incomplete_turn = {"type": "continue"}
            elif last_assistant_content is not None:
                tool_uses = [b for b in last_assistant_content if b.get("type") == "tool_use"]
                if tool_uses:
                    incomplete_turn = {"type": "execute_tools", "tool_uses": tool_uses}
            elif messages and messages[-1].get("role") == "user":
                incomplete_turn = {"type": "continue"}

        last_pos = turn_end

    # Add any remaining compaction events that occurred after the last turn
    for comp_idx, comp in enumerate(compaction_matches):
        if comp_idx not in compactions_added:
            summary = comp.group(3).strip()
            display_history.append(("system", "📦 Compacting..."))
            if config.ui.show_compact_content:
                display_history.append(("system", summary))
            compactions_added.add(comp_idx)

    return messages, display_history, incomplete_turn, session_cost


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
    prefixes = config.ui.prefixes
    role_formats = {
        "user": (get_color("user"), prefixes.user),
        "thinking": (get_color("thinking"), ""),
        "assistant": (get_color("assistant"), prefixes.assistant),
        "tool": (get_color("tool"), ""),
        "system": (get_color("system"), "")
    }
    for role, content in display_history:
        color, prefix = role_formats.get(role, (Style.RESET_ALL, ""))
        print(f"{color}{prefix + ' ' if prefix else ''}{content}{Style.RESET_ALL}")
        if role == "user" or role == "assistant":
            print(); print_divider(); print()


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
        '-m', '--model',
        choices=['gpt', 'opus', 'sonnet', 'haiku'],
        default='gpt',
        help='Model to use: gpt (default), opus, sonnet, or haiku'
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    # Check for API key based on model
    if args.model == 'gpt':
        if not os.getenv("OPENAI_API_KEY"):
            print(f"{Fore.RED}ERROR: OPENAI_API_KEY environment variable not set!")
            print(f"{Fore.YELLOW}Set it with: export OPENAI_API_KEY='your-key-here'")
            print(f"{Fore.YELLOW}Or create a .env file with: OPENAI_API_KEY=your-key-here")
            return
    else:
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
    session_cost = 0.0
    
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
        initial_messages, display_history, incomplete_turn, session_cost = parse_debug_file(resume_file)
        container_info = parse_container_info(resume_file)
        
        if display_history:
            print(f"{Fore.CYAN}--- Previous conversation ---{Style.RESET_ALL}\n")
            replay_display_history(display_history)
            # Only add divider if replay_display_history didn't already add one
            # (it adds divider after "user" and "assistant" entries)
            last_role = display_history[-1][0] if display_history else None
            if last_role not in ("user", "assistant"):
                print()
                print_divider()
                print()
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
        stream=True,  # Always stream for real-time output
        think=args.think,
        model=args.model
    )
    
    # Wire up DockerSandboxTool with agent reference
    docker_sandbox.set_agent_ref(agent)
    
    # If resuming, initialize agent with previous state
    if initial_messages:
        agent.messages = initial_messages
    if display_history:
        agent.display_history = display_history
    if args.resume and session_cost > 0:
        agent.session_cost = session_cost

    # Create key bindings and style
    bindings = create_key_bindings()
    prompt_style = PromptStyle.from_dict({
        'prompt': 'ansicyan bold',
    })

    # Interactive loop
    if not args.resume:
        print(f"{Fore.GREEN}Coding Agent initialized in: {workspace}")
    print(f"{Fore.CYAN}Debug logs: {agent.debug_file}")
    # print(f"{Fore.CYAN}Container: {agent.container_manager.container_name}")
    
    # Show enabled features
    model_display = f"{args.model} (gpt-5.2)" if args.model == 'gpt' else args.model
    features = [f"model: {model_display}"]
    if args.think:
        features.append("extended thinking")
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
