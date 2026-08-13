# Grounding & Guardrails: "Cite or Abstain"

## Core Principle
The fundamental invariant of Codex is: **Every answer must be supported by a cited clause or the system must abstain.** Hallucinations are strictly forbidden in a compliance context.

## 1. The Access Guardrail (RBAC)
Prevents data leakage at the source.
- **Mechanism:** The `access_level` is baked into the JWT.
- **Logic:** The `search_policy` function applies a filter to the database query. If a user is Level 1, they are physically unable to retrieve chunks marked as Level 3 (Admin), meaning the LLM never sees the restricted data.

## 2. The Grounding Guardrail (System Prompt)
Prevents the LLM from using its own "imagination."
- **The "Handcuffs":** The LLM is given a strict system prompt: *"Use ONLY the provided context. Do not use outside knowledge. If the answer is not explicitly in the context, you MUST say: 'Insufficient policy basis'."*
- **Deterministic Output:** Temperature is set to `0.0` to ensure the model provides the same logical answer every time for the same input.

## 3. The Verification Guardrail (Post-Process Audit)
The final safety check performed by `verifier.py`.
- **Citation Extraction:** The verifier uses Regex to find all patterns matching `[Doc: ..., Clause: ...]`.
- **The Validity Check:** 
  - If the LLM cites `[Doc: Finance, Clause: 5.1]` but the search engine only retrieved `Clause 5.2`, the verifier detects this mismatch.
- **Action:** If a mismatch is found, the `verifier` returns `False`, and the API returns a "rejected by guardrails" response instead of the hallucinated answer.

## Summary of Failure Modes
| Failure | Guardrail Triggered | System Response |
|:---|:---|:---|
| Model guesses a value | Verifier (Missing Citation) | `verdict: abstained` |
| Model uses general knowledge | Reasoner (System Prompt) | "Insufficient policy basis" |
| User tries to access Admin data | Search (RBAC Filter) | Result not found $\rightarrow$ Abstain |
