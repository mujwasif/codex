"""
Procedure Agent - Generates structured workflows for procedural queries.
"""
import json
import re
from typing import List, Dict, Any, Optional
from services.agents.tools.llm_tools import llm_generate, QWEN3_8B_MODEL

class ProcedureAgent:
    def process(self, question: str, chunks: List[Dict[str, Any]]) -> Optional[str]:
        """
        Analyzes chunks and generates a structured, step-by-step workflow using Chain-of-Thought.
        """
        if not chunks:
            return None

        context_text = "\n\n".join([
            f"[Doc: {c['title']}, Clause: {c.get('clause_ref', 'N/A')}]: {c['text']}"
            for c in chunks
        ])

        prompt = f"""Analyze the policy context and generate a structured, step-by-step procedure for the user's query.
        
        You MUST follow this Chain-of-Thought process:
        1. <thinking>
           - Identify the primary goal of the procedure.
           - List all mentioned roles, departments, and required documents.
           - Map the sequential dependencies (which step must come first, second, etc.).
           - Check for mandatory prerequisites that must be satisfied before the process starts.
           - Verify if any steps are missing or ambiguous in the provided context.
           </thinking>
        
        2. Final Output:
           Start with "### 📋 Procedure: [Process Name]"
           - Create a "**Prerequisites**" section with a bulleted list. Each item MUST have a citation [Doc: X, Clause: Y].
           - Create a "**Steps**" section with a numbered list. Format: "Step #. **[Action Label]**: [Description]. [Doc: X, Clause: Y]"
           - Create a "**Responsibility**" section stating which role or department is responsible.
        
        If the context does not contain enough information to form a complete sequence, say "Insufficient policy basis".
        
        Context:
        {context_text}
        
        Question: {question}
        """

        result = llm_generate(
            model=QWEN3_8B_MODEL,
            system_prompt="You are a procedural expert. Use Chain-of-Thought reasoning to analyze policy text and generate strictly formatted workflows. Use citations for every claim.",
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
            timeout=120.0
        )

        if result.success:
            answer = result.data.strip()
            
            # Remove the thinking block from the final output to the user
            answer = re.sub(r'<thinking>.*?</thinking>', '', answer, flags=re.DOTALL).strip()
            
            if "Insufficient policy basis" in answer or not answer:
                return None
            return answer
        
        return None
