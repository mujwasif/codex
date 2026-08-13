==================================================
 NODES
  Clause          1842
  Process         268
  Role            60
  Policy          24
  Regulation      17
  Department      17

 RELATIONSHIPS
  GOVERNS                        2237
  PART_OF                        1842
  CAN_APPROVE                    162
  MAPS_TO                        39
  BELONGS_TO                     23

  Total: 2228 nodes, 4303 relationships


  Here is the step-by-step explanation of how your nodes and relationships are now generated.
---
1. The Input (The "Seed")
The process starts in migrate_to_neo4j.py. For every single chunk of text in your PostgreSQL database, the system gathers:
- The Text: The actual clause content.
- The Metadata: Document ID, Document Title, Version, and Section Path.
This combined package is sent to the Phi-4-mini LLM.
---
2. The Reasoning Phase (Chain-of-Thought)
Instead of just guessing a JSON object, the LLM follows a Chain-of-Thought (CoT) process. This is the "brain" of the generation:
1.  Entity Enumeration: The LLM reads the clause and lists every entity it sees (e.g., "CFO", "Password Policy", "ISO 27001", "$10,000").
2.  Schema Mapping: It looks at your Strict Schema (Policy, Clause, Role, Department, Process, Threshold, Regulation) and maps each entity to exactly one label.
3.  Relationship Logic: It reasons about the connection. 
    *   Example: "Since the CFO is approving the purchase, this is a CAN_APPROVE relationship between the Role (CFO) and the Process (Purchase)."
4.  ID Generation: It creates "Merge Keys" (like {name: "CFO"}) to ensure that if "CFO" appears in 10 different chunks, it always maps to the same single node in Neo4j.
---
3. The Generation (JSON Output)
The LLM then produces a structured JSON block. It is split into two parts:
A. Nodes (The "What")
For every entity found, it creates a node.
- Backbone Nodes: It always creates the Policy and Clause nodes (the anchor of the graph).
- Extracted Nodes: It adds whatever it found (e.g., a Role node for "Manager", a Threshold node for "$5,000").
B. Edges (The "How they connect")
It defines the relationships using only the allowed types:
- (Clause)-[:PART_OF]->(Policy)
- (Role)-[:CAN_APPROVE]->(Process)
- (Policy)-[:MAPS_TO]->(Regulation)
- ...and so on.
---
4. The Database Execution (Neo4j MERGE)
The Python script takes this JSON and converts it into Cypher MERGE statements. 
Why MERGE instead of CREATE?
MERGE is like an "Upsert". 
- If the node Role {name: "Manager"} already exists, Neo4j just finds it.
- If it doesn't exist, Neo4j creates it.
This is how the graph "knits" itself together. When Chunk A mentions "Manager" and Chunk B mentions "Manager", they both link to the same node, creating a web of connections across the entire policy corpus.
---
Summary Flowchart
[ PostgreSQL Chunk ] 
       │
       ▼
[ Phi-4-mini LLM ] ───► [ CoT Reasoning ] ───► [ JSON: Nodes & Edges ]
                                                        │
                                                        ▼
                                               [ Python Validator ]
                                              (Checks if labels are allowed)
                                                        │
                                                        ▼
                                               [ Neo4j MERGE Queries ]
                                                        │
                                                        ▼
                                               [ Final Knowledge Graph ]
Example in Action
Text: "The Finance Director must approve any purchase over $10,000 according to the Procurement Policy."
1.  LLM Reasoning:
    - Entity "Finance Director" $\rightarrow$ Role
    - Entity "purchase" $\rightarrow$ Process
    - Entity "$10,000" $\rightarrow$ Threshold
    - Entity "Procurement Policy" $\rightarrow$ Policy
2.  Relationships:
    - (Finance Director)-[:CAN_APPROVE]->(purchase)
    - (purchase)-[:REQUIRES_THRESHOLD]->($10,000)
    - (Procurement Policy)-[:GOVERNS]->(purchase)
3.  Result: Neo4j creates these nodes and links them. Now, when the approval_agent asks "Who can approve purchases?", it just follows the CAN_APPROVE edge to find the "Finance Director".