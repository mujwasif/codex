import requests
import sys
from typing import List, Dict

API_BASE_URL = "http://localhost:8000"

def login(username, password):
    """Authenticate user and return JWT token."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/login",
            json={"username": username, "password": password}
        )
        response.raise_for_status()
        data = response.json()
        return data.get("access_token")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return None

def ask_codex(question: str, token: str, history: List[Dict]):
    """Send a question to the Codex API and return the answer."""
    headers = {"Authorization": f"Bearer {token}"}
    
    # Build context-aware question from history
    full_query = question
    if history:
        history_text = "\n".join([
            f"User: {h['question']}\nCodex: {h['answer']}" 
            for h in history[-5:]  # Keep last 5 turns
        ])
        full_query = f"Conversation History:\n{history_text}\n\nQuestion: {question}"

    try:
        response = requests.post(
            f"{API_BASE_URL}/query",
            json={"question": full_query, "search_mode": "hybrid"},
            headers=headers,
            timeout=120
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Query failed: {e}")
        return None

def main():
    print("=" * 50)
    print("   CODEX POLICY INTELLIGENCE ENGINE CLI")
    print("=" * 50)
    print("\nAvailable users: admin, manager, employee")
    
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    token = login(username, password)
    if not token:
        print("\nAuthentication failed. Exiting...")
        sys.exit(1)
        
    print(f"\n✓ Logged in as {username}. Welcome to Codex.")
    print("Type your questions. Type 'quit' or 'exit' to stop.\n")
    
    history = []
    
    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit"]:
                print("\nGoodbye!")
                break
            
            # Send query to API
            result = ask_codex(user_input, token, history)
            
            if not result:
                print("Codex: Sorry, I encountered an error processing your request.")
                continue
                
            # Extract and display answer
            answer = result.get("answer", "No answer provided.")
            verdict = result.get("verdict", "unknown")
            confidence = result.get("confidence", 0.0)
            
            print(f"\nCodex: {answer}")
            
            # Print citations
            citations = result.get("citations", [])
            if citations:
                print("\nCitations:")
                for i, cit in enumerate(citations, 1):
                    ref = cit.get("clause_ref", "N/A")
                    score = cit.get("score", 0.0)
                    print(f"  {i}. {ref} (score: {score:.3f})")
            
            print(f"\nVerdict: {verdict} | Confidence: {confidence:.2f}")
            print("-" * 30)
            
            # Update history with the ORIGINAL question (not the one with context prepended)
            history.append({
                "question": user_input,
                "answer": answer
            })
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break

if __name__ == "__main__":
    main()
