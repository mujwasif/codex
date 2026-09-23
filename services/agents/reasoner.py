"""
Policy Reasoner Agent

Generates grounded answers from retrieved clauses using Qwen3-8B.
Enforces cite-or-abstain invariant.
"""

import re

from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL

SYSTEM_PROMPT = """
You are the Codex Policy Intelligence Engine. Your goal is to provide grounded, cited answers based ONLY on the provided context.

You MUST follow this Chain-of-Thought process:
1. <thinking>
   - Read the question carefully and identify the core information need.
   - Scan each chunk and identify which ones are relevant to the question.
   - For each claim you plan to make, verify the exact citation source (title + clause_ref).
   - Check if the context contains enough information to answer fully. If any part of the question cannot be answered from the context, plan to say "Insufficient policy basis" for that part only.
   - Map every claim to at least one specific citation before writing the final answer.
   </thinking>
2. Final Answer:
   Provide the grounded answer with citations after each claim.

STRICT RULES:
1. Use ONLY the provided context. Do not use outside knowledge.
2. If the answer is not explicitly in the context, you MUST say: "Insufficient policy basis — routed to policy owner."
3. Every factual claim must be followed by a citation: [Doc: {document_title}, Clause: {clause_ref}]
4. Place citations AFTER the sentence they support, before the period.
5. Example: The password must be changed every 90 days [Doc: Password Policy, Clause: 4.2].
6. If multiple clauses support a claim, cite all of them: [Doc: Policy A, Clause: 1.1] [Doc: Policy B, Clause: 2.3]
7. For procedural answers, use numbered steps with bold action items.
8. For factual answers, use clear paragraphs with inline citations.
9. Write in a professional, human-like tone. Do not use emojis. Do not apologize.
"""


def generate_grounded_answer(query, context_chunks, feedback_guidance=""):
    """
    Generate a grounded answer from retrieved clauses.
    
    Args:
        query: User's question
        context_chunks: List of chunk dicts with 'title', 'clause_ref', 'text'
        feedback_guidance: Optional quality guidance
        
    Returns:
        Answer string with bracketed citations
    """
    # Format the retrieved chunks into a readable block for the LLM
    context_text = "\n\n".join([
        f"[Doc: {c['title']}, Clause: {c.get('clause_ref', 'N/A')}]: {c['text']}"
        for c in context_chunks
    ])

    guidance_text = (
        f"\n\nQuality guidance from aggregate user ratings (not policy evidence):\n{feedback_guidance}"
        if feedback_guidance else ""
    )
    
    # Add structured procedural results if present (handled by orchestrator)
    # Note: Since generate_grounded_answer is a standalone function, 
    # we allow the caller to pass in structured data via the query or context_text if needed.
    # However, to maintain signature, we assume the orchestrator integrates it into user_message.
    
    user_message = f"Context:\n{context_text}{guidance_text}\n\nQuestion: {query}"

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message,
        temperature=0.0,
        max_tokens=4096,
        timeout=120.0
    )

    if result.success:
        answer = result.data.strip()
        answer = re.sub(r'<thinking>.*?</thinking>', '', answer, flags=re.DOTALL).strip()
        return answer
    else:
        return f"Error connecting to hosted LLM API: {result.error}"

