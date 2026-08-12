"""
Code Executor MCP Broker — manages gVisor sandbox lifecycle for secure code execution.

Accepts code and input data from the agent, injects them into an air-gapped
gVisor sandbox, executes the code, and returns results.
"""

import json
import logging
import os

from mcp.server.fastmcp import FastMCP
from sandbox_runner import run_in_sandbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Code Executor", host="0.0.0.0")


@mcp.tool(
    description="Execute Python code in a secure gVisor sandbox. "
    "Pass the input data (e.g., market prices, positions) as a JSON object — "
    "it will be available in the sandbox at /app/input_data.json. "
    "Write your code to read from that file. Use pandas/numpy/scipy/matplotlib as needed. "
    "To generate a chart, save it to /app/chart.png."
)
def run_calculation(code: str, input_data: dict) -> dict:
    """Execute Python code in an isolated gVisor sandbox.

    Args:
        code: Python code to execute. Input data is available at /app/input_data.json.
        input_data: JSON-serializable data to inject into the sandbox.

    Returns:
        Dict with stdout, stderr, and exit_code.
    """
    if not code.strip():
        return {
            "stdout": "",
            "stderr": "No code provided",
            "exit_code": 1,
        }

    logger.info(f"Running calculation, input_data keys: {list(input_data.keys()) if isinstance(input_data, dict) else 'non-dict'}")
    result = run_in_sandbox(code, input_data)
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
