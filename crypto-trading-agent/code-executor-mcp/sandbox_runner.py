"""
Sandbox Runner Module

Thin wrapper over the k8s-agent-sandbox SDK for managing gVisor sandbox lifecycle.
Handles sandbox creation from warm pools, file injection, command execution,
output retrieval, and cleanup with TTL backstops.
"""

import json
import logging
import os

from k8s_agent_sandbox import SandboxClient
from k8s_agent_sandbox.models import SandboxInClusterConnectionConfig

logger = logging.getLogger(__name__)

# Configuration
WARMPOOL_NAME = os.environ.get("SANDBOX_WARMPOOL", "trading-python-pool")
SANDBOX_NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "trading")
SANDBOX_TTL_SECONDS = int(os.environ.get("SANDBOX_TTL_SECONDS", "300"))
MAX_OUTPUT_BYTES = int(os.environ.get("MAX_OUTPUT_BYTES", "65536"))  # 64KB cap

# Initialize the sandbox client for in-cluster usage
_client = SandboxClient(
    connection_config=SandboxInClusterConnectionConfig(server_port=8888),
    cleanup=True,
)


def cap_output(output: str) -> str:
    """Cap stdout/stderr output to prevent excessive memory usage."""
    if not output:
        return ""
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        truncated = output.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        return truncated + "\n... [output truncated at 64KB]"
    return output


def run_in_sandbox(code: str, input_data) -> dict:
    """
    Execute Python code in an isolated gVisor sandbox.

    1. Claims a warm sandbox from the pool
    2. Injects input_data.json and main.py
    3. Executes python3 /app/main.py
    4. Returns stdout/stderr/exit_code
    5. Cleans up the sandbox

    Args:
        code: Python source code to execute.
        input_data: Data to serialize as JSON and inject into the sandbox.

    Returns:
        Dict with stdout, stderr, and exit_code.
    """
    sandbox = None
    try:
        # 1. Claim a warm sandbox
        sandbox = _client.create_sandbox(
            template="trading-python",
            warmpool=WARMPOOL_NAME,
            namespace=SANDBOX_NAMESPACE,
            shutdown_after_seconds=SANDBOX_TTL_SECONDS,
        )
        logger.info(f"Sandbox claimed: {sandbox.claim_name}, warmpool={WARMPOOL_NAME}")

        # 2. Write data and code, then execute - all in one Python invocation
        data_json = json.dumps(input_data, default=str)
        import base64

        # Replace /app/input_data.json path with writable location
        adjusted_code = code.replace("/app/input_data.json", "/tmp/sandbox/input_data.json")

        code_b64 = base64.b64encode(adjusted_code.encode()).decode()
        data_b64 = base64.b64encode(data_json.encode()).decode()

        # Build a bootstrap script that writes files then runs user code
        bootstrap = (
            "import base64, os\n"
            "os.makedirs('/tmp/sandbox', exist_ok=True)\n"
            f"open('/tmp/sandbox/input_data.json','w').write(base64.b64decode('{data_b64}').decode())\n"
            f"exec(compile(base64.b64decode('{code_b64}').decode(), '/tmp/sandbox/main.py', 'exec'))\n"
        )
        import base64 as b64mod
        bootstrap_b64 = b64mod.b64encode(bootstrap.encode()).decode()

        logger.info("Executing code in sandbox")

        # 3. Execute via the sandbox - use python3 with base64 decoded script
        cmd = ["python3", "-c", f"import base64;exec(base64.b64decode('{bootstrap_b64}').decode())"]
        result = sandbox.commands.run(cmd)

        stdout = cap_output(result.stdout or "")
        stderr = cap_output(result.stderr or "")

        logger.info(f"Execution complete: exit_code={result.exit_code}")

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.exit_code,
        }

    except Exception as e:
        logger.error(f"Sandbox execution failed: {e}")
        return {
            "stdout": "",
            "stderr": f"Sandbox execution error: {str(e)}",
            "exit_code": 1,
        }

    finally:
        # 4. Cleanup — TTL backstop handles it if this fails
        if sandbox:
            try:
                _client.delete_sandbox(sandbox.claim_name, namespace=SANDBOX_NAMESPACE)
                logger.info(f"Sandbox {sandbox.claim_name} deleted")
            except Exception as e:
                logger.warning(
                    f"Failed to delete sandbox {sandbox.claim_name}: {e}. "
                    f"TTL backstop ({SANDBOX_TTL_SECONDS}s) will clean up."
                )
