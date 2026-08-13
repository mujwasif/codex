# Intent Test Questions — Codex Policy Intelligence

50 grounded evaluation questions, 10 per intent (APPROVAL, CONFLICT, COMPLIANCE, PROCEDURE, GENERAL).
Grounded in the 24 archive policy/plan docs in `archive/` and the Neo4j graph. CONFLICT #1 is a real documented corpus ambiguity.

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