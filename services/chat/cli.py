import os
import requests
import sys

API_BASE_URL = os.getenv("CODEX_API_URL", "http://127.0.0.1:8000")

access_token = None
refresh_token = None


def login(username, password):
    """Authenticate user and store both JWT tokens."""
    global access_token, refresh_token
    try:
        response = requests.post(
            f"{API_BASE_URL}/login",
            data={"username": username, "password": password}
        )
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        return access_token
    except Exception as e:
        print(f"Login failed: {e}")
        return None


def _refresh_access_token():
    """Try to refresh the access token using the refresh token."""
    global access_token, refresh_token
    if not refresh_token:
        return False
    try:
        resp = requests.post(
            f"{API_BASE_URL}/refresh",
            headers={"Authorization": f"Bearer {refresh_token}"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            access_token = data["access_token"]
            refresh_token = data["refresh_token"]
            return True
    except Exception:
        pass
    return False


def _api_request(method, endpoint, **kwargs):
    """Make an API request with auto-refresh on 401."""
    global access_token
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {access_token}"
    headers["X-Refresh-Token"] = refresh_token or ""
    timeout = kwargs.pop("timeout", 30)

    resp = requests.request(method, f"{API_BASE_URL}{endpoint}", headers=headers, timeout=timeout, **kwargs)

    new_token = resp.headers.get("X-New-Access-Token")
    if new_token:
        access_token = new_token

    if resp.status_code == 401:
        if _refresh_access_token():
            headers["Authorization"] = f"Bearer {access_token}"
            resp = requests.request(method, f"{API_BASE_URL}{endpoint}", headers=headers, timeout=timeout, **kwargs)
            new_token = resp.headers.get("X-New-Access-Token")
            if new_token:
                access_token = new_token

    return resp


def ask_codex(question: str):
    """Send a question to the Codex API and return the answer."""
    try:
        response = _api_request("post", "/query", json={"question": question, "search_mode": "hybrid"}, timeout=120)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Query failed: {e}")
        return None


def print_ingestion_status():
    """Fetch and print the ingestion lifecycle status (admin only)."""
    try:
        response = _api_request("get", "/v1/ingestion/status", timeout=10)
        if response.status_code == 403:
            print("Admin access required for /status.")
            return
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Failed to fetch ingestion status: {e}")
        return

    worker = data.get("worker_alive", False)
    print(f"\n[INGESTION STATUS] worker: {'UP' if worker else 'DOWN'}")
    counts = data.get("counts") or {}
    if counts:
        print("  Counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    docs = data.get("documents") or []
    if docs:
        print(f"  Documents ({len(docs)}):")
        for d in docs:
            stage = d.get("ingestion_status", "unknown")
            g = f" | graph_ready: {d.get('graph_ready_at')}" if d.get("graph_ready_at") else ""
            err = f" | error: {d.get('last_error')}" if d.get("last_error") else ""
            print(f"    * {d.get('title')} -> {stage} ({d.get('chunk_count', 0)} chunks{g}{err})")

    log_tail = data.get("log_tail", "")
    if log_tail:
        print("\n[AGENT LOG TAIL]")
        print(log_tail.rstrip())
    print("-" * 30)


def delete_document(identifier: str):
    """Delete a document by id or title (admin only)."""
    try:
        response = _api_request("get", "/v1/ingestion/status", timeout=10)
        if response.status_code == 403:
            print("Admin access required for /delete.")
            return
        response.raise_for_status()
        docs = response.json().get("documents") or []
    except Exception as e:
        print(f"Failed to fetch document list: {e}")
        return

    match = None
    for d in docs:
        if d.get("id") == identifier or identifier.lower() in (d.get("title") or "").lower():
            match = d
            break
    if not match:
        print(f"No document found matching '{identifier}'.")
        return

    status_val = match.get("ingestion_status", "ready")
    in_flight = status_val in ("processing", "pending", "chunks_ready", "graph_building")
    action = "Cancel" if in_flight else "Remove"
    print(f"{action}ing: {match.get('title')} ({match.get('id')}) [{status_val}]")
    confirm = input(f"Type the document title to confirm {action.lower()}: ").strip()
    if confirm != match.get("title"):
        print("Confirmation text did not match. Aborting.")
        return

    try:
        response = _api_request("delete", f"/v1/documents/{match['id']}", timeout=60)
        response.raise_for_status()
        result = response.json()
        print(f"Done: '{result.get('title')}': {result.get('chunks_removed')} chunks removed")
        print(f"  Graph cleanup: {result.get('graph', {})}")
        if result.get("archive_file_removed"):
            print("  Source file removed from archive/.")
    except Exception as e:
        print(f"Delete failed: {e}")


def main():
    print("=" * 50)
    print("   CODEX POLICY INTELLIGENCE ENGINE CLI")
    print("=" * 50)
    print("\nAvailable users: admin, manager, employee")

    username = input("Username: ").strip()
    password = input("Password: ").strip()

    token = login(username, password)
    if not token:
        print("\nAuthentication failed. Exiting...")
        sys.exit(1)

    print(f"\nLogged in as {username}. Welcome to Codex.")
    print("Type your questions. Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit"]:
                print("\nGoodbye!")
                break

            if user_input.strip() == "/status":
                print_ingestion_status()
                continue

            if user_input.strip().startswith("/delete"):
                ident = user_input.strip()[len("/delete"):].strip()
                if ident:
                    delete_document(ident)
                else:
                    print("Usage: /delete <document-id-or-title>")
                continue

            result = ask_codex(user_input)

            if not result:
                print("Codex: Sorry, I encountered an error processing your request.")
                continue

            answer = result.get("answer", "No answer provided.")
            verdict = result.get("verdict", "unknown")
            confidence = result.get("confidence", 0.0)

            print(f"\nCodex: {answer}")

            reasoning = result.get("reasoning") or {}
            approval = reasoning.get("approval")
            if approval and (approval.get("process") or approval.get("matching_roles")):
                roles = approval.get("matching_roles") or []
                process = approval.get("process")
                user_can = approval.get("user_can_approve", False)
                if process and roles:
                    status_str = "APPROVABLE BY YOU" if user_can else "APPROVAL REQUIRED"
                    print(f"\n[{status_str}] {', '.join(roles)} for '{process}'")
                elif process:
                    print(f"\n[NO APPROVAL CHAIN FOUND] '{process}'")
                else:
                    print("\n[NO APPROVAL PROCESS FOUND]")

            conflicts = reasoning.get("conflicts") or []
            if conflicts:
                print(f"\n[CONFLICT DETECTED] {len(conflicts)} conflict(s):")
                for c in conflicts[:5]:
                    ref_a = c.get("ref") or c.get("clause_a") or "unknown"
                    ref_b = c.get("clause_b")
                    reason = c.get("reason") or "contradiction found"
                    where = str(ref_a) + (f" <-> {ref_b}" if ref_b else "")
                    print(f"  * {where} -- {reason}")

            risk = reasoning.get("risk")
            if risk and risk.get("verdict"):
                verdict = risk.get("verdict")
                risk_level = risk.get("risk_level", "unknown")
                regs = risk.get("regulations") or []
                line = f"[COMPLIANCE {verdict.upper()}] risk level: {risk_level}"
                if regs:
                    line += f" | regulations: {', '.join(regs)}"
                print(f"\n{line}")

            next_steps = result.get("next_steps") or []
            if next_steps:
                print("\nNext steps:")
                for s in next_steps[:5]:
                    print(f"  * {s}")

            citations = result.get("citations", [])
            if citations:
                print("\nCitations:")
                for i, cit in enumerate(citations, 1):
                    ref = cit.get("clause_ref", "N/A")
                    title = cit.get("title") or cit.get("document", "N/A")
                    score = cit.get("score", 0.0)
                    quote = cit.get("quote", "")
                    path = cit.get("section_path")
                    where = f" ({path})" if path else ""
                    if quote:
                        print(f'  {i}. "{quote}" -- {title}: {ref}{where} (score: {score:.3f})')
                    else:
                        print(f"  {i}. {title}: {ref}{where} (score: {score:.3f})")

            print(f"\nVerdict: {verdict} | Confidence: {confidence:.2f}")
            print("-" * 30)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break


if __name__ == "__main__":
    main()
