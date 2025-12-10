import anthropic
import json
import os

class CodingAgent:
    """Minimal AI agent for coding workspace"""

    def __init__(self, tools, workspace_dir):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-opus-4-5"
        self.system_message = f"""You are an AI coding assistant with access to a workspace at {workspace_dir}.
You can execute shell commands, search the web, and fetch documentation.
Always verify your actions and explain what you're doing."""

        self.messages = []
        self.tools = tools
        self.tool_map = {tool.get_schema()["name"]: tool for tool in tools}

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

    def run(self, user_input, max_turns=10):
        """Main agent loop"""
        current_input = user_input

        for i in range(max_turns):
            print(f"\n{'='*60}")
            print(f"Turn {i+1}")
            print(f"{'='*60}")

            response = self.chat(current_input)

            # Print text responses
            for block in response.content:
                if hasattr(block, 'text'):
                    print(f"\nAgent: {block.text}")

            # Handle tool use
            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        print(f"\n[Using tool: {tool_name}]")
                        print(f"Input: {json.dumps(tool_input, indent=2)}")

                        # Execute tool
                        tool = self.tool_map[tool_name]
                        result = tool.execute(**tool_input)

                        print(f"Output: {json.dumps(result, indent=2)[:500]}...")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })

                current_input = tool_results
            else:
                # Agent finished
                return response

        print("\nMax turns reached")
        return response
