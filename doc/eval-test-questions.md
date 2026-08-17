# Intent Test Questions — Codex Policy Intelligence

74 grounded evaluation questions: 10 APPROVAL, 34 CONFLICT (10 core + 12 by type + 12 Clause-vs-Corpus validation/guardrail), 10 COMPLIANCE, 10 PROCEDURE, 10 GENERAL.
Grounded in the 24 archive policy/plan docs in `archive/` and the Neo4j graph. CONFLICT #1 is a real documented corpus ambiguity. CONFLICT #11–34 cover the three conflict detection types and the structured Clause-vs-Corpus validation guardrails (Type 1: Clause-vs-Corpus, Type 2: Document-vs-Document, Type 2b: Document-vs-Corpus).

## 1. APPROVAL (who approves / authorizes) — graph-backed where possible

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | Who approves the supplier risk assessment? | Graph ISMS Manager -CAN_APPROVE-> Supplier Risk Assessment + Supplier policy |
| 2 | Who can approve an exception to use a USB drive? | Graph Management/Asset Manager -> USB Drive Installation + Removable Media policy |
| 3 | Who authorizes patch management activities? | Graph Asset Manager / System Administrator -> Patch Management + Patch policy |
| 4 | Who approves the post-incident report? | Graph Incident Response Officer -> Post-Incident Report + Incident Response plan |
| 5 | Who approves changes to company policies? | ISMS Manager -> Policy Change Approval + every policy's "Change, Review, and Update" (ISMS Committee) |
| 6 | Who authorizes removal of access rights when an employee leaves? | Access Mgmt: access revoking approved by the PM, executed by Security Engineer |
| 7 | Who approves the risk scores in the risk assessment? | Risk Mgmt: Risk Owner approves with the ISMS Manager |
| 8 | Who signs off on repairing a broken company asset? | Asset Mgmt: Asset Manager approves the matter with the COO |
| 9 | Who must approve client/media notification during an incident? | Incident plan: clients/media notified only with CEO & PR team approval |
| 10 | Who approves data purging after a terminated client contract? | Graph Project Manager -> Data Purging After Contract Termination + Data Retention |

## 2. CONFLICT (contradictions / supersede) — includes one real ambiguity

Three conflict detection types:
- **Type 1 (Clause-vs-Corpus):** Finds conflicts between a topic and similar clauses across all documents. Triggered by `/query` with conflict intent.
- **Type 2 (Document-vs-Document):** Compares 2-3 named documents. Triggered by `POST /v1/conflicts/compare`.
- **Type 2b (Document-vs-Corpus):** Finds which documents conflict with a target document. Triggered by `GET /documents/{id}/conflicts`.

### Core conflict questions (1–10)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | Is there an inconsistency between the Vulnerability and Network Security policies on how often penetration testing happens? | Real ambiguity: Vulnerability Mgmt = "biannually" (2×/yr); Network Security = "at least once per year" |
| 2 | Are there clauses that conflict inside the corpus (as recorded in the graph)? | 2 CONFLICTS_WITH edges exist between Clause nodes |
| 3 | Does any policy supersede an older version? | 1 SUPERSEDES edge exists (Policy self-edge) |
| 4 | Do the Backup and Data Retention policies contradict each other about retention? | Should be no conflict (Backup defers retention to business/legal; Retention defines periods) — negative test |
| 5 | Do removable-media rules conflict with the Physical Media Transfer policy? | Probe: Transfer policy uses couriers + physical media; Removable Media bans most USB use — check whether system sees a clash or keeps them distinct |
| 6 | Is there a conflict between Access Management and Clear Desk about workstation lockout? | Both require locking — should return no conflict (negative test) |
| 7 | Do the Encryption policy and Backup policy's storage clauses contradict? | Both about key/backup protection — likely no conflict (negative test) |
| 8 | Is there a conflict between BYOD and Remote Access policies on how to connect personal devices? | BYOD→Guest network only; Remote Access→VPN — complementary, no conflict |
| 9 | Which policy would take precedence if the Change Management and Incident Response rules disagree? | No explicit precedence rule → expected "insufficient basis"/abstain |
| 10 | Are the password-related requirements in Access Management consistent across the corpus? | Password Management Policy is referenced but not present in archive → expected limited/abstained answer |

### Type 1: Clause-vs-Corpus (11–14)

Questions that find similar clauses across all documents and check for contradictions. The expected result should distinguish confirmed conflicts, possible conflicts, complementary scope, supersession, and insufficient context. It should return all validated conflicts, not fabricate findings to reach three.

| # | Question | Expected basis |
|---|----------|----------------|
| 11 | Are there conflicting rules about data retention across all policies? | Type 1: Data Retention (10yr payroll, 90-day logs) vs Log Mgmt (90-day logs) vs Backup (yearly integrity) — may surface retention tension between electronic logs and payroll |
| 12 | Do any policies disagree on how often access rights should be reviewed? | Type 1: Access Mgmt says "quarterly review" — other docs may reference different cadences; likely no conflict (negative test) |
| 13 | Are there conflicting requirements about how to handle stolen devices? | Type 1: Incident Response (notify + revoke credentials + shut down) vs Asset Management (report to COO + Asset Manager) — complementary procedures, check if system sees a clash |
| 14 | Is there a conflict between policies about supplier data handling? | Type 1: Supplier Security (NDA + due diligence + monitoring) vs Data Retention (purging after contract) vs Clear Desk (physical security) — check for tension on data lifecycle |

### Clause-vs-Corpus validation and guardrail questions (23–34)

These questions exercise the structured semantic comparison and deterministic validation rules. Expected results include candidate counts, checked/unchecked analysis where exposed, both-clause citations, and an abstention or inconclusive result when evidence is insufficient.

| # | Question | Expected basis |
|---|----------|----------------|
| 23 | Are there at least three distinct conflicts in the corpus involving mandatory retention periods? | Evaluate enough accessible candidates to find three when present; return every validated conflict ranked by confidence. |
| 24 | If only one policy pair conflicts about password rotation, can you identify the single conflict without inventing two more? | Return one validated conflict; do not manufacture findings to reach the minimum target of three. |
| 25 | Which rules look similar but are complementary because they apply to employees and administrators separately? | Different populations should produce `complementary_scope`, not a false threshold conflict. |
| 26 | Do conditional remote-access requirements conflict when one applies only outside the office and the other applies only to internal networks? | Conditions must be compared; separated applicability should be `complementary_scope` or `no_conflict`. |
| 27 | Do the password rules conflict when one policy says passwords must change every 90 days and another says every 60 days? | Same population, action, modality, and unit with incompatible thresholds; expected `confirmed_conflict` with both citations. |
| 28 | Do a minimum seven-year retention rule and a maximum three-year retention rule conflict? | Comparator and threshold semantics should be recognized as incompatible obligations. |
| 29 | Do a rule requiring encryption and a rule prohibiting encryption for the same data population conflict? | Negation and modality should produce a high-confidence conflict. |
| 30 | Is an older 90-day password rule still in conflict with a newer active 60-day rule that explicitly supersedes it? | Expected `superseded` when version, effective date, and supersession metadata establish precedence. |
| 31 | What happens when two related clauses do not identify their applicable population or precedence? | Expected `possible_conflict` or `insufficient_context`, not a definitive conflict. |
| 32 | What happens when the conflict comparison budget prevents checking every retrieved candidate? | Expected inconclusive analysis with checked and unchecked candidate counts; never report definitive absence of conflicts. |
| 33 | Can the same source/candidate conflict returned by semantic analysis and version analysis appear twice? | Duplicate findings should merge using the canonical source/candidate pair key. |
| 34 | Can an employee retrieve or receive a conflict involving an admin-only policy? | Access-level filtering must exclude unauthorized clauses from candidates, evidence, answers, and citations. |

### Type 2: Document-vs-Document (15–18)

Questions that name 2 specific documents to compare head-to-head.

| # | Question | Expected basis |
|---|----------|----------------|
| 15 | Are there conflicts between the Backup Policy and the Data Retention Policy? | Type 2: Backup defers retention to business/legal; Retention defines periods — should be no conflict (negative test) |
| 16 | Compare the Access Management Policy and the BYOD Policy for contradictions. | Type 2: Access Mgmt requires MFA + least privilege; BYOD requires security package on guest network — complementary, no conflict |
| 17 | Are there contradictions between the Encryption Policy and the Log Management Policy? | Type 2: Encryption requires TLS 1.2+ and at-rest encryption; Log Mgmt sends logs to SIEM — check if log encryption requirements conflict |
| 18 | How do the Change Management and Incident Response policies differ on approval processes? | Type 2: Change Mgmt uses ISMS Manager + CAB; Incident Response uses CEO + PR for notification — different workflows, check if system flags as conflict |

### Type 2b: Document-vs-Corpus (19–22)

Questions that ask which documents in the corpus conflict with a single named document.

| # | Question | Expected basis |
|---|----------|----------------|
| 19 | Which policies conflict with the Clear Desk and Screen Policy? | Type 2b: find all docs with conflicting lock-screen or physical-access rules — likely no conflict (Clear Desk is additive) |
| 20 | Which documents contradict the Vulnerability Management Policy? | Type 2b: Network Security says "at least once per year" for pen tests; Vuln Mgmt says "biannually" — real conflict |
| 21 | Are there any policies that conflict with the Removable Media policy? | Type 2b: Physical Media Transfer uses couriers + physical media; Removable Media bans most USB — possible clash on physical transfer methods |
| 22 | Which other policies disagree with the Incident Response Plan on notification timelines? | Type 2b: find docs with different notification/approval chains — likely no conflict (IR Plan is the authoritative source) |

## 3. COMPLIANCE (regulation, ISO 27001, violation, mandatory)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | What ISO 27001 annex does the Access Management Policy reference? | A.9.1.1/A.9.2.x (Access Control) |
| 2 | Is ISO 27001 Annex A.12.3.1 the standard backing the Backup Policy? | Yes — A.12.3.1 Information Backup |
| 3 | Are audit/log data retention requirements defined? For how long? | Log Mgmt: 90 days; Data Retention: electronic system logs 90 days |
| 4 | What happens if an employee violates a security policy? | Disciplinary consequences "in proportion to their violation" (all policies) |
| 5 | Are suppliers required to sign an NDA before accessing company data? | Supplier policy: yes, all third parties |
| 6 | Is encryption of confidential data at rest mandatory? | Encryption policy: at-rest encryption required for confidential data |
| 7 | Are backup integrity tests a mandatory annual requirement? | Backup policy: integrity tested at least yearly |
| 8 | Is vulnerability scanning a required compliance obligation? At what cadence? | Biweekly routine scans (Vulnerability Mgmt) |
| 9 | What are the retention requirements for payroll and client contracts? | Data Retention: payroll 10 yr; NDA/MSA/SoW retained permanently |
| 10 | Which ISO 27001 requirements govern the Risk Assessment process? | 6.1, 8.2, 8.3 (Risk Mgmt policy References) |

## 4. PROCEDURE (how to / steps / what should I do)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | How do I get my password reset? | Access Mgmt: only after the Security Engineer verifies the employee's identity |
| 2 | What should I do if I lose my access card? | Physical Security: inform the HR Generalist |
| 3 | How do I lock my screen when leaving my desk? | Clear Desk policy: per-OS key combos (Win/macOS/Linux) |
| 4 | Walk me through what to do if a laptop is stolen or malware-infected | BCP IT-incident scenario: immediate notify, revoke credentials, shut down, impact assessment by CISO |
| 5 | What are the steps of the standard change management process? | Change Mgmt: request → scope → impact → approval → test → implement → document → communicate |
| 6 | What happens when both internet providers are unavailable? | BCP: notify Asset Manager → COO fallback; local IT activates 3rd channel |
| 7 | How is a new hire's laptop provisioned? | Asset Mgmt acquiring process: HR/PM ticket → COO specs → Asset Manager formats/encrypts/installs |
| 8 | What should I do if I suspect a security incident? | Incident plan: report promptly via the chain; IR team gathered; log details |
| 9 | How do I request an exception to use removable media? | Removable Media: ask Management for an individual exception (or Asset Manager USB exception) |
| 10 | How often are critical access rights reviewed? | Access Mgmt: quarterly review by system administrators/owners |

## 5. GENERAL (explain / tell me about / what is)

| # | Question | Expected basis |
|---|----------|----------------|
| 1 | What does the Access Management Policy require for user accounts? | Unique accounts, MFA where possible, least privilege, quarterly review |
| 2 | Tell me about the BYOD policy | Guest network only, security package (EDR/MDM/encryption/SIEM) required, no client data |
| 3 | What does the Backup Policy require? | Images of key systems, ≥3 retention copies, on-site + off-site, yearly integrity test |
| 4 | What is the Encryption Policy about? | TLS1.2+ in transit, at-rest encryption, key lifecycle (rotate ≥ annually) |
| 5 | Summarize the Supplier Security Policy | Due diligence, NDA, agreements, 3 supplier groups, annual monitoring |
| 6 | What does the Incident Response Plan cover? | Detection → containment → eradication → recovery → post-incident review; 4 severity levels |
| 7 | What are the BCP recovery targets? | BC for 72 h; tech recovery ≤8 h; business recovery ≤4 h |
| 8 | What does the Clear Desk and Screen Policy require? | Lock desks/screens, no sticky-note passwords, 5-min screensaver, log off at day's end |
| 9 | What does the Log Management Policy require? | All log sources → SIEM, admin/operator actions logged, NTP sync, 90-day retention |
| 10 | Give me an overview of the vulnerability management workflow | Biweekly scans, pen tests, risk-based patching, rescan/re-test after remediation |
