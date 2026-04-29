import logging
import asyncio
import os
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
import mlflow

# Load environment variables from .env file
#load_dotenv()

# Configure logging for debug information
logging.getLogger("strands").setLevel(logging.INFO)
logging.basicConfig(
    format="%(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()]
)


def setup_mlflow_tracing():
    """Configure MLflow tracing"""
    mlflow_tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:80")

    try:
        mlflow.set_tracking_uri(mlflow_tracking_uri)
        mlflow.set_experiment("clinical-assistant")
        mlflow.strands.autolog()
        print("✅ MLflow tracing enabled successfully!")
        return True
    except Exception as e:
        print(f"⚠️  Failed to setup MLflow tracing: {e}")
        print("   Continuing without tracing...")
        return None

openai_model = OpenAIModel(
    client_args={
        #"base_url": "http://localhost:4000/v1",
        "base_url": os.environ.get("LITELLM_HOST", "http://litellm-graviton:4000/v1"), 
        "api_key": "your-secret-key"  # Required but can be dummy for local servers
    },
    model_id=os.environ.get("MODEL_ID", "my-model"),  # Model identifier
    params={"temperature": 0.3, "max_tokens": 2048}
)

def create_health_agent():
    """Create the health agent with MCP healthcare data server connection and MLflow tracing"""
    
    mcp_host = os.environ.get("MCP_HOST", "http://healthcare-mcp-server:8000/mcp")
    
    # Setup MLflow tracing
    setup_mlflow_tracing()
    
    # Connect to the MCP healthcare server using streamable HTTP client
    mcp_client = MCPClient(lambda: streamablehttp_client(mcp_host))
    
    # Get tools from the MCP server
    with mcp_client:
        tools = mcp_client.list_tools_sync()
        
        # Create the health agent with MCP tools
        health_agent = Agent(
            model=openai_model,
            tools=tools,
            system_prompt="""You are a Clinical Decision Support AI Assistant with access to patient data through an MCP healthcare data server. You help healthcare professionals with diagnostic reasoning and clinical decision-making.

Your role is to:
- Provide evidence-based clinical insights and differential diagnoses
- Assist with symptom analysis and pattern recognition using patient data
- Offer treatment recommendations based on current medical guidelines and patient history
- Help interpret clinical findings and laboratory results
- Support clinical reasoning with relevant medical knowledge and patient-specific data
- Retrieve and analyze patient histories, lab results, and demographic information

Available Healthcare Data Tools (via MCP server at http://localhost:8000):
- get_patient_info: Retrieve patient demographics (use patient IDs: PAT001, PAT002, PAT003)
- get_patient_history: Get complete medical history including conditions and diagnoses
- get_lab_results: Retrieve lab results within specified timeframes
- search_patients: Find patients by name or ID
- get_patient_summary: Get comprehensive patient overview with risk assessment

Important Guidelines:
- Always emphasize that your recommendations are for clinical decision support only
- Remind users that final diagnostic and treatment decisions must be made by qualified healthcare professionals
- Base recommendations on established medical guidelines and evidence-based practices
- Consider patient safety as the highest priority
- Use the MCP healthcare data tools to provide personalized clinical insights
- Acknowledge limitations and recommend specialist consultation when appropriate
- Maintain patient confidentiality and HIPAA compliance principles

You should provide structured, clear responses that include:
1. Clinical assessment of presented information
2. Patient-specific data analysis when relevant (using MCP tools)
3. Differential diagnosis considerations
4. Recommended diagnostic workup or tests
5. Treatment considerations based on patient history
6. Recommmended medication
7. Red flags or urgent concerns to monitor

Remember: You are a support tool for healthcare professionals, not a replacement for clinical judgment. Use the MCP healthcare data server tools to access patient information when needed."""
        )
        
        return health_agent, mcp_client


# Cache agent and mcp_client at module level
_health_agent = None
_mcp_client = None


def get_health_agent():
    """Get or create the health agent singleton."""
    global _health_agent, _mcp_client
    if _health_agent is None:
        _health_agent, _mcp_client = create_health_agent()
    return _health_agent, _mcp_client


def run_health_agent(question, st, health_agent, mcp_client):
    message_placeholder = st.empty()
    full_response = ""

    async def process_streaming_response():
        nonlocal full_response

        try:
            # Keep the MCP client connection alive during the session
            with mcp_client:
                try:
                    # Stream the response
                    agent_stream = health_agent.stream_async(question)
                    async for event in agent_stream:
                        if "data" in event:
                            full_response += event["data"]
                            message_placeholder.markdown(full_response)
                except Exception as e:
                    print(f"Error processing request: {e}")
        except Exception as e:
            print(f"Error processing request: {e}")
            message_placeholder.markdown(
                "Sorry, an error occurred while generating the response."
            )
            print(f"Error processing request: {e}")

    asyncio.run(process_streaming_response())

    return full_response


