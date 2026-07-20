"""
Codex PostgreSQL -> Neo4j Knowledge Graph Migration

Two-pass approach:
  Pass 1: Regex + headings (fast, ~1 min)
  Pass 2: Phi-4-mini LLM (slow, ~1 hour)

Usage:
    source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/migrate_to_neo4j.py
"""

import os
import re
import json
import sys
from neo4j import GraphDatabase
from sqlalchemy import text
from packages.shared.db import get_db_session
from services.ingestion.kg_extractor import extract_entities_from_clause, detect_conflicts

NEO4J_URI = "neo4j://localhost:7687"
neo = GraphDatabase.driver(NEO4J_URI)


def run(query, **params):
    neo.execute_query(query, **params)


# ═══════════════════════════════════════
#  PASS 1: Regex + Headings (Fast)
# ═══════════════════════════════════════

def pass1_clear():
    neo.execute_query("MATCH (n) DETACH DELETE n")
    print("Graph cleared.")


def pass1_index():
    for label in ["Policy", "Clause", "Role", "Department", "Process", "Threshold", "Regulation"]:
        prop = "name" if label in ("Role", "Department", "Process", "Regulation") else "id"
        run(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.{prop})")
    print("Indexes created.")


def pass1_policies(session):
    rows = session.execute(text("SELECT * FROM documents")).fetchall()
    for row in rows:
        r = row._mapping
        title = r["title"] or ""
        process_name = re.sub(r'\s*policy\s*$', '', title, flags=re.I).strip()
        if not process_name:
            process_name = title

        run("""
            MERGE (p:Policy {id: $id})
            SET p.title = $title, p.version = $version,
                p.status = $status, p.effective_date = $ed,
                p.process_name = $process_name
        """, id=str(r["id"]), title=title, version=r["version"] or "v1",
             status=r["status"] or "active",
             ed=str(r["effective_date"]) if r["effective_date"] else "",
             process_name=process_name)

        run("""
            MERGE (pr:Process {name: $name})
            SET pr.source_policy = $title
        """, name=process_name, title=title)

    print(f"Policies: {len(rows)}")


def pass1_clauses(session):
    rows = session.execute(text(
        "SELECT id, document_id, section_path, clause_ref, text, version FROM chunks"
    )).fetchall()
    for row in rows:
        r = row._mapping
        run("""
            MERGE (c:Clause {id: $id})
            SET c.clause_ref = $cr, c.section_path = $sp,
                c.text_ref = LEFT($text, 300), c.version = $ver
            WITH c
            MATCH (p:Policy {id: $doc_id})
            MERGE (c)-[:PART_OF]->(p)
        """, id=str(r["id"]), cr=r["clause_ref"] or "",
             sp=r["section_path"] or "", text=r["text"] or "",
             ver=r["version"] or "v1", doc_id=str(r["document_id"]))

        run("""
            MATCH (c:Clause {id: $cid})
            MATCH (p:Policy {id: $doc_id})
            MATCH (pr:Process {name: p.process_name})
            MERGE (c)-[:GOVERNS]->(pr)
        """, cid=str(r["id"]), doc_id=str(r["document_id"]))

    print(f"Clauses: {len(rows)}")


def pass1_entities(session):
    rows = session.execute(text(
        "SELECT id, type, name, document_id, attrs FROM entities"
    )).fetchall()

    roles_found = set()
    thresholds_found = []

    for row in rows:
        r = row._mapping
        eid = str(r["id"])
        etype = (r["type"] or "").lower()
        ename = r["name"] or ""
        doc_id = str(r["document_id"])
        attrs = r["attrs"] if isinstance(r["attrs"], dict) else {}

        if etype == "approval":
            role_name = attrs.get("authority", "").title()
            if not role_name:
                m = re.search(r":\s*(\w+)\s+approval", ename, re.I)
                if m:
                    role_name = m.group(1).title()
            if role_name:
                records, _, _ = neo.execute_query(
                    "MATCH (p:Policy {id: $doc_id}) RETURN p.process_name AS pn",
                    doc_id=doc_id
                )
                process_name = records[0]["pn"] if records else "General Compliance"

                run("""
                    MERGE (r:Role {name: $name})
                    WITH r
                    MATCH (pr:Process {name: $process})
                    MERGE (r)-[:CAN_APPROVE]->(pr)
                """, name=role_name, process=process_name)
                roles_found.add(role_name)

        elif etype == "threshold":
            amt = float(attrs.get("amount", 0))
            cur = attrs.get("currency", "USD")
            if amt > 0:
                thresholds_found.append((doc_id, amt, cur))
                run("""
                    MERGE (t:Threshold {id: $id})
                    SET t.amount = $amt, t.currency = $cur, t.basis = $basis
                    WITH t
                    MATCH (p:Policy {id: $doc_id})
                    MERGE (p)-[:HAS_THRESHOLD]->(t)
                """, id=eid, amt=amt, cur=cur,
                     basis=attrs.get("original_text", ""), doc_id=doc_id)

    for doc_id, amt, cur in thresholds_found:
        run("""
            MATCH (r:Role)-[:CAN_APPROVE]->(pr:Process)
            MATCH (p:Policy {id: $doc_id})
            WHERE pr.name = p.process_name
            SET r.max_amount = $amt, r.currency = $cur
        """, doc_id=doc_id, amt=amt, cur=cur)

    print(f"Roles: {len(roles_found)}, Thresholds: {len(thresholds_found)}")


def pass1_supersedes():
    records, _, _ = neo.execute_query("""
        MATCH (p1:Policy), (p2:Policy)
        WHERE p1.id < p2.id AND p1.title = p2.title AND p1.version <> p2.version
        RETURN p1.id AS old_id, p2.id AS new_id
    """)
    for rec in records:
        run("""
            MATCH (n:Policy {id: $new}), MATCH (o:Policy {id: $old})
            MERGE (n)-[:SUPERSEDES]->(o)
        """, new=rec["new_id"], old=rec["old_id"])
    print(f"SUPERSEDES: {len(records)}")


# ═══════════════════════════════════════
#  PASS 2: LLM Entity Extraction (Slow)
# ═══════════════════════════════════════

def pass2_llm_entities(session):
    """
    For each clause, use Phi-4-mini to extract
    {role, department, process, threshold, regulation, obligation}.
    No LIMIT — processes ALL clauses for full graph coverage.
    """
    rows = session.execute(text(
        "SELECT id, document_id, section_path, text FROM chunks"
    )).fetchall()

    total = len(rows)
    print(f"\nPass 2: Extracting entities from {total} clauses with Phi-4-mini...")

    for i, row in enumerate(rows):
        try:
            r = row._mapping
            clause_id = str(r["id"])
            clause_text = r["text"] or ""

            if len(clause_text) < 20:
                continue

            entities = extract_entities_from_clause(clause_text)

            # Helper: extract first string from LLM response
            def first_str(val):
                if isinstance(val, list):
                    return val[0] if val else None
                if isinstance(val, dict):
                    return list(val.values())[0] if val else None
                return val

            # Obligation — set on Clause node
            obligation = first_str(entities.get("obligation"))
            if obligation and isinstance(obligation, str):
                run("MATCH (c:Clause {id: $cid}) SET c.obligation = $ob",
                    cid=clause_id, ob=obligation)

            # Role
            role_name = first_str(entities.get("role"))
            if role_name:
                role_name = str(role_name).title()

            # Process
            process_name = first_str(entities.get("process"))
            if process_name:
                process_name = str(process_name).title()
            else:
                process_name = "General Compliance"

            if role_name:
                # CAN_APPROVE: Role → Process
                run("MERGE (r:Role {name: $role}) MERGE (pr:Process {name: $proc}) MERGE (r)-[:CAN_APPROVE]->(pr)",
                    role=role_name, proc=process_name)

                # BELONGS_TO: Role → Department
                dept_name = first_str(entities.get("department"))
                if dept_name:
                    dept_name = str(dept_name).title()
                    run("MERGE (d:Department {name: $dept}) MERGE (r:Role {name: $role}) MERGE (r)-[:BELONGS_TO]->(d)",
                        dept=dept_name, role=role_name)

                # REQUIRES_THRESHOLD: Process → Threshold
                threshold = first_str(entities.get("threshold"))
                if isinstance(threshold, dict):
                    amt = float(threshold.get("amount", 0))
                    cur = threshold.get("currency", "USD")
                    if amt > 0:
                        run("MERGE (pr:Process {name: $proc}) MERGE (t:Threshold {id: $tid}) SET t.amount=$amt, t.currency=$cur MERGE (pr)-[:REQUIRES_THRESHOLD]->(t)",
                            proc=process_name, tid=f"llm_{clause_id}", amt=amt, cur=cur)

                # GOVERNS: Clause → Process
                run("MATCH (c:Clause {id: $cid}) MERGE (pr:Process {name: $proc}) MERGE (c)-[:GOVERNS]->(pr)",
                    cid=clause_id, proc=process_name)

            # MAPS_TO: Policy → Regulation
            reg_name = first_str(entities.get("regulation"))
            if reg_name:
                reg_name = str(reg_name).upper().strip()
                run("MATCH (c:Clause {id: $cid}) MATCH (p:Policy)<-[:PART_OF]-(c) MERGE (reg:Regulation {name: $reg}) MERGE (p)-[:MAPS_TO]->(reg)",
                    cid=clause_id, reg=reg_name)

        except Exception:
            pass

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{total}")

    print(f"  Done: {total} clauses processed")


def pass2_conflicts(session):
    """
    Compare clauses within same document for contradictions.
    """
    print("\nPass 2: Detecting conflicts between clauses...")

    rows = session.execute(text(
        "SELECT id, document_id, section_path, text FROM chunks WHERE text IS NOT NULL AND length(text) > 50"
    )).fetchall()

    from collections import defaultdict
    by_doc = defaultdict(list)
    for row in rows:
        r = row._mapping
        by_doc[str(r["document_id"])].append((str(r["id"]), r["section_path"] or "", r["text"] or ""))

    conflicts = 0
    for doc_id, clauses in by_doc.items():
        if len(clauses) < 2:
            continue
        for i in range(len(clauses)):
            for j in range(i + 1, min(i + 5, len(clauses))):
                id_a, sp_a, text_a = clauses[i]
                id_b, sp_b, text_b = clauses[j]
                if sp_a.split(">")[0] != sp_b.split(">")[0]:
                    continue
                if detect_conflicts(text_a[:300], text_b[:300]):
                    run("""
                        MATCH (c1:Clause {id: $id1})
                        MATCH (c2:Clause {id: $id2})
                        MERGE (c1)-[:CONFLICTS_WITH]->(c2)
                    """, id1=id_a, id2=id_b)
                    conflicts += 1

    print(f"  CONFLICTS_WITH: {conflicts}")


# ═══════════════════════════════════════
#  Summary
# ═══════════════════════════════════════

def summary():
    print("\n" + "=" * 50)
    print(" NODES")
    records, _, _ = neo.execute_query("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC")
    for r in records:
        print(f"  {r['l']:15s} {r['c']}")
    print("\n RELATIONSHIPS")
    records, _, _ = neo.execute_query("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC")
    for r in records:
        print(f"  {r['t']:30s} {r['c']}")
    nodes_rec, _, _ = neo.execute_query("MATCH (n) RETURN count(n) AS n")
    rels_rec, _, _ = neo.execute_query("MATCH ()-[r]->() RETURN count(r) AS r")
    print(f"\n  Total: {nodes_rec[0]['n']} nodes, {rels_rec[0]['r']} relationships")
    print("=" * 50)


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    incremental = "--incremental" in sys.argv

    print("=" * 40)
    print(" Codex -> Neo4j (Dynamic)")
    print("=" * 40)

    # Pass 1: Fast (regex + headings) — skip if --incremental
    if not incremental:
        print("\n--- PASS 1: Regex + Headings ---")
        pass1_clear()
        pass1_index()
        with get_db_session() as s:
            pass1_policies(s)
            pass1_clauses(s)
            pass1_entities(s)
        pass1_supersedes()
        summary()
    else:
        print("\n--- PASS 1: SKIPPED (incremental mode) ---")

    # Pass 2: Slow (LLM) — only if llama.cpp server is running
    import requests as req
    llm_running = False
    try:
        resp = req.get("http://localhost:8080/health", timeout=3)
        llm_running = resp.status_code == 200
    except Exception:
        pass

    if llm_running:
        print("\n--- PASS 2: LLM Entity Extraction ---")
        with get_db_session() as s:
            pass2_llm_entities(s)
            pass2_conflicts(s)
        summary()
    else:
        print("\n--- PASS 2: SKIPPED (llama.cpp server not running) ---")
        print("  Start with: /home/mujtaba/new_folder/codex/infra/server_phi.sh")
        print("  Then re-run this script to add LLM-extracted relationships.")
        summary()

    neo.close()
    print("\nDone! http://localhost:7474")


if __name__ == "__main__":
    main()
