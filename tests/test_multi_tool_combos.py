#!/usr/bin/env python3
"""Test all multi-tool combinations."""

import json
import time
import requests
import subprocess

BASE_URL = "http://localhost:8000"

def get_token():
    r = requests.post(f"{BASE_URL}/login", data={"username": "admin", "password": "password123"})
    return r.json()["access_token"]

def query(token, question, selected_doc_ids=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"question": question}
    if selected_doc_ids:
        payload["selected_doc_ids"] = selected_doc_ids
    r = requests.post(f"{BASE_URL}/query", headers=headers, json=payload)
    data = r.json()
    # Normalize None fields
    if data.get("citations") is None:
        data["citations"] = []
    if data.get("document_slots") is None:
        data["document_slots"] = []
    if data.get("resolved_documents") is None:
        data["resolved_documents"] = []
    if data.get("tool_selection") is None:
        data["tool_selection"] = {}
    return data

def clear_history():
    subprocess.run([
        "/home/mujtaba/postgresql/bin/psql", "-U", "codex_admin", "-d", "codex_db", "-c",
        """DELETE FROM citations WHERE answer_id IN (SELECT answers.id FROM answers JOIN queries ON answers.query_id = queries.id WHERE queries.user_id = 'admin');
           DELETE FROM answers WHERE query_id IN (SELECT id FROM queries WHERE user_id = 'admin');
           DELETE FROM queries WHERE user_id = 'admin';"""
    ], capture_output=True)

def safe_get(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d

def detect_sections(answer):
    if not answer:
        return []
    sections = []
    a = answer.upper()
    if "SUMMARY" in a or "SUMMAR" in a: sections.append("summary")
    if "APPROVAL" in a or "AUTHORITY" in a: sections.append("approval")
    if "CONFLICT" in a or "CONTRADICTION" in a or "DISCREPANC" in a: sections.append("conflict")
    if "COMPLIANCE" in a or "RISK" in a: sections.append("compliance")
    if "PROCEDURE" in a or "PROCESS" in a or "STEP" in a: sections.append("procedure")
    return sections

def run_test(token, name, question):
    print(f"\n{'='*70}")
    print(f"TEST: {name}")
    print(f"{'='*70}")
    print(f"Q: {question}")

    # Phase 1
    p1 = query(token, question)
    tools = safe_get(p1, "tool_selection", "tools", default=[])
    verdict = safe_get(p1, "verdict", default="")
    doc_slots = safe_get(p1, "document_slots", default=[])
    resolved = safe_get(p1, "resolved_documents", default=[])
    slots = len(doc_slots)

    print(f"\n  Phase 1: tools={tools}, verdict={verdict}, slots={slots}")

    if verdict == "pending_selection" and slots > 0 and resolved:
        doc_ids = [d["id"] for d in resolved if d.get("selected")]
        if not doc_ids:
            doc_ids = [resolved[0]["id"]]

        # Phase 2
        p2 = query(token, question, selected_doc_ids=doc_ids)
        v2 = safe_get(p2, "verdict", default="")
        c2 = safe_get(p2, "confidence", default=0)
        ans2 = safe_get(p2, "answer", default="")
        cite2 = len(safe_get(p2, "citations", default=[]))

        sections2 = detect_sections(ans2)
        print(f"  Phase 2: verdict={v2}, confidence={c2:.1f}, citations={cite2}")
        print(f"  Sections: {sections2}")
        print(f"  Answer length: {len(ans2)}")
        print(f"  Preview: {ans2[:400]}...")

        # Follow-up (same question again)
        print(f"\n  --- Follow-up test ---")
        time.sleep(1)
        fu = query(token, question)
        fu_tools = safe_get(fu, "tool_selection", "tools", default=[])
        fu_verdict = safe_get(fu, "verdict", default="")
        fu_conf = safe_get(fu, "confidence", default=0)
        fu_ans = safe_get(fu, "answer", default="")
        fu_cite = len(safe_get(fu, "citations", default=[]))

        fu_sections = detect_sections(fu_ans)
        print(f"  Follow-up: tools={fu_tools}, verdict={fu_verdict}, confidence={fu_conf:.1f}, citations={fu_cite}")
        print(f"  Sections: {fu_sections}")
        print(f"  Answer length: {len(fu_ans)}")
        print(f"  Preview: {fu_ans[:400]}...")

        clear_history()
        time.sleep(0.5)

        # Pass criteria: Phase 2 has multiple sections from different tools
        p2_pass = len(sections2) >= 2
        passed = p2_pass

        return {
            "name": name,
            "pass": passed,
            "p2_sections": sections2,
            "p2_pass": p2_pass,
            "fu_tools": fu_tools,
            "fu_sections": fu_sections,
        }
    else:
        # No doc selection needed
        ans = safe_get(p1, "answer", default="")
        sections = detect_sections(ans)
        print(f"  Direct answer: sections={sections}, len={len(ans)}")
        print(f"  Preview: {ans[:400]}...")
        clear_history()
        time.sleep(0.5)
        return {"name": name, "pass": len(ans) > 50, "p2_sections": sections, "fu_tools": [], "fu_sections": []}


TESTS = [
    ("conflict + summary", "Compare the Backup Policy and the Disaster Recovery Policy, and summarize the Disaster Recovery Policy"),
    ("conflict + approval", "Compare the Backup Policy and the Disaster Recovery Policy, and who approves disaster recovery procedures"),
    ("summary + approval", "Summarize the Disaster Recovery Policy and who approves disaster recovery procedures"),
    ("conflict + summary + approval", "Compare the Backup Policy and the Disaster Recovery Policy, summarize the Disaster Recovery Policy, and who approves disaster recovery procedures"),
    ("conflict + summary + compliance", "Compare the Backup Policy and the Disaster Recovery Policy, summarize the Disaster Recovery Policy, and what are the compliance risks"),
    ("approval + compliance", "Who approves disaster recovery procedures and what are the compliance risks"),
    ("conflict + approval + compliance", "Compare the Backup Policy and the Disaster Recovery Policy, who approves disaster recovery, and what are the compliance risks"),
]

if __name__ == "__main__":
    print("=" * 70)
    print("MULTI-TOOL COMBINATION TESTS")
    print("=" * 70)

    token = get_token()
    clear_history()
    time.sleep(1)

    results = []
    for name, question in TESTS:
        try:
            result = run_test(token, name, question)
            results.append(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ERROR: {e}")
            results.append({"name": name, "pass": False, "error": str(e)})
            clear_history()
            time.sleep(0.5)

    # Summary
    print(f"\n{'='*70}")
    print("RESULTS SUMMARY")
    print(f"{'='*70}")
    for r in results:
        status = "PASS" if r.get("pass") else "FAIL"
        name = r.get("name", "unknown")
        p2s = r.get("p2_sections", [])
        fut = r.get("fu_tools", [])
        fus = r.get("fu_sections", [])
        print(f"  [{status}] {name}")
        print(f"         Phase 2 sections: {p2s}")
        print(f"         Follow-up tools: {fut}  sections: {fus}")
        if r.get("error"):
            print(f"         Error: {r['error']}")

    passed = sum(1 for r in results if r.get("pass"))
    total = len(results)
    print(f"\n  Total: {passed}/{total} passed")
