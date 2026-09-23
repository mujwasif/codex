"""
Procedure Agent - Generates structured workflows or summaries for procedural queries.
"""
import re
from typing import List, Dict, Any, Optional
from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL


class ProcedureAgent:
    def process(self, question: str, chunks: List[Dict[str, Any]]) -> Optional[str]:
        """
        Analyzes chunks and generates either a step-by-step workflow or a
        structured summary, depending on the query type.
        """
        if not chunks:
            return None

        context_text = "\n\n".join([
            f"[Doc: {c['title']}, Clause: {c.get('clause_ref', 'N/A')}]: {c['text']}"
            for c in chunks
        ])

        is_summary = any(kw in question.lower() for kw in (
            "summarize", "summary", "overview", "sum up", "recap",
            "tell me about", "what is", "what are the key",
        ))

        if is_summary:
            prompt = f"""Explain what this policy covers in a conversational, easy-to-understand way. Write as if you're briefing a colleague who needs to understand the key points quickly. Include [Doc: X, Clause: Y] citations for every claim.

Chain-of-Thought:
1. Identify the primary subject and scope of the policy.
2. Extract the key obligations (must/should/may), thresholds, and deadlines.
3. Identify who is responsible and who is affected.
4. Note any important exceptions or special conditions.

Output format (no thinking block — just the final output):

Here's what this policy covers:

[1-2 paragraph introduction explaining the purpose in plain language]

Key things to know:
- [Point 1] [Doc: X, Clause: Y]
- [Point 2] [Doc: X, Clause: Y]
- [Point 3] [Doc: X, Clause: Y]

Who this applies to: [scope]
Main obligations: [mandatory requirements with citations]
Any deadlines: [time-based requirements]

If the context does not contain enough information to form a meaningful summary, say "Insufficient policy basis."

---

Context:
{context_text}

Question: {question}
"""
        else:
            prompt = f"""Walk someone through this process step by step, like you're guiding them through it for the first time. Be clear and encouraging. Use [Doc: X, Clause: Y] citations after each step.

Chain-of-Thought:
1. Identify the primary goal of the procedure.
2. List all mentioned roles, departments, and required documents.
3. Map the sequential dependencies (which step must come first, second, etc.).
4. Check for mandatory prerequisites that must be satisfied before the process starts.
5. Verify if any steps are missing or ambiguous in the provided context.

Output format (no thinking block — just the final output):

Here's how to do this, step by step:

Before you start, make sure you have [prerequisites with citations].

Then:
1. **[Action]**: [Description] [Doc: X, Clause: Y]
2. **[Action]**: [Description] [Doc: X, Clause: Y]
...

Who's responsible: [role] [Doc: X, Clause: Y]
If something goes wrong: [escalation path] [Doc: X, Clause: Y]

If the context does not contain enough information to form a complete sequence, say "Insufficient policy basis."
**Escalation:** [What happens if a step is missed or delayed]

If the context does not contain enough information to form a complete sequence, say "Insufficient policy basis."

---

Context:
{context_text}

Question: {question}
"""

        result = llm_generate(
            model=AGENT_MODEL,
            system_prompt=(
                "You are a senior policy analyst who explains policies in a clear, "
                "conversational tone — like you're helping a colleague understand what "
                "they need to do. Cite every claim with [Doc: X, Clause: Y]. "
                "Do not use emojis. "
                "If information is insufficient, say 'Insufficient policy basis'. "
                "Respond ONLY with the formatted output — no commentary outside the template."
            ),
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=120.0,
        )

        if result.success:
            answer = result.data.strip()
            answer = re.sub(r'<thinking>.*?</thinking>', '', answer, flags=re.DOTALL).strip()
            if "Insufficient policy basis" in answer or not answer:
                return None
            return answer

        return None
