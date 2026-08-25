import base64
import html
import json
import os
from datetime import datetime

import requests
import streamlit as st

API_BASE_URL = os.getenv("CODEX_API_URL", "http://127.0.0.1:8000")


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


def retry_policy(token, document_id):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/v1/ingestion/retry/{document_id}",
            headers=headers,
            timeout=30,
        )
        if response.status_code == 403:
            st.error("Admin access required to retry documents.")
            return None
        if response.status_code == 404:
            st.error("Document not found.")
            return None
        if response.status_code == 400:
            st.warning(response.json().get("detail", "Cannot retry this document."))
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Retry failed: {e}")
        return None


def retry_chunks_policy(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/v1/ingestion/retry-chunks",
            headers=headers,
            timeout=30,
        )
        if response.status_code == 403:
            st.error("Admin access required to retry documents.")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Retry chunking failed: {e}")
        return None


def retry_graph_policy(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/v1/ingestion/retry-graph",
            headers=headers,
            timeout=30,
        )
        if response.status_code == 403:
            st.error("Admin access required to retry documents.")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Retry graph failed: {e}")
        return None


def remove_policy(token, document_id):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.delete(
            f"{API_BASE_URL}/v1/documents/{document_id}",
            headers=headers,
            timeout=60,
        )
        if response.status_code == 403:
            st.error("Admin access required to remove documents.")
            return None
        if response.status_code == 404:
            st.error("Document not found (may already be removed).")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Remove failed: {e}")
        return None


def fetch_users(token):
    try:
        response = requests.get(
            f"{API_BASE_URL}/users",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to fetch users: {e}")
        return []


def create_user(token, data):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/users",
            json=data,
            headers=headers,
            timeout=10,
        )
        if response.status_code == 400:
            detail = response.json().get("detail", "Username already exists")
            st.error(detail)
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to create user: {e}")
        return None


def update_user(token, user_id, data):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.patch(
            f"{API_BASE_URL}/users/{user_id}",
            json=data,
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            st.error("User not found.")
            return None
        if response.status_code == 400:
            detail = response.json().get("detail", "No fields to update")
            st.error(detail)
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to update user: {e}")
        return None


def delete_user_api(token, user_id):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.delete(
            f"{API_BASE_URL}/users/{user_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            st.error("User not found.")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to delete user: {e}")
        return None


def fetch_documents(token):
    try:
        response = requests.get(
            f"{API_BASE_URL}/documents",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to fetch documents: {e}")
        return []


def fetch_document_detail(token, doc_id):
    try:
        response = requests.get(
            f"{API_BASE_URL}/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to fetch document detail: {e}")
        return None


INGESTION_STAGES = ["pending", "processing", "chunks_ready", "graph_building", "ready", "failed"]


@st.fragment(run_every=5)
def render_live_ingestion_feed(token):
    """Terminal-style live feed of the ingestion worker output. Auto-refreshes every 5s."""
    data = fetch_ingestion_status(token)
    tail = (data.get("log_tail") or "").strip() if data else ""
    st.caption(
        f"Live feed - auto-refreshes every 5s. Last updated {datetime.now().strftime('%H:%M:%S')}"
    )
    if tail:
        with st.container(height=200):
            st.code(tail[-2000:], language="log")
    else:
        st.info("No ingestion activity yet.")


def render_ingestion_status_panel(status_data, token):
    if not status_data:
        return
    counts = status_data.get("counts") or {}

    render_live_ingestion_feed(token)

    st.markdown(
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
            tone = (
                "verdict-clear" if stage == "ready"
                else "verdict-abstained" if stage == "failed"
                else "verdict-conditional"
            )
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

    failed_docs = [d for d in (docs or []) if d.get("ingestion_status") == "failed"]
    if failed_docs:
        st.subheader(f"Failed Documents ({len(failed_docs)})")
        st.caption("Remove deletes a single document and all its data permanently.")

        chunk_failures = [d for d in failed_docs if not d.get("chunk_count")]
        graph_failures = [d for d in failed_docs if d.get("chunk_count")]

        rc1, rc2 = st.columns(2)
        with rc1:
            if chunk_failures:
                if st.button(f"Retry Chunking ({len(chunk_failures)})", type="primary", use_container_width=True):
                    with st.spinner("Retrying chunking for failed documents..."):
                        result = retry_chunks_policy(token)
                    if result and result.get("retried", 0) > 0:
                        st.success(f"Retrying {result['retried']} document(s) for chunking.")
                        st.rerun()
                    else:
                        st.info("Nothing to retry.")
            else:
                st.button("Retry Chunking (0)", disabled=True, use_container_width=True)
        with rc2:
            if graph_failures:
                if st.button(f"Retry Graph ({len(graph_failures)})", type="primary", use_container_width=True):
                    with st.spinner("Retrying graph generation for failed documents..."):
                        result = retry_graph_policy(token)
                    if result and result.get("retried", 0) > 0:
                        st.success(f"Retrying {result['retried']} document(s) for graph.")
                        st.rerun()
                    else:
                        st.info("Nothing to retry.")
            else:
                st.button("Retry Graph (0)", disabled=True, use_container_width=True)

        for d in failed_docs:
            doc_id = d.get("id")
            title = d.get("title", "Unknown")
            error = d.get("last_error", "No error message")
            chunk_count = d.get("chunk_count", 0)
            fail_type = "graph" if chunk_count else "chunking"
            st.markdown(
                f"**{html.escape(title)}** <span style='color:#856404;'>(failed at {fail_type})</span>"
                f"<br><small style='color:#721c24;'>{html.escape(error[:200])}</small>",
                unsafe_allow_html=True,
            )
            if st.button("Remove", key=f"remove_{doc_id}", use_container_width=False, type="secondary"):
                with st.spinner(f"Removing {title} and all its data..."):
                    result = remove_policy(token, doc_id)
                if result:
                    st.success(f"Removed '{result.get('title')}': {result.get('chunks_removed', 0)} chunks, graph cleaned.")
                    st.rerun()
            st.divider()

    if docs:
        st.subheader("Cancel / Remove Document")
        st.caption(
            "Permanently deletes the document record, its chunks/citations/entities in "
            "PostgreSQL, and the source file in archive/. **In-flight documents (processing/pending/chunks_ready/graph_building) "
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



def render_ingestion_tab(token):
    st.subheader("Ingestion & Policies")
    st.caption(
        "Admin workspace. Upload one PDF or DOCX at a time. Each document becomes "
        "queryable **only** after chunking AND knowledge-graph generation both complete."
    )

    uploaded = st.file_uploader(
        "Drag and drop policy documents (.pdf or .docx)",
        type=["pdf", "docx"],
        accept_multiple_files=True,
        key="ingestion_file_uploader",
    )

    col_a = st.columns(1)[0]
    with col_a:
        upload_clicked = st.button("Upload Policies", type="primary", use_container_width=True)

    if uploaded and upload_clicked:
        if isinstance(uploaded, list):
            files_to_upload = uploaded
        else:
            files_to_upload = [uploaded]

        progress_bar = st.progress(0)
        status_text = st.empty()
        
        success_count = 0
        for idx, file in enumerate(files_to_upload):
            if not file.name.lower().endswith((".pdf", ".docx")):
                st.error(f"Skipping {file.name}: Only .pdf and .docx are supported.")
                continue
            
            status_text.text(f"Queuing {idx+1}/{len(files_to_upload)}: {file.name}...")
            result = upload_policy(token, file)
            if result:
                success_count += 1
            
            progress_bar.progress((idx + 1) / len(files_to_upload))
        
        status_text.empty()
        progress_bar.empty()
        
        if success_count > 0:
            st.success(f"Successfully queued {success_count} of {len(files_to_upload)} documents.")
            st.rerun()
        else:
            st.error("No documents were successfully queued.")

    st.divider()
    render_ingestion_auto_refresh(token)


@st.fragment(run_every=2)
def render_ingestion_auto_refresh(token):
    with st.spinner("Loading ingestion status..."):
        status_data = fetch_ingestion_status(token)
    render_ingestion_status_panel(status_data, token)


def render_users_tab(token):
    st.subheader("User Management")
    st.caption("Admin workspace. Create, edit, and delete users. New users default to Employee access level.")

    # --- Add new user form ---
    st.markdown("#### Add New User")
    with st.form("create_user_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_username = st.text_input("Username", key="new_user_name")
            new_password = st.text_input("Password", type="password", key="new_user_pass")
        with col2:
            new_department = st.text_input("Department", key="new_user_dept")
            new_access = st.selectbox(
                "Access Level",
                options=[1, 2, 3],
                format_func=lambda x: {1: "1 - Employee", 2: "2 - Manager", 3: "3 - Admin"}[x],
                key="new_user_level",
            )
        create_clicked = st.form_submit_button("Create User", type="primary")

    if create_clicked:
        if not new_username or not new_password or not new_department:
            st.error("All fields are required.")
        else:
            result = create_user(token, {
                "username": new_username,
                "password": new_password,
                "department": new_department,
                "access_level": new_access,
            })
            if result:
                st.success(f"User '{result['username']}' created.")
                st.rerun()

    st.divider()

    # --- User list ---
    st.markdown("#### All Users")
    with st.spinner("Loading users..."):
        users = fetch_users(token)

    if not users:
        st.info("No users found.")
        return

    # Render table
    header_cols = st.columns([2, 2, 1, 1, 2, 2])
    headers = ["Username", "Department", "Level", "Active", "Created", "Actions"]
    for col, h in zip(header_cols, headers):
        with col:
            st.markdown(f"**{h}**")

    for user in users:
        uid = user["id"]
        level_label = {1: "Employee", 2: "Manager", 3: "Admin"}.get(user["access_level"], "?")
        active_badge = "Yes" if user["is_active"] else "No"
        created = (user.get("created_at") or "")[:10]

        row_cols = st.columns([2, 2, 1, 1, 2, 2])
        with row_cols[0]:
            st.write(user["username"])
        with row_cols[1]:
            st.write(user["department"])
        with row_cols[2]:
            st.write(level_label)
        with row_cols[3]:
            st.write(active_badge)
        with row_cols[4]:
            st.write(created)
        with row_cols[5]:
            edit_key = f"edit_{uid}"
            delete_key = f"delete_{uid}"
            e1, e2 = st.columns(2)
            with e1:
                if st.button("Edit", key=edit_key, use_container_width=True):
                    st.session_state[f"editing_{uid}"] = True
            with e2:
                if st.button("Delete", key=delete_key, use_container_width=True, type="secondary"):
                    st.session_state[f"deleting_{uid}"] = True

        # --- Inline edit form ---
        if st.session_state.get(f"editing_{uid}"):
            with st.expander(f"Edit {user['username']}", expanded=True):
                with st.form(f"edit_form_{uid}"):
                    edit_dept = st.text_input("Department", value=user["department"])
                    edit_level = st.selectbox(
                        "Access Level",
                        options=[1, 2, 3],
                        index=user["access_level"] - 1,
                        format_func=lambda x: {1: "1 - Employee", 2: "2 - Manager", 3: "3 - Admin"}[x],
                    )
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        save_clicked = st.form_submit_button("Save Changes", type="primary")
                    with fc2:
                        cancel_clicked = st.form_submit_button("Cancel")

                if save_clicked:
                    result = update_user(token, uid, {
                        "department": edit_dept,
                        "access_level": edit_level,
                    })
                    if result:
                        st.success(f"User '{result['username']}' updated.")
                        st.session_state[f"editing_{uid}"] = False
                        st.rerun()
                if cancel_clicked:
                    st.session_state[f"editing_{uid}"] = False
                    st.rerun()

        # --- Inline delete confirmation ---
        if st.session_state.get(f"deleting_{uid}"):
            st.warning(f"Are you sure you want to permanently delete **{user['username']}**?")
            dc1, dc2 = st.columns(2)
            with dc1:
                if st.button("Confirm Delete", key=f"confirm_del_{uid}", type="primary"):
                    result = delete_user_api(token, uid)
                    if result:
                        st.success(f"User '{result['username']}' deleted.")
                        st.session_state[f"deleting_{uid}"] = False
                        st.rerun()
            with dc2:
                if st.button("Cancel Delete", key=f"cancel_del_{uid}"):
                    st.session_state[f"deleting_{uid}"] = False
                    st.rerun()


def render_documents_tab(token):
    st.subheader("Documents & Chunks")
    st.caption("Browse ingested policy documents and their chunked content.")

    with st.spinner("Loading documents..."):
        documents = fetch_documents(token)

    if not documents:
        st.info("No documents found. Upload documents in the Ingestion tab.")
        return

    # Search bar
    search_query = st.text_input("Search chunks by text...", key="doc_search", placeholder="e.g. password policy")

    # Document list
    st.markdown("#### Documents")

    for doc in documents:
        doc_id = doc["id"]
        status = doc.get("status", "unknown")
        chunk_count = doc.get("chunk_count", 0)
        entity_count = doc.get("entity_count", 0)

        tone = "verdict-clear" if status == "active" else "verdict-abstained" if status == "failed" else "verdict-conditional"

        col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
        with col1:
            st.markdown(f"**{doc['title']}**")
        with col2:
            st.markdown(f'<span class="verdict-box {tone}">{status}</span>', unsafe_allow_html=True)
        with col3:
            st.caption(f"{chunk_count} chunks")
        with col4:
            st.caption(f"{entity_count} entities")

        # View detail button
        if st.button("View Chunks", key=f"view_doc_{doc_id}", use_container_width=True):
            st.session_state[f"viewing_doc_{doc_id}"] = not st.session_state.get(f"viewing_doc_{doc_id}", False)

        # Show document detail if toggled
        if st.session_state.get(f"viewing_doc_{doc_id}", False):
            with st.spinner(f"Loading chunks for {doc['title']}..."):
                detail = fetch_document_detail(token, doc_id)

            if detail:
                chunks = detail.get("chunks", [])
                entities = detail.get("entities", [])

                # Filter chunks if search query exists
                if search_query:
                    search_lower = search_query.lower()
                    chunks = [c for c in chunks if search_lower in (c.get("text", "") or "").lower()
                              or search_lower in (c.get("clause_ref", "") or "").lower()]

                # Display chunks
                if chunks:
                    st.markdown(f"**Chunks ({len(chunks)})**")
                    for i, chunk in enumerate(chunks):
                        clause_ref = chunk.get("clause_ref", "N/A")
                        section_path = chunk.get("section_path", "")
                        token_count = chunk.get("token_count", 0)
                        text = chunk.get("text", "")

                        # Create a collapsible expander for each chunk
                        with st.expander(f"Chunk {i+1}: {clause_ref} ({token_count} tokens) - {section_path[:50]}{'...' if len(section_path) > 50 else ''}"):
                            st.markdown(f"**Clause Ref:** `{clause_ref}`")
                            st.markdown(f"**Section Path:** {section_path}")
                            st.markdown(f"**Token Count:** {token_count}")
                            st.divider()
                            st.markdown(text)
                else:
                    st.info("No chunks found for this document.")

                # Display entities
                if entities:
                    st.markdown(f"**Entities ({len(entities)})**")
                    for entity in entities:
                        st.markdown(f"- **{entity.get('type', 'N/A')}**: {entity.get('name', 'N/A')}")
                        if entity.get("attrs"):
                            st.json(entity["attrs"])

        st.divider()


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
    .users-section h4 {
        color: #d7dce3;
        font-size: 1em;
        margin-bottom: 8px;
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
            col_a, col_i, col_d, col_u = st.columns(4)
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
            with col_d:
                if st.button(
                    "Documents",
                    use_container_width=True,
                    type="primary" if st.session_state.active_view == "documents" else "secondary",
                ):
                    st.session_state.active_view = "documents"
                    st.rerun()
            with col_u:
                if st.button(
                    "Users",
                    use_container_width=True,
                    type="primary" if st.session_state.active_view == "users" else "secondary",
                ):
                    st.session_state.active_view = "users"
                    st.rerun()

    if st.session_state.active_view == "ingestion":
        render_ingestion_tab(st.session_state.token)
    elif st.session_state.active_view == "documents":
        render_documents_tab(st.session_state.token)
    elif st.session_state.active_view == "users":
        render_users_tab(st.session_state.token)
    else:
        render_assistant_tab()
else:
    render_assistant_tab()
