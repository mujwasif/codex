"""
Policy Reasoner Agent

Generates grounded answers from retrieved clauses using DeepSeek-R1.
Enforces cite-or-abstain invariant.
"""

from services.agents.tools.llm_tools import llm_generate, DEEPSEEK_MODEL

SYSTEM_PROMPT = """
You are the Codex Policy Intelligence Engine. Your goal is to provide grounded, cited answers based ONLY on the provided context.

STRICT RULES:
1. Use ONLY the provided context. Do not use outside knowledge.
2. If the answer is not explicitly in the context, you MUST say: "Insufficient policy basis — routed to policy owner."
3. Every claim must be followed by a citation in brackets, e.g., [Doc: Access_Policy, Clause: 4.2].
4. If the context contains conflicting information, highlight the conflict.
5. Do not apologize or explain your reasoning; provide only the final grounded answer.
"""


def generate_grounded_answer(query, context_chunks):
    """
    Generate a grounded answer from retrieved clauses.
    
    Args:
        query: User's question
        context_chunks: List of chunk dicts with 'title', 'clause_ref', 'text'
        
    Returns:
        Answer string with bracketed citations
    """
    # Format the retrieved chunks into a readable block for the LLM
    context_text = "\n\n".join([
        f"[Doc: {c['title']}, Clause: {c.get('clause_ref', 'N/A')}]: {c['text']}"
        for c in context_chunks
    ])

    user_message = f"Context:\n{context_text}\n\nQuestion: {query}"

    result = llm_generate(
        model=DEEPSEEK_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message,
        temperature=0.0,
        max_tokens=2048,
        timeout=120.0
    )

    if result.success:
        return result.data
    else:
        return f"Error connecting to llama.cpp server: {result.error}"
