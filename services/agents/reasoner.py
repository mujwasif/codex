import requests

# llama.cpp server configuration
LLAMA_URL = "http://localhost:8080/v1/chat/completions"

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
    # Format the retrieved chunks into a readable block for the LLM
    context_text = "\n\n".join([
        f"[Doc: {c['title']}, Clause: {c.get('clause_ref', 'N/A')}]: {c['text']}" 
        for c in context_chunks
    ])
    
    # Construct the message sequence for Chat Completion API
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"}
    ]
    
    payload = {
        "model": "deepseek-r1-distill-llama-8b",
        "messages": messages,
        "temperature": 0.0,
        "stream": False
    }
    
    try:
        response = requests.post(LLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        return f"Error connecting to llama.cpp server: {str(e)}"
