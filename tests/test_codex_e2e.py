#!/usr/bin/env python3
"""
End-to-end tests for Codex Policy Intelligence Engine.
140 tests across 6 intent categories: conversational, general, procedure,
compliance, approval, and conflict (type_1, type_2, type_2b).

Run: PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_codex_e2e.py
"""

import requests
import json
import sys
import time

API = "http://127.0.0.1:8000"
TOKEN = None
PASSED = 0
FAILED = 0
ERRORS = []


def login():
    global TOKEN
    r = requests.post(f"{API}/login", data={"username": "admin", "password": "password123"}, timeout=10)
    body = r.json()
    TOKEN = body.get("access_token")
    if not TOKEN:
        print(f"FATAL: Login failed: {body}")
        sys.exit(1)


def query(question, timeout=120):
    H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    r = requests.post(f"{API}/query", headers=H, json={"question": question, "search_mode": "hybrid"}, timeout=timeout)
    return r.json()


def check(label, condition):
    global PASSED, FAILED
    if condition:
        print(f"    ✅ {label}")
        PASSED += 1
    else:
        print(f"    ❌ {label}")
        FAILED += 1
        ERRORS.append(label)


def get_doc_phrases(data):
    """Extract doc_phrases from resolved_documents + document_slots."""
    phrases = []
    for s in (data.get("document_slots") or []):
        phrases.append(s.get("phrase", ""))
    return phrases


def test_category(name, tests):
    """Run a list of (label, question, expected_intent, expected_phrases) tests."""
    print(f"\n=== {name} ({len(tests)} tests) ===")
    for label, question, expected_intent, expected_phrases in tests:
        print(f"\n  [{label}] \"{question[:60]}{'...' if len(question)>60 else ''}\"")
        try:
            t0 = time.time()
            data = query(question)
            elapsed = time.time() - t0
            intent = data.get("reasoning", {}).get("intent", "?")
            verdict = data.get("verdict", "?")
            confidence = data.get("reasoning", {}).get("intent_confidence", 0)
            actual_phrases = get_doc_phrases(data)
            answer = data.get("answer") or ""

            check(f"HTTP OK (no crash)", "detail" not in data)
            check(f"Intent={expected_intent} (got {intent})", intent == expected_intent)

            if expected_phrases is not None and len(expected_phrases) > 0:
                # Only check doc phrases if explicitly specified (non-empty list)
                all_ok = True
                for ep in expected_phrases:
                    found = any(ep.lower() in ap.lower() for ap in actual_phrases)
                    if not found:
                        all_ok = False
                        break
                check(f"Doc phrases contain {expected_phrases} (got {actual_phrases})", all_ok)
            elif expected_phrases is not None and len(expected_phrases) == 0:
                # Empty expected phrases means we don't check doc phrases at all
                pass

            check(f"Response time {elapsed:.0f}s < 120s", elapsed < 120)

        except Exception as e:
            check(f"No exception (got {type(e).__name__}: {e})", False)


def test_two_phase_flow():
    """Test the two-phase conflict flow: Phase 1 (pending) + Phase 2 (analysis)."""
    print(f"\n=== TWO-PHASE FLOW (4 tests) ===")

    # Test 1: type_2 → pending_selection with 2 slots
    print(f"\n  [T2-P1] type_2 query → pending_selection")
    try:
        data = query("Compare the Backup Policy and the Data Retention Policy")
        intent = data.get("reasoning", {}).get("intent", "?")
        verdict = data.get("verdict", "?")
        slots = data.get("document_slots") or []
        check(f"Intent=conflict (got {intent})", intent == "conflict")
        check(f"Verdict=pending_selection (got {verdict})", verdict == "pending_selection")
        check(f"2 document slots (got {len(slots)})", len(slots) == 2)
        if len(slots) == 2:
            c0 = slots[0].get("candidates", [])
            c1 = slots[1].get("candidates", [])
            check(f"Slot 1 has 5 candidates (got {len(c0)})", len(c0) == 5)
            check(f"Slot 2 has 5 candidates (got {len(c1)})", len(c1) == 5)
            if c0:
                check(f"Slot 1 top candidate selected=True", c0[0].get("selected") is True)
            check(f"Candidate has id field", c0[0].get("id") is not None if c0 else False)
            check(f"Candidate has title field", c0[0].get("title") is not None if c0 else False)
            check(f"Candidate has similarity field", c0[0].get("similarity") is not None if c0 else False)
    except Exception as e:
        check(f"No exception (got {e})", False)

    # Test 2: type_2b → pending_selection with 1 slot
    print(f"\n  [T2-P2] type_2b query → pending_selection")
    try:
        data = query("Does the Incident Response Plan conflict with anything?")
        intent = data.get("reasoning", {}).get("intent", "?")
        verdict = data.get("verdict", "?")
        slots = data.get("document_slots") or []
        check(f"Intent=conflict (got {intent})", intent == "conflict")
        check(f"Verdict=pending_selection (got {verdict})", verdict == "pending_selection")
        check(f"1 document slot (got {len(slots)})", len(slots) == 1)
        if len(slots) == 1:
            c0 = slots[0].get("candidates", [])
            check(f"Slot has 5 candidates (got {len(c0)})", len(c0) == 5)
    except Exception as e:
        check(f"No exception (got {e})", False)

    # Test 3: type_1 → runs to completion, no slots
    print(f"\n  [T2-P3] type_1 query → runs to completion")
    try:
        data = query("Are there conflicting rules about breach notification timelines?")
        intent = data.get("reasoning", {}).get("intent", "?")
        verdict = data.get("verdict", "?")
        slots = data.get("document_slots") or []
        check(f"Intent=conflict (got {intent})", intent == "conflict")
        check(f"Verdict is NOT pending_selection (got {verdict})", verdict != "pending_selection")
        check(f"No document slots (got {len(slots)})", len(slots) == 0)
    except Exception as e:
        check(f"No exception (got {e})", False)

    # Test 4: general + doc phrase → resolved_documents populated
    print(f"\n  [T2-P4] general+doc → resolved_documents populated")
    try:
        data = query("Tell me about the Remote Access Policy")
        intent = data.get("reasoning", {}).get("intent", "?")
        resolved = data.get("resolved_documents") or []
        check(f"Intent=general (got {intent})", intent == "general")
        check(f"resolved_documents not empty (got {len(resolved)})", len(resolved) > 0)
        if resolved:
            check(f"resolved doc has title", resolved[0].get("title") is not None)
            check(f"resolved doc has id", resolved[0].get("id") is not None)
    except Exception as e:
        check(f"No exception (got {e})", False)


def test_doc_resolution_accuracy():
    """Verify that resolved documents match expected titles."""
    print(f"\n=== DOC RESOLUTION ACCURACY (6 tests) ===")

    tests = [
        ("Backup Policy → Backup", "What does the Backup Policy require?", "Backup"),
        ("IR Plan → Incident", "Does the Incident Response Plan conflict with anything?", "Incident"),
        ("BYOD Policy → BYOD/Bring", "What does the BYOD Policy require?", "BYOD"),
        ("Data Retention → Retent", "Which documents contradict the Data Retention Policy?", "Retent"),
        ("Change Management → Change", "Compare the Change Management Policy and the Incident Response Plan", "Change"),
        ("Remote Access → Remote", "Tell me about the Remote Access Policy", "Remote"),
    ]

    for label, question, expected_substr in tests:
        print(f"\n  [{label}] \"{question[:50]}\"")
        try:
            data = query(question)
            slots = data.get("document_slots") or []
            resolved = data.get("resolved_documents") or []

            if slots:
                titles = [s.get("phrase", "") for s in slots]
                found = any(expected_substr.lower() in t.lower() for t in titles)
                check(f"Slot phrase contains '{expected_substr}' (got {titles})", found)
            elif resolved:
                titles = [r.get("title", "") for r in resolved]
                found = any(expected_substr.lower() in t.lower() for t in titles)
                check(f"Resolved doc contains '{expected_substr}' (got {titles})", found)
            else:
                check(f"Has slots or resolved docs", False)

        except Exception as e:
            check(f"No exception (got {e})", False)


def test_answer_quality():
    """Verify answers contain substantive content."""
    print(f"\n=== ANSWER QUALITY (5 tests) ===")

    tests = [
        ("BYOD answer substantive", "What does the BYOD Policy require?", "BYOD"),
        ("Procedure answer substantive", "How do I install new software?", "software"),
        ("Approval answer substantive", "Who can approve a purchase over $10,000?", None),
        ("Greeting answer", "Hello", None),
        ("General answer substantive", "What does the Backup Policy require?", "backup"),
    ]

    for label, question, keyword in tests:
        print(f"\n  [{label}] \"{question[:50]}\"")
        try:
            data = query(question)
            answer = data.get("answer") or ""
            check(f"Answer not empty ({len(answer)} chars)", len(answer) > 0)
            check(f"Answer not error ({answer[:60]})", "Error processing" not in answer)
            if keyword:
                check(f"Answer mentions '{keyword}'", keyword.lower() in answer.lower())

        except Exception as e:
            check(f"No exception (got {e})", False)


def main():
    global PASSED, FAILED

    print("=" * 60)
    print("CODEX E2E TEST SUITE — 140+ intent classification tests")
    print("=" * 60)

    login()
    print(f"Logged in as admin")

    # ── Category 1: CONVERSATIONAL (20 tests) ──
    conv_tests = [
        (f"C{i+1}", q, "conversational", [])
        for i, q in enumerate([
            "Hello", "Hi, how are you?", "Thanks for the help", "Good morning",
            "Good afternoon", "Bye", "Thank you", "What's up", "Hey there",
            "See you later", "Good evening", "How's it going", "Much appreciated",
            "Have a good day", "Thanks a lot", "OK", "Cool", "Sure",
            "That's great", "Wonderful",
        ])
    ]
    test_category("CONVERSATIONAL", conv_tests)

    # ── Category 2: GENERAL (20 tests) ──
    gen_tests = [
        (f"G{i+1}", q, "general", phrases)
        for i, (q, phrases) in enumerate([
            ("What does the BYOD Policy require?", ["BYOD Policy"]),
            ("Tell me about the Remote Access Policy", ["Remote Access Policy"]),
            ("What does the Backup Policy require?", ["Backup Policy"]),
            ("What is the Risk Management Policy about?", ["Risk Management Policy"]),
            ("Summarize the Business Continuity and Disaster Recovery Plan", ["Business Continuity"]),
            ("What does the Incident Response Plan cover?", ["Incident Response Plan"]),
            ("What are the Clear Desk requirements?", ["Clear Desk"]),
            ("What does the Log Management Policy require?", ["Log Management Policy"]),
            ("What does the Change Management Policy cover?", ["Change Management Policy"]),
            ("What does the Data Retention Policy require?", ["Data Retention"]),
            ("Tell me about the Software Installation Policy", ["Software Installation"]),
            ("What does the Physical Security Policy say?", ["Physical Security"]),
            ("What does the Removable Media Policy cover?", ["Removable Media"]),
            ("What is the Human Resource Security Policy about?", ["Human Resource Security"]),
            ("Tell me about the Backup Policy", ["Backup Policy"]),
            ("What does the BYOD Policy cover?", ["BYOD Policy"]),
            ("Describe the Data Handling Policy", ["Data Handling"]),
            ("What is the Amendment Notice about?", ["Amendment Notice"]),
            ("Tell me about the Tax Circular", ["Tax Circular"]),
            ("What does the Data Protection Directive cover?", ["Data Protection"]),
        ])
    ]
    test_category("GENERAL", gen_tests)

    # ── Category 3: PROCEDURE (20 tests) ──
    proc_tests = [
        (f"P{i+1}", q, "procedure", [])
        for i, q in enumerate([
            "How do I install new software on my work computer?",
            "What should I do if I lose my access card?",
            "Walk me through what to do if a laptop is stolen",
            "What are the steps of the change management process?",
            "What happens when both internet providers are unavailable?",
            "How is a new hire's laptop provisioned?",
            "What should I do if I suspect a security incident?",
            "How do I request an exception to use removable media?",
            "How do I request remote access from outside?",
            "How do I lock my screen when leaving my desk?",
            "What happens after I submit a change request?",
            "How do I report a security incident?",
            "What should I do if my account is locked?",
            "How do I set up a new device for work?",
            "What steps do I follow during a disaster recovery?",
            "How do I backup my work files?",
            "What is the process for offboarding an employee?",
            "How do I escalate a security concern?",
            "What should I do if I find confidential data exposed?",
            "How do I request access to a new system?",
        ])
    ]
    test_category("PROCEDURE", proc_tests)

    # ── Category 4: COMPLIANCE (20 tests) ──
    comp_tests = [
        (f"C{i+1}", q, "compliance", [])
        for i, q in enumerate([
            "What data breach notification timeline does the Data Protection Directive require?",
            "What are the penalties for non-compliance?",
            "Are audit/log retention requirements defined?",
            "What happens if an employee violates a security policy?",
            "Is encryption of confidential data mandatory?",
            "Are backup integrity tests a mandatory requirement?",
            "What are the retention requirements for payroll?",
            "What DPIA requirements apply?",
            "What are the cross-border data transfer requirements?",
            "What is the tech sector tax classification?",
            "Are employees required to complete security training?",
            "Is multi-factor authentication mandatory?",
            "What are the data classification requirements?",
            "Are background checks required for employees?",
            "What is the mandatory incident reporting timeline?",
            "Is annual security awareness training required?",
            "What are the compliance obligations for remote workers?",
            "Are vendor security assessments mandatory?",
            "What regulatory frameworks apply to this organization?",
            "What are the penalties for data breaches?",
        ])
    ]
    test_category("COMPLIANCE", comp_tests)

    # ── Category 5: APPROVAL (20 tests) ──
    appr_tests = [
        (f"A{i+1}", q, "approval", None)
        for i, q in enumerate([
            "Who can approve a purchase over $10,000?",
            "Who authorizes emergency changes?",
            "Who approves the post-incident report?",
            "Who approves client/media notification?",
            "Who approves new software installation?",
            "Who authorizes remote access for contractors?",
            "Who approves the risk scores?",
            "Who must approve data purging?",
            "Who approves bringing a personal device to work?",
            "Who signs off on business continuity testing?",
            "Who approves the change management process?",
            "Who authorizes access to sensitive data?",
            "Who approves the disaster recovery plan?",
            "Who can approve policy exceptions?",
            "Who approves new hire onboarding?",
            "Who signs off on the log management policy?",
            "Who approves the physical security assessment?",
            "Who authorizes data retention changes?",
            "Who approves the backup restoration test?",
            "Who can authorize emergency access to systems?",
        ])
    ]
    test_category("APPROVAL", appr_tests)

    # ── Category 6: CONFLICT (40 tests) ──

    # Type 1 (10 tests) — no doc_phrases expected
    con1_tests = [
        (f"CON1-{i+1}", q, "conflict", [])
        for i, q in enumerate([
            "Are there conflicting rules about breach notification timelines?",
            "Do any policies disagree about log retention periods?",
            "Are there conflicting requirements about USB device usage?",
            "Is there a conflict about policy review frequency?",
            "Are there conflicting rules about data retention across all policies?",
            "Do any policies disagree on access right review cadence?",
            "Are there conflicting requirements about handling security incidents?",
            "Is there a conflict about data handling during offboarding?",
            "Are there contradictions about password complexity requirements?",
            "Do any policies conflict on encryption requirements?",
        ])
    ]
    test_category("CONFLICT TYPE 1 (Clause-vs-Corpus)", con1_tests)

    # Type 2 (15 tests) — 2 doc_phrases expected
    con2_tests = [
        (f"CON2-{i+1}", q, "conflict", phrases)
        for i, (q, phrases) in enumerate([
            ("Compare the Backup Policy and the Data Retention Policy", ["Backup Policy", "Data Retention Policy"]),
            ("Are there conflicts between the BYOD Policy and the Remote Access Policy?", ["BYOD Policy", "Remote Access Policy"]),
            ("Compare the Clear Desk Policy and the Physical Security Policy", ["Clear Desk Policy", "Physical Security Policy"]),
            ("Does the Change Management Policy contradict the Incident Response Plan?", ["Change Management Policy", "Incident Response Plan"]),
            ("Compare the Log Management Policy and the Backup Policy", ["Log Management Policy", "Backup Policy"]),
            ("Are there conflicts between the Risk Management Policy and the BCP?", ["Risk Management Policy", "Business Continuity"]),
            ("How do the Software Installation Policy and BYOD Policy differ?", ["Software Installation Policy", "BYOD Policy"]),
            ("Compare the Human Resource Security Policy and the Data Retention Policy", ["Human Resource Security Policy", "Data Retention Policy"]),
            ("Are there contradictions between the Removable Media Policy and Physical Security?", ["Removable Media Policy", "Physical Security"]),
            ("Compare the Change Management Policy and the Risk Management Policy", ["Change Management Policy", "Risk Management Policy"]),
            ("How do the Backup Policy and the BCP differ on recovery?", ["Backup Policy", "Business Continuity"]),
            ("Are there conflicts between the Remote Access Policy and the Log Management Policy?", ["Remote Access Policy", "Log Management Policy"]),
            ("Compare the Incident Response Plan and the BCP", ["Incident Response Plan", "Business Continuity"]),
            ("How do the BYOD Policy and Software Installation Policy differ?", ["BYOD Policy", "Software Installation Policy"]),
            ("Are there contradictions between Clear Desk and Remote Access?", ["Clear Desk", "Remote Access"]),
        ])
    ]
    test_category("CONFLICT TYPE 2 (Document-vs-Document)", con2_tests)

    # Type 2b (15 tests) — 1 doc_phrase expected
    con2b_tests = [
        (f"CON2B-{i+1}", q, "conflict", [phrase])
        for i, (q, phrase) in enumerate([
            ("Does the Incident Response Plan conflict with any other policy?", "Incident Response Plan"),
            ("Which documents contradict the Data Retention Policy?", "Data Retention Policy"),
            ("Are there policies that conflict with the BYOD Policy?", "BYOD Policy"),
            ("Does the Risk Management Policy conflict with anything?", "Risk Management Policy"),
            ("Which other policies conflict with the Software Installation Policy?", "Software Installation Policy"),
            ("Does the Backup Policy conflict with any other policy?", "Backup Policy"),
            ("Which documents disagree with the Remote Access Policy?", "Remote Access Policy"),
            ("Are there policies that contradict the Change Management Policy?", "Change Management Policy"),
            ("Does the Log Management Policy conflict with anything?", "Log Management Policy"),
            ("Which policies disagree with the Physical Security Policy?", "Physical Security Policy"),
            ("Does the Clear Desk Policy conflict with anything?", "Clear Desk Policy"),
            ("Which documents contradict the Human Resource Security Policy?", "Human Resource Security Policy"),
            ("Does the BCP conflict with any other policy?", "Business Continuity"),
            ("Are there policies that conflict with the Removable Media Policy?", "Removable Media Policy"),
            ("Does the Amendment Notice contradict the Data Protection Directive?", "Amendment Notice"),
        ])
    ]
    test_category("CONFLICT TYPE 2b (Document-vs-Corpus)", con2b_tests)

    # ── Two-Phase Flow Tests (4 tests) ──
    test_two_phase_flow()

    # ── Doc Resolution Accuracy (6 tests) ──
    test_doc_resolution_accuracy()

    # ── Answer Quality (5 tests) ──
    test_answer_quality()

    # ── Summary ──
    total = PASSED + FAILED
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {PASSED}/{total} passed ({FAILED} failed)")
    if ERRORS:
        print(f"\nFailed tests:")
        for e in ERRORS:
            print(f"  ❌ {e}")
    print(f"{'=' * 60}")

    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
