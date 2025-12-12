#!/usr/bin/env python3
"""
Test script to verify prompt caching token requirements.

This script verifies that the system prompt + tools meet the minimum
token requirements for prompt caching on different models.

Usage:
    python src/ntn/test_cache.py
"""
import os
import sys

# Add src to path if running directly
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import anthropic
from ntn.config import config
from ntn.tools import TerminalTool, WebSearchTool, FetchWebTool, DockerSandboxTool
from ntn.prompts import get_system_prompt, get_mount_section_text


# Model cache requirements from centralized config
CACHE_REQUIREMENTS = config.models.cache_requirements


def get_test_system_prompt():
    """Get system prompt for testing with sample values."""
    workspace_dir = "D:\\Downloads\\project"
    mount_section = get_mount_section_text(
        container_name="agent_test",
        mount_info="  - D:\\Downloads\\project → /d/downloads/project"
    )
    return get_system_prompt(workspace_dir, mount_section)


def get_tool_schemas():
    """Get tool schemas matching CodingAgent._get_tool_schemas()."""
    tools = [
        TerminalTool("D:\\Downloads\\project"),
        WebSearchTool(),
        FetchWebTool(),
        DockerSandboxTool(),
    ]
    return [tool.get_schema() for tool in tools]

# Overhead constant from centralized config
MESSAGE_OVERHEAD = config.cache.message_overhead


def count_tokens(client, model, system_text, tools=None):
    """Count tokens for the given system prompt and tools.
    
    Uses minimal 'x' message (1 token) to get accurate count.
    """
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    kwargs = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": "x"}]  # Minimal 1-token message
    }
    if tools:
        kwargs["tools"] = tools
    
    response = client.messages.count_tokens(**kwargs)
    return response.input_tokens


def predict_cacheable_tokens(count_tokens_result):
    """Predict how many tokens will be cached.
    
    Formula: cacheable = count_tokens('x') - 324
    Where 324 = 323 (framing overhead) + 1 ('x' token)
    """
    return count_tokens_result - MESSAGE_OVERHEAD


def test_caching(client, model, system_text, tools):
    """Test if caching actually works by making two identical requests."""
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    
    # First request: should create cache
    resp1 = client.messages.create(
        model=model,
        max_tokens=50,
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": "Say hello"}]
    )
    
    # Second request: should hit cache
    resp2 = client.messages.create(
        model=model,
        max_tokens=50,
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": "Say hello"}]
    )
    
    return {
        "cache_creation": resp1.usage.cache_creation_input_tokens,
        "cache_read": resp2.usage.cache_read_input_tokens,
        "caching_works": resp2.usage.cache_read_input_tokens > 0
    }


def main():
    """Run cache verification tests."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set")
        sys.exit(1)
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Get system prompt and tools (now from shared prompts module!)
    system_text = get_test_system_prompt()
    tools = get_tool_schemas()
    
    print("=" * 60)
    print("PROMPT CACHING TOKEN VERIFICATION")
    print("=" * 60)
    
    # Test each model
    for model, min_tokens in CACHE_REQUIREMENTS.items():
        model_short = model.split("-")[1]  # opus, sonnet, haiku
        print(f"\n📊 {model_short.upper()} ({model})")
        print(f"   Minimum for caching: {min_tokens} tokens")
        
        try:
            # Count tokens
            system_only = count_tokens(client, model, system_text)
            with_tools = count_tokens(client, model, system_text, tools)
            
            # Predict cacheable tokens (before sending actual request)
            predicted_cacheable = predict_cacheable_tokens(with_tools)
            
            print(f"   System prompt tokens: {predict_cacheable_tokens(system_only)}")
            print(f"   System + tools tokens: {with_tools} (total with overhead)")
            print(f"   Predicted cacheable: {predicted_cacheable} tokens")
            
            # Check if meets requirement
            meets_requirement = predicted_cacheable >= min_tokens
            status = "✅ MEETS" if meets_requirement else "❌ BELOW"
            print(f"   {status} minimum requirement ({predicted_cacheable} vs {min_tokens})")
            
            # Actually test caching for all models
            if meets_requirement:
                print("   Testing actual cache behavior...")
                result = test_caching(client, model, system_text, tools)
                actual_cached = result['cache_creation'] or result['cache_read']
                
                if result["caching_works"]:
                    accuracy = "✓" if actual_cached == predicted_cacheable else f"(predicted: {predicted_cacheable})"
                    print(f"   ✅ CACHING WORKS! Cached: {actual_cached} tokens {accuracy}")
                else:
                    print(f"   ❌ CACHING NOT WORKING (created: {result['cache_creation']}, read: {result['cache_read']})")
        
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
