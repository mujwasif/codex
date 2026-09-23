#!/usr/bin/env python3
"""Test 10 queries: mix of single-tool and multi-tool with conflicts."""

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
    return r.json()

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
    if "APPROVAL" in a or "AUTHORITY" in a or "APPROVE" in a: sections.append("approval")
    if "CONFLICT" in a or "CONTRADICTION" in a or "DISCREPANC" in a: sections.append("conflict")
    if "COMPLIANCE" in a or "RISK" in a: sections.append("compliance")
    if "PROCEDURE" in a or "PROCESS" in a or "STEP" in a: sections.append("procedure")
    return sections


TESTS = [
    # Single tool tests
    ("single: approval", "Who approves disaster recovery procedures?"),
    ("single: summary", "Summarize the Disaster Recovery Policy"),
    ("single: compliance", "What are the compliance risks for data handling?"),
    ("single: procedure", "What is the vendor onboarding process?"),
    ("single: conflict (type_2b)", "Are there any conflicts in the Backup Policy?"),

    # Multi-tool with conflict
    ("conflict + summary", "Compare the Backup Policy and the Disaster Recovery Policy, and summarize the Disaster Recovery Policy"),
    ("conflict + approval", "Compare the Backup Policy and the Disaster Recovery Policy, and who approves disaster recovery procedures"),
    ("conflict + summary + approval", "Compare the Backup Policy and the Disaster Recovery Policy, summarize the Disaster Recovery Policy, and who approves disaster recovery procedures"),
    ("conflict + summary + compliance", "Compare the Backup Policy and the Disaster Recovery Policy, summarize the Disaster Recovery Policy, and what are the compliance risks"),
    ("conflict + approval + compliance", "Compare the Backup Policy and the Disaster Recovery Policy, who approves disaster recovery, and what are the compliance risks"),
]

if __name__ == "__main__":
    print("=" * 70)
    print("10-QUERY TEST: Single + Multi-Tool with Conflicts")
    print("=" * 70)

    token = get_token()
    clear_history()
    time.sleep(1)

    results = []
    for name, question in TESTS:
        print(f"\n{'='*70}")
        print(f"TEST: {name}")
        print(f"{'='*70}")
        print(f"Q: {question}")

        try:
            p1 = query(token, question)
            tools = safe_get(p1, "tool_selection", "tools", default=[])
            verdict = safe_get(p1, "verdict", default="")
            doc_slots = safe_get(p1, "document_slots", default=[]) or []
            resolved = safe_get(p1, "resolved_documents", default=[]) or []
            slots = len(doc_slots)

            print(f"\n  Phase 1: tools={tools}, verdict={verdict}, slots={slots}")

            if verdict == "pending_selection" and slots > 0 and resolved:
                doc_ids = [d["id"] for d in resolved if d.get("selected")]
                if not doc_ids:
                    doc_ids = [resolved[0]["id"]]

                p2 = query(token, question, selected_doc_ids=doc_ids)
                v2 = safe_get(p2, "verdict", default="")
                c2 = safe_get(p2, "confidence", default=0)
                ans2 = safe_get(p2, "answer", default="")
                cite2 = len(safe_get(p2, "citations", default=[]))
                sections2 = detect_sections(ans2)

                print(f"  Phase 2: verdict={v2}, confidence={c2:.1f}, citations={cite2}")
                print(f"  Sections: {sections2}")
                print(f"  Answer length: {len(ans2)} chars, {len(ans2.split())} words")
                print(f"\n  --- FULL ANSWER ---")
                print(ans2)
                print(f"  --- END ---")

                results.append({
                    "name": name, "pass": len(sections2) >= 1 and len(ans2) > 100,
                    "sections": sections2, "chars": len(ans2), "words": len(ans2.split()),
                    "citations": cite2,
                })
            else:
                ans = safe_get(p1, "answer", default="")
                c = safe_get(p1, "confidence", default=0)
                cite = len(safe_get(p1, "citations", default=[]))
                sections = detect_sections(ans)

                print(f"  Direct: verdict={verdict}, confidence={c:.1f}, citations={cite}")
                print(f"  Sections: {sections}")
                print(f"  Answer length: {len(ans)} chars, {len(ans.split())} words")
                print(f"\n  --- FULL ANSWER ---")
                print(ans)
                print(f"  --- END ---")

                results.append({
                    "name": name, "pass": len(ans) > 50,
                    "sections": sections, "chars": len(ans), "words": len(ans.split()),
                    "citations": cite,
                })

            clear_history()
            time.sleep(0.5)

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ERROR: {e}")
            results.append({"name": name, "pass": False, "sections": [], "chars": 0, "words": 0, "citations": 0})
            clear_history()
            time.sleep(0.5)

    # Summary
    print(f"\n{'='*70}")
    print("RESULTS SUMMARY")
    print(f"{'='*70}")
    for r in results:
        status = "PASS" if r.get("pass") else "FAIL"
        print(f"  [{status}] {r['name']}")
        print(f"         sections={r.get('sections', [])} | {r.get('chars', 0)} chars | {r.get('words', 0)} words | {r.get('citations', 0)} citations")

    passed = sum(1 for r in results if r.get("pass"))
    total = len(results)
    avg_words = sum(r.get("words", 0) for r in results) / total
    avg_chars = sum(r.get("chars", 0) for r in results) / total
    print(f"\n  Total: {passed}/{total} passed")
    print(f"  Average answer: {avg_chars:.0f} chars, {avg_words:.0f} words")
