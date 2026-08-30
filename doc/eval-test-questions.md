# Intent Test Questions — Codex Policy Intelligence

92 grounded evaluation questions across 10 sections: 10 APPROVAL, 40 CONFLICT (10 core + 4 Type 1 + 6 Type 2 + 6 Type 2b + 12 Guardrails + 2 negative), 10 COMPLIANCE, 10 PROCEDURE, 10 GENERAL, 14 DOCUMENT RESOLUTION + TWO-PHASE FLOW.

Grounded in the 18 archive policy/plan docs in `archive/` (1,442 chunks). All document titles below are actual filenames in the PostgreSQL `documents` table.

---

## 1. APPROVAL (who approves / authorizes)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | Who approves changes to company policies? | Change Management: ISMS Manager + CAB review |
| 2 | Who authorizes emergency changes outside the standard process? | Change Management: emergency CAB, documented retroactively |
| 3 | Who approves the post-incident report? | Incident Response: IR Officer + CEO for severe incidents |
| 4 | Who approves client/media notification during an incident? | Incident Response: CEO & PR team approval required |
| 5 | Who approves new software installation requests? | Software Installation: management approval before installation |
| 6 | Who authorizes remote access for contractors? | Remote Access: ISMS Manager approval required |
| 7 | Who approves the risk scores in the risk assessment? | Risk Management: Risk Owner approves with the ISMS Manager |
| 8 | Who must approve data purging after contract termination? | Data Retention: Project Manager + COO |
| 9 | Who approves bringing a personal device to work? | BYOD: management approval + security package verification |
| 10 | Who signs off on business continuity plan testing? | BCP/DR: COO approval for plan activation and testing |

## 2. CONFLICT (contradictions / supersede)

Three conflict detection types triggered via `/query`:
- **Type 1 (Clause-vs-Corpus):** 0 doc phrases → generic search + clause-vs-corpus expansion
- **Type 2 (Document-vs-Document):** 2 doc phrases → per-slot pgvector resolution → user selects 2 docs → compare_document_chunks
- **Type 2b (Document-vs-Corpus):** 1 doc phrase → per-slot pgvector resolution → user selects 1 doc → detect_conflicting_documents

### Core conflict questions (1–10)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | Is there a conflict between Change Management and Incident Response on approval processes? | Change Mgmt: ISMS Manager + CAB; IR: CEO + PR for notification — different workflows, complementary |
| 2 | Are there clauses that conflict inside the corpus (as recorded in the graph)? | CONFLICTS_WITH edges exist between Clause nodes |
| 3 | Do any documents supersede an older version? | Amendment Notice (03) modifies Data Protection Directive (01): breach period 72h→48h |
| 4 | Do Backup and Data Retention policies contradict about retention periods? | Backup: yearly integrity test; Retention: 10yr payroll, 90d logs — should be no conflict (negative test) |
| 5 | Do Removable Media rules conflict with Physical Security about USB devices? | Removable Media bans most USB; Physical Security may have exceptions — check for clash |
| 6 | Is there a conflict between Clear Desk and Remote Access about screen lock requirements? | Both require locking — should return no conflict (negative test) |
| 7 | Do Change Management and Data Retention disagree on how long to keep change records? | Change Mgmt: document all changes; Retention: define periods — check for tension |
| 8 | Is there a conflict between BYOD and Remote Access about how personal devices connect? | BYOD: guest network only; Remote Access: VPN — complementary, no conflict |
| 9 | Which policy takes precedence if Log Management and Incident Response disagree on log retention during an incident? | No explicit precedence rule → expected "insufficient basis"/abstain |
| 10 | Are the physical security requirements in Clear Desk consistent with the Physical Security Policy? | Both about desk/screen security — should be consistent (negative test) |

### Type 1: Clause-vs-Corpus (11–14)

Questions that find similar clauses across all documents and check for contradictions. Expected results should distinguish confirmed conflicts, possible conflicts, complementary scope, and insufficient context.

| # | Question | Expected basis |
|---|----------|----------------|
| 11 | Are there conflicting rules about data retention across all policies? | Data Retention (10yr payroll, 90d logs) vs Log Mgmt (90d logs) vs BCP (yearly integrity) — may surface retention tension |
| 12 | Do any policies disagree on how often access rights should be reviewed? | HR Security may mention review cadence — check against other docs; likely no conflict (negative test) |
| 13 | Are there conflicting requirements about handling security incidents? | IR Plan (4 severity levels, notification chains) vs HR Security (disciplinary) vs Change Mgmt (emergency changes) — complementary procedures |
| 14 | Is there a conflict between policies about data handling during offboarding? | HR Security vs Data Retention vs Clear Desk — check lifecycle tension during employee departure |

### Type 2: Document-vs-Document (15–20)

Questions that name 2 specific documents. These trigger the **two-phase flow**: Phase 1 returns document_slots with top 5 candidates per slot; Phase 2 runs analysis on user-selected docs.

| # | Question | Expected doc pair | Conflict? |
|---|----------|-------------------|-----------|
| 15 | Are there conflicts between the Backup Policy and the Data Retention Policy? | UnderDefense MAXI - Backup policy.docx vs UnderDefense MAXI - Data retention and destruction policy.docx | No (negative test — complementary) |
| 16 | Compare the Change Management Policy and the Incident Response Plan for contradictions | UnderDefense MAXI - Change management policy.docx vs UnderDefense MAXI - Incident response plan.docx | No (different workflows, complementary) |
| 17 | Are there contradictions between the Log Management Policy and the Backup Policy? | UnderDefense MAXI - Log management policy.docx vs UnderDefense MAXI - Backup policy.docx | Possible — log retention vs backup retention cadences |
| 18 | How do the Clear Desk Policy and the Physical Security Policy differ on workstation security? | UnderDefense MAXI - Clear desk and screen policy.docx vs UnderDefense MAXI - Physical security policy.docx | No (additive requirements) |
| 19 | Are there conflicts between the BYOD Policy and the Remote Access Policy? | UnderDefense MAXI - Bring your own device policy.docx vs UnderDefense MAXI - Remote access policy.docx | No (complementary — guest network vs VPN) |
| 20 | Compare the Risk Management Policy and the Business Continuity Plan on risk treatment | UnderDefense MAXI - Risk management policy.docx vs UnderDefense MAXI - Business continuity_Disaster recovery plan.docx | Possible — risk scoring vs recovery targets may overlap |

### Type 2b: Document-vs-Corpus (21–26)

Questions that name 1 document and ask which others conflict with it. These also trigger the **two-phase flow** with one slot.

| # | Question | Target doc | Expected findings |
|---|----------|-----------|-------------------|
| 21 | Which policies conflict with the Change Management Policy? | UnderDefense MAXI - Change management policy.docx | Likely no direct conflicts — complementary with IR, Risk |
| 22 | Which documents contradict the Data Retention Policy? | UnderDefense MAXI - Data retention and destruction policy.docx | Backup policy: different retention cadences possible |
| 23 | Are there any policies that conflict with the Incident Response Plan? | UnderDefense MAXI - Incident response plan.docx | HR Security: disciplinary process may differ from IR escalation |
| 24 | Which other policies disagree with the BYOD Policy on device security? | UnderDefense MAXI - Bring your own device policy.docx | Software Installation may have overlap on device management |
| 25 | Does the Log Management Policy conflict with anything in the corpus? | UnderDefense MAXI - Log management policy.docx | Backup policy: log backup vs log retention may have tension |
| 26 | Which documents disagree with the Remote Access Policy? | UnderDefense MAXI - Remote access policy.docx | BYOD: guest network vs VPN may have policy tension |

### Conflict guardrails (27–38)

Questions that exercise structured validation, access filtering, budget limits, and deduplication.

| # | Question | Expected basis |
|---|----------|----------------|
| 27 | Are there at least three distinct conflicts in the corpus involving mandatory retention periods? | Evaluate enough accessible candidates; return every validated conflict ranked by confidence |
| 28 | If only one policy pair conflicts about password rotation, can you identify the single conflict without inventing two more? | Return one validated conflict; do not fabricate findings to reach minimum target |
| 29 | Which rules look similar but are complementary because they apply to employees and administrators separately? | Different populations should produce `complementary_scope`, not a false conflict |
| 30 | Do conditional remote-access requirements conflict when one applies only outside the office and the other applies only to internal networks? | Separated applicability → `complementary_scope` or `no_conflict` |
| 31 | Do the password rules conflict when one policy says passwords must change every 90 days and another says every 60 days? | Same population, action, modality with incompatible thresholds → `confirmed_conflict` |
| 32 | Do a minimum seven-year retention rule and a maximum three-year retention rule conflict? | Comparator and threshold semantics → incompatible obligations |
| 33 | Is an older 90-day rule still in conflict with a newer active 60-day rule that explicitly supersedes it? | Expected `superseded` when version metadata establishes precedence |
| 34 | What happens when two related clauses do not identify their applicable population or precedence? | Expected `possible_conflict` or `insufficient_context`, not a definitive conflict |
| 35 | What happens when the conflict comparison budget prevents checking every retrieved candidate? | Expected inconclusive analysis with checked/unchecked counts; never report definitive absence |
| 36 | Can the same conflict returned by semantic analysis and version analysis appear twice? | Duplicate findings should merge using canonical pair key |
| 37 | Can a level-1 employee retrieve or receive a conflict involving a level-3 admin-only policy? | Access-level filtering must exclude unauthorized clauses from candidates, evidence, answers, and citations |
| 38 | Are there conflicting requirements about data breach notification timelines? | Amendment Notice (03) changed 72h→48h; Data Protection Directive (01) may still reference 72h — real version conflict |

## 3. COMPLIANCE (regulation, mandatory)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | What data breach notification timeline does the Data Protection Directive require? | 01 Directive: 72 hours (but 03 Amendment changed to 48 hours) |
| 2 | What are the penalties for non-compliance with data protection rules? | 01 Directive: sanctions and penalties defined; 03 Amendment: self-reporting 30% discount |
| 3 | Are audit/log data retention requirements defined? For how long? | Log Mgmt: 90 days; Data Retention: electronic system logs 90 days |
| 4 | What happens if an employee violates a security policy? | All policies: disciplinary consequences "in proportion to their violation" |
| 5 | Is encryption of confidential data at rest mandatory? | 01 Data Protection Directive: data classification and protection requirements |
| 6 | Are backup integrity tests a mandatory requirement? At what cadence? | Backup policy: integrity tested at least yearly |
| 7 | What are the retention requirements for payroll and client contracts? | Data Retention: payroll 10yr; NDA/MSA/SoW retained permanently |
| 8 | What DPIA requirements apply to high-risk data processing? | 01 Data Protection Directive: DPIA required; 03 Amendment: periodic review added |
| 9 | What are the cross-border data transfer requirements? | 01 Data Protection Directive: cross-border transfer restrictions and adequacy requirements |
| 10 | What is the tech sector tax classification for companies? | 02 Technology Sector Tax Circular: classification by turnover, CIT rates, VAT |

## 4. PROCEDURE (how to / steps / what should I do)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | What should I do if I lose my access card? | Physical Security: inform the HR Generalist |
| 2 | How do I lock my screen when leaving my desk? | Clear Desk: per-OS key combos (Win/macOS/Linux) |
| 3 | Walk me through what to do if a laptop is stolen or malware-infected | BCP/DR + Incident Response: immediate notify, revoke credentials, shut down, impact assessment |
| 4 | What are the steps of the standard change management process? | Change Management: request → scope → impact → approval → test → implement → document → communicate |
| 5 | What happens when both internet providers are unavailable? | BCP/DR: notify Asset Manager → COO fallback; local IT activates 3rd channel |
| 6 | How is a new hire's laptop provisioned? | BCP/DR: HR/PM ticket → COO specs → format/encrypt/install |
| 7 | What should I do if I suspect a security incident? | Incident Response: report promptly via chain; IR team gathered; log details |
| 8 | How do I request an exception to use removable media? | Removable Media: ask Management for individual exception |
| 9 | How do I request remote access from outside the office? | Remote Access: submit request, ISMS Manager approves, VPN client installed |
| 10 | How do I install new software on my work computer? | Software Installation: submit request → management approval → IT installs |

## 5. GENERAL (explain / tell me about / what is)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | What does the BYOD Policy require? | Guest network only, security package (EDR/MDM/encryption/SIEM), no client data |
| 2 | Tell me about the Remote Access Policy | VPN required, ISMS Manager approval, no split tunneling |
| 3 | What does the Backup Policy require? | Key system images, retention copies, on-site + off-site, yearly integrity test |
| 4 | What is the Risk Management Policy about? | Risk assessment, risk treatment, risk register, annual review |
| 5 | Summarize the Business Continuity and Disaster Recovery Plan | BC for 72h; tech recovery ≤8h; business recovery ≤4h; 4 scenarios |
| 6 | What does the Incident Response Plan cover? | Detection → containment → eradication → recovery → post-incident review; 4 severity levels |
| 7 | What are the Clear Desk and Screen Policy requirements? | Lock desks/screens, no sticky-note passwords, 5-min screensaver, log off at day's end |
| 8 | What does the Log Management Policy require? | All log sources → SIEM, admin/operator actions logged, NTP sync, 90-day retention |
| 9 | What does the Change Management Policy cover? | Request → scope → impact analysis → CAB approval → test → implement → document → communicate |
| 10 | What does the Data Retention and Destruction Policy require? | Retention periods by data type, secure destruction methods, documented approval for purging |

## 6. DOCUMENT RESOLUTION + TWO-PHASE FLOW

Tests the CoT classify_intent, per-slot pgvector resolution, and the two-phase pending_selection → selected_doc_ids flow.

### Phase 1: Classification + candidate resolution

| # | Test | Expected behavior |
|---|------|-------------------|
| 39 | "Compare Backup Policy vs Change Management" (conflict type_2) | Phase 1: verdict=pending_selection, 2 document_slots with top 5 candidates each |
| 40 | "Does the Incident Response Plan conflict with anything?" (conflict type_2b) | Phase 1: verdict=pending_selection, 1 document_slot with top 5 candidates |
| 41 | "Are there password conflicts?" (conflict type_1) | No doc phrases extracted → runs to completion, no pause |
| 42 | "Tell me about Remote Access" (general) | 1 doc phrase → resolved_documents in response, no document_slots |
| 43 | "Check any conflict between backup and change management" — CoT phrases | CoT extracts doc_phrases: ["backup", "change management"] or ["backup policy", "change management"] |
| 44 | RBAC: level-1 user asks "Compare BYOD and Clear Desk" (both level 3) | Slots may be empty or show only level-1 alternatives; BYOD/Clear Desk excluded by access filter |
| 45 | "Compare backup and incident response" — both named docs | 2 slots resolved; if both accessible, conflict_type=type_2 |
| 46 | CoT intent classification: "Who can approve a purchase over $10,000?" | intent=approval, conflict_type=null, doc_phrases=[] |
| 47 | CoT intent classification: "Hello, how are you?" | intent=conversational, conflict_type=null, doc_phrases=[] |
| 48 | Conflict type_2 but only 1 slot resolves → fallback to type_2b | If only 1 doc phrase finds matches, conflict_type adjusted to type_2b |

### Phase 2: User selects documents → conflict analysis

| # | Test | Expected behavior |
|---|------|-------------------|
| 49 | Phase 2: selected_doc_ids=["abc","def"] for type_2 | Runs compare_document_chunks on selected docs, returns conflict/abstained answer with citations |
| 50 | Phase 2: selected_doc_ids=["abc"] for type_2b | Runs detect_conflicting_documents on selected doc, returns doc-vs-corpus answer |
| 51 | Phase 2: selected_doc_ids but no initial conflict question | Falls through — selected_doc_ids without prior_intent=CONFLICT is ignored |
| 52 | Phase 2: re-submit same question with different selected_doc_ids | New analysis runs on the newly selected documents |

---

## Summary

| Section | Count | Tests |
|---------|-------|-------|
| 1. APPROVAL | 10 | KG-backed approval chains |
| 2. CONFLICT Core | 10 | Mixed conflict scenarios |
| 3. CONFLICT Type 1 | 4 | Clause-vs-corpus expansion |
| 4. CONFLICT Type 2 | 6 | Doc-vs-doc (two-phase flow) |
| 5. CONFLICT Type 2b | 6 | Doc-vs-corpus (two-phase flow) |
| 6. CONFLICT Guardrails | 12 | Validation, access, budget, dedup |
| 7. COMPLIANCE | 10 | Regulation, mandatory requirements |
| 8. PROCEDURE | 10 | Step-by-step workflows |
| 9. GENERAL | 10 | Explainers, summaries |
| 10. DOCUMENT RESOLUTION | 14 | CoT classification, per-slot pgvector, two-phase flow |
| **Total** | **92** | |
