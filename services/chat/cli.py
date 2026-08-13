import requests
import sys

API_BASE_URL = "http://localhost:8000"


def login(username, password):
    """Authenticate user and return JWT token."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/login",
            data={"username": username, "password": password}
        )
        response.raise_for_status()
        data = response.json()
        return data.get("access_token")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return None


def ask_codex(question: str, token: str):
    """Send a question to the Codex API and return the answer."""
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.post(
            f"{API_BASE_URL}/query",
            json={"question": question, "search_mode": "hybrid"},
            headers=headers,
            timeout=120
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Query failed: {e}")
        return None


def print_ingestion_status(token: str):
    """Fetch and print the ingestion lifecycle status (admin only)."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{API_BASE_URL}/v1/ingestion/status", headers=headers, timeout=10)
        if response.status_code == 403:
            print("⛔ Admin access required for /status.")
            return
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"❌ Failed to fetch ingestion status: {e}")
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
            print(f"    • {d.get('title')} → {stage} ({d.get('chunk_count', 0)} chunks{g}{err})")

    log_tail = data.get("log_tail", "")
    if log_tail:
        print("\n[AGENT LOG TAIL]")
        print(log_tail.rstrip())
    print("-" * 30)


def delete_document(token: str, identifier: str):
    """Delete a document by id or title (admin only)."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{API_BASE_URL}/v1/ingestion/status", headers=headers, timeout=10)
        if response.status_code == 403:
            print("⛔ Admin access required for /delete.")
            return
        response.raise_for_status()
        docs = response.json().get("documents") or []
    except Exception as e:
        print(f"❌ Failed to fetch document list: {e}")
        return

    match = None
    for d in docs:
        if d.get("id") == identifier or identifier.lower() in (d.get("title") or "").lower():
            match = d
            break
    if not match:
        print(f"❌ No document found matching '{identifier}'.")
        return

    status = match.get("ingestion_status", "ready")
    in_flight = status in ("processing", "pending", "chunks_ready", "graph_building")
    action = "Cancel" if in_flight else "Remove"
    print(f"{action}ing: {match.get('title')} ({match.get('id')}) [{status}]")
    confirm = input(f"Type the document title to confirm {action.lower()}: ").strip()
    if confirm != match.get("title"):
        print("⛔ Confirmation text did not match. Aborting.")
        return

    try:
        response = requests.delete(
            f"{API_BASE_URL}/v1/documents/{match['id']}",
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        print(f"✓ {action}ed '{result.get('title')}': {result.get('chunks_removed')} chunks removed")
        print(f"  Graph cleanup: {result.get('graph', {})}")
        if result.get("archive_file_removed"):
            print("  Source file removed from archive/.")
    except Exception as e:
        print(f"❌ Delete failed: {e}")


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

    print(f"\n✓ Logged in as {username}. Welcome to Codex.")
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
                print_ingestion_status(token)
                continue

            if user_input.strip().startswith("/delete"):
                ident = user_input.strip()[len("/delete"):].strip()
                if ident:
                    delete_document(token, ident)
                else:
                    print("Usage: /delete <document-id-or-title>")
                continue

            # Send query to API (server handles conversational history)
            result = ask_codex(user_input, token)

            if not result:
                print("Codex: Sorry, I encountered an error processing your request.")
                continue

            # Extract and display answer
            answer = result.get("answer", "No answer provided.")
            verdict = result.get("verdict", "unknown")
            confidence = result.get("confidence", 0.0)

            print(f"\nCodex: {answer}")

            # Print approval decision, if present
            reasoning = result.get("reasoning") or {}
            approval = reasoning.get("approval")
            if approval and (approval.get("process") or approval.get("matching_roles")):
                roles = approval.get("matching_roles") or []
                process = approval.get("process")
                user_can = approval.get("user_can_approve", False)
                if process and roles:
                    status = "APPROVABLE BY YOU" if user_can else "APPROVAL REQUIRED"
                    print(f"\n[{status}] {', '.join(roles)} for '{process}'")
                elif process:
                    print(f"\n[NO APPROVAL CHAIN FOUND] '{process}'")
                else:
                    print("\n[NO APPROVAL PROCESS FOUND]")

            # Print detected conflicts, if any
            conflicts = reasoning.get("conflicts") or []
            if conflicts:
                print(f"\n[CONFLICT DETECTED] {len(conflicts)} conflict(s):")
                for c in conflicts[:5]:
                    ref_a = c.get("ref") or c.get("clause_a") or "unknown"
                    ref_b = c.get("clause_b")
                    reason = c.get("reason") or "contradiction found"
                    where = str(ref_a) + (f" ↔ {ref_b}" if ref_b else "")
                    print(f"  • {where} — {reason}")

            # Print compliance risk assessment, if present
            risk = reasoning.get("risk")
            if risk and risk.get("verdict"):
                verdict = risk.get("verdict")
                risk_level = risk.get("risk_level", "unknown")
                regs = risk.get("regulations") or []
                line = f"[COMPLIANCE {verdict.upper()}] risk level: {risk_level}"
                if regs:
                    line += f" | regulations: {', '.join(regs)}"
                print(f"\n{line}")

            # Print next steps
            next_steps = result.get("next_steps") or []
            if next_steps:
                print("\nNext steps:")
                for s in next_steps[:5]:
                    print(f"  • {s}")

            # Print citations
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
                        print(f"  {i}. “{quote}” — {title}: {ref}{where} (score: {score:.3f})")
                    else:
                        print(f"  {i}. {title}: {ref}{where} (score: {score:.3f})")

            print(f"\nVerdict: {verdict} | Confidence: {confidence:.2f}")
            print("-" * 30)

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break


if __name__ == "__main__":
    main()