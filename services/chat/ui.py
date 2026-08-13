import base64
import html
import json
import os

import requests
import streamlit as st

API_BASE_URL = os.getenv("CODEX_API_URL", "http://localhost:8000")


def token_payload(token):
    """Decode the JWT payload (no signature check) to read access_level."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def fetch_ingestion_status(token):
    try:
        response = requests.get(
            f"{API_BASE_URL}/v1/ingestion/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to fetch ingestion status: {e}")
        return None


def upload_policy(token, file):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/v1/ingest",
            files={"file": (file.name, file.getvalue(), file.type or "application/octet-stream")},
            headers=headers,
            timeout=60,
        )
        if response.status_code == 403:
            st.error("Admin access required to ingest documents.")
            return None
        if response.status_code == 409:
            message = response.json().get("detail", "already exists in the knowledge base")
            st.warning(f"Duplicate upload blocked: {message}")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Upload failed: {e}")
        return None


def delete_policy(token, document_id):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.delete(
            f"{API_BASE_URL}/v1/documents/{document_id}",
            headers=headers,
            timeout=60,
        )
        if response.status_code == 403:
            st.error("Admin access required to delete documents.")
            return None
        if response.status_code == 404:
            st.error(f"Document {document_id} no longer exists (may have been removed mid-flight).")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Delete failed: {e}")
        return None


INGESTION_STAGES = ["pending", "processing", "chunks_ready", "graph_building", "ready", "failed"]


def render_ingestion_status_panel(status_data, token):
    if not status_data:
        return
    worker = status_data.get("worker_alive", False)
    counts = status_data.get("counts") or {}

    w_label = "UP" if worker else "DOWN"
    tone = "verdict-clear" if worker else "verdict-abstained"
    st.markdown(
        f'<span class="verdict-box {tone}">INGESTION WORKER: {w_label}</span>'
        '<span class="metric-text">documents become ready only after chunking AND graph; '
        'ingestion runs only for files uploaded here</span>',
        unsafe_allow_html=True,
    )

    cols = st.columns(len(counts) or 1)
    for col, (stage, n) in zip(cols, counts.items()):
        with col:
            st.metric(stage, n)

    docs = status_data.get("documents") or []
    if docs:
        rows = []
        for d in docs:
            stage = d.get("ingestion_status", "unknown")
            tone = "verdict-clear" if stage == "ready" else "verdict-abstained" if stage == "failed" \
                else "verdict-conditional"
            rows.append({
                "Title": d.get("title"),
                "Stage": f"<span class='verdict-box {tone}'>{stage}</span>",
                "Chunks": d.get("chunk_count", 0),
                "Graph Ready": (d.get("graph_ready_at") or "")[:19],
                "Updated": (d.get("updated_at") or "")[:19],
                "Error": d.get("last_error") or "",
            })
        st.markdown("**Recent documents**")
        st.markdown(
            "<table style='width:100%'><tr>"
            + "".join(f"<th>{c}</th>" for c in ["Title", "Stage", "Chunks", "Graph Ready", "Updated", "Error"])
            + "</tr>" + "".join(
                "<tr>" + "".join(f"<td>{r[c]}</td>" for c in ["Title", "Stage", "Chunks", "Graph Ready", "Updated", "Error"]) + "</tr>"
                for r in rows
            ) + "</table>",
            unsafe_allow_html=True,
        )

    if docs:
        st.subheader("Cancel / Remove Document")
        st.caption(
            "Permanently deletes the document record, its chunks/citations/entities in "
            "PostgreSQL, its knowledge-graph nodes/edges in Neo4j, and the source file "
            "in archive/. **In-flight documents (processing/chunks_ready/graph_building) "
            "are cancelled.** This cannot be undone."
        )
        options = {
            f"{d.get('title')}  [{d.get('ingestion_status')}]": d.get("id")
            for d in docs
        }
        selected = st.selectbox("Document", list(options.keys()), key="ingestion_doc_select")
        selected_status = selected.split(" [")[-1].rstrip("]") if selected else "ready"
        in_flight = selected_status in ("processing", "pending", "chunks_ready", "graph_building")
        confirm_delete = st.checkbox("I understand this permanently removes the document and its graph")
        action_label = "Cancel Document" if in_flight else "Remove Document"
        if st.button(action_label, type="secondary", use_container_width=True):
            if not confirm_delete:
                st.warning("Please tick the confirmation checkbox first.")
            else:
                with st.spinner(f"{action_label} and cleaning up all related content..."):
                    result = delete_policy(token, options[selected])
                if result:
                    st.success(
                        f"{action_label.split()[0]}d '{result.get('title')}': "
                        f"{result.get('chunks_removed')} chunks removed, "
                        f"{result.get('graph', {}).get('clauses_deleted', '?')} graph clauses removed."
                    )
                    st.rerun()

    log_tail = (status_data.get("log_tail") or "").strip()
    if log_tail:
        with st.expander("Ingestion Agent Log (tail)"):
            st.code(log_tail[-3000:], language="log")


def render_ingestion_tab(token):
    st.subheader("Ingestion & Policies")
    st.caption(
        "Admin workspace. Upload one PDF or DOCX at a time. Each document becomes "
        "queryable **only** after chunking AND knowledge-graph generation both complete."
    )

    uploaded = st.file_uploader(
        "Drag and drop a policy document (single file, .pdf or .docx)",
        type=["pdf", "docx"],
        accept_multiple_files=False,
        key="ingestion_file_uploader",
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        upload_clicked = st.button("Upload Policy", type="primary", use_container_width=True)
    with col_b:
        refresh_clicked = st.button("Refresh Status", use_container_width=True)

    if uploaded is not None and upload_clicked:
        if not uploaded.name.lower().endswith((".pdf", ".docx")):
            st.error("Only .pdf and .docx files are supported.")
        else:
            with st.spinner(f"Queuing {uploaded.name}..."):
                result = upload_policy(token, uploaded)
            if result:
                st.success(f"Queued: {result.get('filename')}. Watch it become ready below.")
                st.rerun()

    if refresh_clicked:
        st.rerun()

    st.divider()
    with st.spinner("Loading ingestion status..."):
        status_data = fetch_ingestion_status(token)
    render_ingestion_status_panel(status_data, token)


def render_citations(citations):
    """Render the citations list with the full source quote (if present)."""
    if not citations:
        return
    rows = []
    for idx, cit in enumerate(citations, 1):
        ref = cit.get("clause_ref", "N/A")
        title = cit.get("title") or cit.get("document", "N/A")
        score = cit.get("score", 0.0)
        quote = cit.get("quote")
        path = cit.get("section_path")
        header = f"{idx}. {html.escape(title)}: Clause <code>{html.escape(str(ref))}</code>"
        header += f" &mdash; Relevance Score: <code>{score:.3f}</code>"
        if path:
            header += f" &middot; {html.escape(str(path))}"
        rows.append(f'<div class="cite-row"><span class="cite-head">{header}</span>')
        if quote:
            rows.append(f'<div class="cite-quote">{html.escape(str(quote))}</div>')
        rows.append("</div>")
    st.markdown(
        f"""
        <div class="meta-card">
            <h4>View Citations ({len(citations)})</h4>
            <div class="card-scroll">{''.join(rows)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_approval(approval):
    """Render the approval-matrix decision as a callout box."""
    if not approval:
        return
    process = approval.get("process")
    roles = approval.get("matching_roles") or []
    approvable = approval.get("approvable", False)
    user_can = approval.get("user_can_approve", False)

    if process and roles:
        if user_can:
            status = "APPROVABLE BY YOU"
            style = "verdict-clear"
        else:
            status = "APPROVAL REQUIRED"
            style = "verdict-conditional"
        detail = f"{', '.join(roles)} for '{process}'"
    elif process:
        status = "NO APPROVAL CHAIN FOUND"
        style = "verdict-abstained"
        detail = f"'{process}'"
    else:
        status = "NO APPROVAL PROCESS FOUND"
        style = "verdict-abstained"
        detail = "no matching process in the knowledge graph"

    st.markdown(
        f"""
        <div style="margin-top: 10px;">
            <span class="verdict-box {style}">{html.escape(status)}</span>
            <span class="metric-text">{html.escape(detail)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_conflicts(conflicts):
    """Render detected policy conflicts as a callout box."""
    if not conflicts:
        return
    rows = []
    for c in conflicts:
        ref_a = c.get("ref") or c.get("clause_a") or "unknown"
        ref_b = c.get("clause_b")
        reason = c.get("reason") or "contradiction found"
        source = c.get("source", "")
        where = str(ref_a)
        if ref_b:
            where += f" ↔ {str(ref_b)}"
        row = f'<div class="cite-row"><span style="color:#721c24;">⚠ {html.escape(where)}</span>'
        row += f' &mdash; {html.escape(str(reason))}'
        if source:
            row += f' <span style="color:#555;">(source: {html.escape(str(source))})</span>'
        row += "</div>"
        rows.append(row)
    st.markdown(
        f"""
        <div class="meta-card">
            <h4>Conflicts Detected ({len(conflicts)})</h4>
            <div class="card-scroll">{''.join(rows)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_risk(risk):
    """Render the compliance-risk assessment as a callout box."""
    if not risk:
        return
    verdict = risk.get("verdict", "unknown")
    risk_level = risk.get("risk_level", "unknown")
    regulations = risk.get("regulations") or []
    if verdict == "clear":
        style = "verdict-clear"
    elif verdict == "violation":
        style = "verdict-violation"
    else:
        style = "verdict-conditional"

    detail = f"Risk level: {html.escape(str(risk_level))}"
    if regulations:
        detail += f" · Regulations: {html.escape(', '.join(str(r) for r in regulations))}"

    st.markdown(
        f"""
        <div style="margin-top: 10px;">
            <span class="verdict-box {style}">Compliance: {html.escape(str(verdict).upper())}</span>
            <span class="metric-text">{detail}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_next_steps(next_steps):
    """Render the actionable next steps, if any."""
    if not next_steps:
        return
    items = "".join(
        f'<div class="cite-row">{html.escape(str(step))}</div>' for step in next_steps
    )
    st.markdown(
        f"""
        <div class="meta-card">
            <h4>Next Steps ({len(next_steps)})</h4>
            <div class="card-scroll">{items}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.set_page_config(
    page_title="Codex Policy Intelligence Engine",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
    .verdict-clear { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    .verdict-violation { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    .verdict-conditional { background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
    .verdict-abstained { background-color: #e2e3e5; color: #383d41; border: 1px solid #d6d8db; }
    .metric-text { font-size: 0.9em; color: #555; }
    .intent-chip {
        background-color: #233554;
        color: #9fb6ff;
        border: 1px solid #2f4a86;
        border-radius: 12px;
        padding: 3px 10px;
        font-size: 0.8em;
        font-weight: 600;
        display: inline-block;
        margin-right: 10px;
    }
    div[data-testid="stLayoutWrapper"]:has(> .st-key-app_header) {
        position: sticky;
        top: 60px;
        z-index: 999;
        background-color: #1b1f27;
        padding: 6px 8px;
        margin-bottom: 8px;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
    }
    .app-header-title {
        color: #ffffff;
        font-size: 1.05em;
        font-weight: 700;
        padding-top: 6px;
        margin-bottom: 0;
        white-space: nowrap;
    }
    .app-header-title small {
        color: #9aa3b0;
        font-size: 0.7em;
        font-weight: 400;
        display: block;
        margin-top: 2px;
        white-space: normal;
    }
    div[data-testid="stLayoutWrapper"]:has(> .st-key-app_header) button[kind="secondary"] {
        background-color: rgba(255, 255, 255, 0.10);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.25);
    }
    div[data-testid="stLayoutWrapper"]:has(> .st-key-app_header) button[kind="secondary"]:hover {
        background-color: rgba(255, 255, 255, 0.18);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.4);
    }
    div.stVerticalBlock.st-key-chat_transcript {
        min-height: calc(100vh - 308px) !important;
        max-height: calc(100vh - 308px) !important;
        padding-bottom: 20px;
        background-color: #1b1f27;
        border: 1px solid rgba(255, 255, 255, 0.12);
    }
    div.stVerticalBlock.st-key-chat_transcript::-webkit-scrollbar {
        width: 8px;
    }
    div.stVerticalBlock.st-key-chat_transcript::-webkit-scrollbar-thumb {
        background: #3a4350;
        border-radius: 4px;
    }
    div.stVerticalBlock.st-key-chat_transcript::-webkit-scrollbar-track {
        background: transparent;
    }
    .st-key-chat_transcript [data-testid="stChatMessageContent"] {
        color: #e6e9ef;
    }
    .st-key-chat_transcript .stChatMessage:has([data-testid="stChatMessageAvatarUser"]) {
        background-color: #2f6fed;
    }
    .st-key-chat_transcript .stChatMessage:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
        color: #ffffff;
    }
    .st-key-chat_transcript .stChatMessage:has([data-testid="stChatMessageAvatarAssistant"]) {
        background-color: rgba(255, 255, 255, 0.05);
    }
    .st-key-chat_transcript [data-testid="stChatMessageAvatarUser"] {
        color: #ffffff;
    }
    .st-key-chat_transcript [data-testid="stChatMessageAvatarAssistant"] {
        background-color: #2a303b;
        color: #d7dce3;
    }
    .st-key-chat_transcript [data-testid="stSpinner"] {
        color: #b8c0cc;
    }
    [data-testid="stChatInput"] {
        background-color: #161a20;
        border-top: 1px solid #2a303b;
    }
    [data-testid="stChatInput"] textarea {
        color: #e6e9ef;
    }
    [data-testid="stChatInput"] textarea::placeholder {
        color: rgba(255, 255, 255, 0.45);
    }
    .meta-card {
        border: 1px solid #323b49;
        border-radius: 6px;
        padding: 10px 12px;
        margin-top: 8px;
        background-color: #222a36;
    }
    .meta-card h4 {
        margin: 0 0 6px;
        font-size: 1em;
        color: #d7dce3;
    }
    .card-scroll {
        max-height: 340px;
        overflow-y: auto;
        padding-right: 4px;
    }
    .card-scroll::-webkit-scrollbar {
        width: 8px;
    }
    .card-scroll::-webkit-scrollbar-thumb {
        background: #3a4350;
        border-radius: 4px;
    }
    .cite-row {
        font-size: 1em;
        margin-bottom: 8px;
        color: #c6cdd6;
    }
    .cite-head {
        color: #d7dce3;
        font-weight: 600;
    }
    .cite-quote {
        border-left: 3px solid #4a5464;
        padding-left: 8px;
        color: #b8c0cc;
        margin: 4px 0 8px;
        font-size: 0.95em;
        white-space: pre-wrap;
    }
    .st-key-chat_transcript .metric-text {
        color: #9aa3b0;
    }
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
if "active_view" not in st.session_state:
    st.session_state.active_view = "assistant"
if "pending" not in st.session_state:
    st.session_state.pending = None


def login_user(username, password):
    try:
        response = requests.post(
            f"{API_BASE_URL}/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("access_token")
    except requests.exceptions.RequestException as e:
        st.error(f"Authentication failed: {e}")
        return None


def ask_codex(question: str):
    headers = {"Authorization": f"Bearer {st.session_state.token}"}

    try:
        response = requests.post(
            f"{API_BASE_URL}/query",
            json={"question": question, "search_mode": "hybrid"},
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Query error: {e}")
        return None


def fetch_chat_history(token):
    """Load the user's persisted Q&A history (newest first) for rehydration."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{API_BASE_URL}/query/history", headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Failed to fetch chat history: {e}")
        return None


def clear_chat_history(token):
    """Permanently delete the user's chat history from the server."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.delete(f"{API_BASE_URL}/query/history", headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to clear chat history: {e}")
        return None


def messages_from_history(history):
    """Convert persisted QueryResponse items into chat bubbles (oldest first)."""
    messages = []
    for item in reversed(history):
        question = item.get("question")
        answer = item.get("answer")
        if not question or not answer:
            continue
        messages.append({"role": "user", "content": question})
        messages.append(
            {
                "role": "assistant",
                "content": answer,
                "metadata": {
                    "verdict": item.get("verdict", "unknown"),
                    "confidence": item.get("confidence") or 0.0,
                    "citations": item.get("citations") or [],
                    "intent": item.get("intent"),
                },
            }
        )
    return messages


if not st.session_state.token:
    st.title("Codex Policy Intelligence Engine")
    st.subheader("Authentication Required")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        with st.form("login_form"):
            st.write("Please log in to continue")
            username_input = st.text_input("Username", value="admin")
            password_input = st.text_input("Password", type="password")
            submit_button = st.form_submit_button("Log In", use_container_width=True)

            if submit_button:
                if not username_input or not password_input:
                    st.warning("Please enter both username and password.")
                else:
                    token = login_user(username_input, password_input)
                    if token:
                        st.session_state.token = token
                        st.session_state.username = username_input
                        history = fetch_chat_history(token)
                        st.session_state.messages = messages_from_history(history) if history else []
                        st.rerun()

    st.stop()


with st.sidebar:
    st.title("Codex Control Panel")
    st.write(f"Logged in as: **{st.session_state.username}**")

    st.divider()

    confirm_clear = st.checkbox(
        "I understand this permanently deletes my chat history",
        key="confirm_clear_history",
    )
    if st.button("Clear Conversation", use_container_width=True):
        if not confirm_clear:
            st.warning("Tick the checkbox to confirm permanent deletion of your chat history.")
        else:
            with st.spinner("Deleting chat history..."):
                result = clear_chat_history(st.session_state.token)
            st.session_state.messages = []
            if result and result.get("queries_deleted", 0) > 0:
                st.success(f"Deleted {result.get('queries_deleted')} saved question(s) from history.")
            st.rerun()

    if st.button("Log Out", use_container_width=True):
        st.session_state.token = None
        st.session_state.username = None
        st.session_state.messages = []
        st.session_state.active_view = "assistant"
        st.rerun()

is_admin = token_payload(st.session_state.token).get("access_level", 1) >= 3

if not is_admin:
    st.title("Codex Policy Intelligence Engine")
    st.caption("Context-Aware RAG Engine for Policy Querying & Compliance Verification")


def _verdict_class(verdict):
    if verdict == "clear":
        return "verdict-clear"
    if verdict == "violation":
        return "verdict-violation"
    if verdict == "conditional":
        return "verdict-conditional"
    return "verdict-abstained"


def _render_bubble(msg):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        if msg["role"] == "assistant" and "metadata" in msg:
            meta = msg["metadata"]
            verdict = meta.get("verdict", "unknown")
            confidence = meta.get("confidence", 0.0)
            citations = meta.get("citations", [])
            intent = meta.get("intent")
            intent_confidence = meta.get("intent_confidence")
            approval = meta.get("approval")
            conflicts = meta.get("conflicts", [])
            risk = meta.get("risk")
            next_steps = meta.get("next_steps", [])

            intent_chip = ""
            if intent:
                label = html.escape(str(intent).upper())
                if intent_confidence is not None:
                    pct = float(intent_confidence) * 100
                    label += f" &middot; {pct:.0f}%"
                intent_chip = f'<span class="intent-chip">Intent: {label}</span>'

            st.markdown(
                f"""
                <div style="margin-top: 10px;">
                    {intent_chip}
                    <span class="verdict-box {_verdict_class(verdict)}">Verdict: {html.escape(str(verdict).upper())}</span>
                    <span class="metric-text">Confidence Score: <b>{confidence:.2f}</b></span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            render_approval(approval)
            render_conflicts(conflicts)
            render_risk(risk)
            render_next_steps(next_steps)

            if citations:
                render_citations(citations)


def render_assistant_tab():
    if prompt := st.chat_input("Ask a policy question..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.pending = prompt
        st.rerun()

    with st.container(height=500, autoscroll=True, key="chat_transcript"):
        for msg in st.session_state.messages:
            _render_bubble(msg)

        if st.session_state.pending:
            with st.chat_message("assistant"):
                with st.spinner("Analyzing policy documents..."):
                    result = ask_codex(st.session_state.pending)

            if result:
                answer = result.get("answer", "No answer provided.")
                verdict = result.get("verdict", "unknown")
                confidence = result.get("confidence", 0.0)
                citations = result.get("citations", [])
                reasoning = result.get("reasoning") or {}
                intent = reasoning.get("intent")
                intent_confidence = reasoning.get("intent_confidence")
                approval = reasoning.get("approval")
                conflicts = reasoning.get("conflicts", [])
                risk = reasoning.get("risk")
                next_steps = result.get("next_steps", [])

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": answer,
                        "metadata": {
                            "verdict": verdict,
                            "confidence": confidence,
                            "citations": citations,
                            "intent": intent,
                            "intent_confidence": intent_confidence,
                            "approval": approval,
                            "conflicts": conflicts,
                            "risk": risk,
                            "next_steps": next_steps,
                        },
                    }
                )
            else:
                st.session_state.messages.append(
                    {"role": "assistant", "content": "Error processing your request. Please check connection."}
                )

            st.session_state.pending = None
            st.rerun()


if is_admin:
    with st.container(key="app_header"):
        col_title, col_btns = st.columns([4, 2], vertical_alignment="center")
        with col_title:
            st.markdown(
                '<div class="app-header-title">Codex Policy Intelligence Engine '
                '<small>Context-Aware RAG Engine for Policy Querying &amp; Compliance Verification</small></div>',
                unsafe_allow_html=True,
            )
        with col_btns:
            col_a, col_i = st.columns(2)
            with col_a:
                if st.button(
                    "Assistant",
                    use_container_width=True,
                    type="primary" if st.session_state.active_view == "assistant" else "secondary",
                ):
                    st.session_state.active_view = "assistant"
                    st.rerun()
            with col_i:
                if st.button(
                    "Ingestion & Policies",
                    use_container_width=True,
                    type="primary" if st.session_state.active_view == "ingestion" else "secondary",
                ):
                    st.session_state.active_view = "ingestion"
                    st.rerun()

    if st.session_state.active_view == "ingestion":
        render_ingestion_tab(st.session_state.token)
    else:
        render_assistant_tab()
else:
    render_assistant_tab()