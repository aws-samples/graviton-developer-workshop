"""
Streamlit UI for the Crypto Trading Agent.
"""

import os
import sys
import logging

import boto3
import streamlit as st

from agent import create_agent, run_agent_streaming

logging.basicConfig(level=logging.INFO, format="%(filename)s:%(lineno)d | %(message)s", handlers=[logging.StreamHandler(sys.stderr)])

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")


def authenticate_user(username: str, password: str) -> dict | None:
    client = boto3.client("cognito-idp", region_name=AWS_REGION)
    try:
        response = client.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        return response.get("AuthenticationResult")
    except Exception:
        return None


def login_page():
    st.title("🪙 Crypto Trading Agent")
    st.subheader("Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted and username and password:
            with st.spinner("Authenticating..."):
                tokens = authenticate_user(username, password)
            if tokens:
                st.session_state["authenticated"] = True
                st.session_state["access_token"] = tokens["AccessToken"]
                st.session_state["id_token"] = tokens["IdToken"]
                st.session_state["username"] = username
                st.rerun()
            else:
                st.error("Invalid username or password.")


def chat_page():
    st.title("🪙 Crypto Trading Agent")

    with st.sidebar:
        st.write(f"Logged in as: **{st.session_state.get('username', 'Unknown')}**")
        if st.button("Logout"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        st.divider()
        st.subheader("Sample Prompts")
        for prompt in [
            "Show my current portfolio positions",
            "Calculate the 30-day VaR for my portfolio",
            "Calculate my portfolio Sharpe ratio",
        ]:
            if st.button(prompt, key=f"s_{prompt[:15]}"):
                st.session_state["pending_prompt"] = prompt
                st.rerun()

    # Create agent once per session
    if "agent" not in st.session_state:
        access_token = st.session_state.get("id_token")
        agent, mcp = create_agent(authorization_token=access_token)
        st.session_state["agent"] = agent
        st.session_state["mcp_client"] = mcp

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    pending = st.session_state.pop("pending_prompt", None)
    user_input = st.chat_input("Ask about your crypto portfolio...")
    query = user_input or pending

    if query:
        st.session_state["messages"].append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        with st.chat_message("assistant"):
            response = run_agent_streaming(
                query, st, st.session_state["agent"], st.session_state["mcp_client"]
            )

        st.session_state["messages"].append({"role": "assistant", "content": response})


def main():
    st.set_page_config(page_title="Crypto Trading Agent", page_icon="🪙", layout="wide")
    if st.session_state.get("authenticated"):
        chat_page()
    else:
        login_page()


if __name__ == "__main__":
    main()
