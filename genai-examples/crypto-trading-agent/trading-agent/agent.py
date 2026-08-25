"""
Strands Trading Agent - Crypto trading assistant powered by Strands Agents SDK.
"""

import asyncio
import os

from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

SYSTEM_PROMPT = (
    "You are a crypto trading assistant. You help traders analyze their portfolio, "
    "calculate risk metrics, identify trading opportunities, and run quantitative analysis.\n\n"
    "When calculations are needed:\n"
    "1. First retrieve the relevant data using trading-mcp-server_get_market_data/trading-mcp-server_get_positions/trading-mcp-server_get_order_history\n"
    "2. Write Python code using pandas/numpy/scipy to perform the calculation\n"
    "3. Pass the retrieved data as input_data and execute using code-executor-mcp_run_calculation tool\n"
    "4. Explain the results clearly\n\n"
    "Your code should read input data from /app/input_data.json using:\n"
    "  import json\n"
    "  with open('/app/input_data.json') as f:\n"
    "      data = json.load(f)\n\n"
    "IMPORTANT: Always execute calculations using the code-executor-mcp_run_calculation tool. "
    "Do NOT compute results inline — you MUST run code in the sandbox.\n"
    "Never execute code that makes network calls or modifies the filesystem outside /tmp."
)

LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://localhost:8080/v1")
MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT", "http://localhost:8081/mcp")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")


def create_agent(authorization_token: str | None = None):
    """Create agent and MCP client."""
    mcp_headers = {}
    if authorization_token:
        mcp_headers["Authorization"] = f"Bearer {authorization_token}"

    mcp_client = MCPClient(
        lambda: streamablehttp_client(MCP_ENDPOINT, headers=mcp_headers),
    )

    with mcp_client:
        tools = mcp_client.list_tools_sync()

        model = OpenAIModel(
            model_id=MODEL_ID,
            client_args={
                "base_url": LLM_ENDPOINT,
                "api_key": "not-needed",
            },
            params={"max_tokens": 16384},
        )

        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
        )

        return agent, mcp_client


def run_agent_streaming(question, st, agent, mcp_client):
    """Run agent with streaming output."""
    message_placeholder = st.empty()
    full_response = ""

    async def process_streaming_response():
        nonlocal full_response

        try:
            with mcp_client:
                try:
                    agent_stream = agent.stream_async(question)
                    async for event in agent_stream:
                        if "data" in event:
                            full_response += event["data"]
                            message_placeholder.markdown(full_response)
                except Exception as e:
                    print(f"Error processing request: {e}")
        except Exception as e:
            print(f"Error with MCP client: {e}")
            if not full_response:
                message_placeholder.markdown("Sorry, an error occurred.")
                full_response = "Sorry, an error occurred."

    asyncio.run(process_streaming_response())

    return full_response
