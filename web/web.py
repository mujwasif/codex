import requests
import streamlit as st

API_URL = "http://localhost:8000"  # Replace with your FastAPI server URL
st.set_page_config(page_title="Codex Policy Intelligence Engine", page_icon=":guardsman:", layout="wide")

st.markdown(
    """
    <style>
    .verdict-box {
        padding: 4px 12px;
        border-radius: 4px;
        font-weight: bold;
        display: inline-block;
        margin-right: 10px;
    }
    .verdict-compliant { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    .verdict-non-compliant { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    .verdict-unknown { background-color: #e2e3e5; color: #383d41; border: 1px solid #d6d8db; }
    .metric-text { font-size: 0.9em; color: #555; }
    </style>
    """,
    unsafe_allow_html=True,
)



if "token" not in st.session_state:
    st.session_state.token = None

if "username" not in st.session_state:
    st.session_state.username = None

if "messages" not in st.session_state:
    st.session_state.messages = []

if "history" not in st.session_state:
    st.session_state.history = []

    