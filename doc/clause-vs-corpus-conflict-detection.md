# Clause-vs-Corpus Conflict Detection

## Purpose

Clause-vs-Corpus analysis compares policy clauses relevant to a user's conflict question with semantically related clauses across the accessible corpus. Retrieval produces candidates; semantic comparison and deterministic validation decide whether a conflict is reportable.

The system must not invent conflicts to reach a target count. It returns every validated finding, ranked strongest first, and reports when candidate analysis is incomplete.

## Processing Flow

```text
Conflict question
  -> source clause retrieval
  -> corpus candidate retrieval
  -> context preservation
  -> rule extraction
  -> semantic comparison
  -> deterministic validation
  -> deduplication and ranking
  -> cited explanation
  -> citation verification
```

## Source Clauses

The normal retrieval pipeline supplies the strongest relevant clauses using vector search, BM25, reciprocal-rank fusion, and CrossEncoder reranking. Each source clause is marked `origin="query_source"` and should preserve its document ID/title, version, effective date, status, clause reference, section path, page, headings, and exact text.

## Corpus Candidates

Each source clause searches accessible documents for related candidates. The current Clause-vs-Corpus defaults are:

| Setting | Value |
|---|---:|
| Candidate retrieval limit | 20 per source clause |
| Minimum conflict target | 3 when three exist |
| Maximum semantic comparisons | 15 |
| Returned conflicts | All validated findings |

Candidates are filtered for access level, low-information content, duplicate chunks, the source clause itself, and the source document. Candidates are marked `origin="corpus_candidate"`. Similarity is only a candidate-generation signal; it does not prove contradiction.

## Rule Meaning

The comparison represents each clause using:

- Subject and action/object
- Actor, role, population, department, and geography
- Modality such as `must`, `may`, `should`, or `must not`
- Numeric thresholds, comparators, units, and time periods
- Conditions and exceptions
- Applicability and effective period
- Document version, status, and precedence metadata

Deterministic extraction handles common values, units, modal verbs, negation, roles, and dates. The LLM handles ambiguous subject and scope meaning, but its claims must be supported by the actual clause text.

## Semantic Statuses

The semantic comparison returns one of these statuses:

| Status | Meaning |
|---|---|
| `confirmed_conflict` | Same applicable scope and incompatible mandatory requirements, with strong evidence. |
| `possible_conflict` | Evidence suggests contradiction, but scope or precedence remains uncertain. |
| `no_conflict` | The evaluated clauses do not contradict one another. |
| `complementary_scope` | Requirements differ because roles, populations, departments, or conditions differ. |
| `insufficient_context` | The available text or metadata cannot support a safe decision. |
| `superseded` | A newer valid policy clearly replaces an older requirement. |

The structured LLM result includes status, subject, scope overlap, difference type, both requirements, confidence, reason, and missing context.

## Validation Rules

A positive conflict is accepted only when:

1. The subjects overlap.
2. The roles or populations overlap.
3. The actions are comparable.
4. Units and requirements are comparable.
5. Obligations or thresholds are incompatible.
6. Exceptions and conditions do not separate the rules.
7. Both clauses are applicable and accessible.
8. Both stated requirements occur in the cited clause text.
9. The result has a supported status, valid confidence, scope, and explanation.

Confidence is interpreted as:

```text
0.85 or higher -> confirmed_conflict
0.60 to 0.84  -> possible_conflict
below 0.60    -> insufficient_context
```

For example:

```text
Employees must change passwords every 90 days.
Employees must change passwords every 60 days.
```

is a likely threshold conflict. In contrast:

```text
Employees must change passwords every 90 days.
Administrators must change passwords every 60 days.
```

is complementary scope unless additional context proves that administrators are also covered by the employee rule.

## Versions and Supersession

A matching clause reference is not automatically a conflict. The system compares policy identity, version, effective date, active status, supersession metadata, applicability, and actual rule meaning. It reports `superseded` only when precedence is supported; otherwise it reports `possible_conflict` or `insufficient_context`.

## Candidate Completion

The evaluator checks candidates in similarity order until the comparison budget is exhausted or all candidates are evaluated. It records:

- Total candidates
- Checked candidates
- Unchecked candidates
- LLM call count
- Truncation state
- Inconclusive state

If candidates remain unchecked, the response must say that analysis was inconclusive. It must not claim that no conflicts exist.

## Deduplication and Ranking

Findings use this canonical pair key:

```text
(source_document_id, source_clause_id,
 candidate_document_id, candidate_clause_id)
```

Duplicate findings from semantic analysis, version checks, graph data, or deterministic checks are merged. Final results are ranked by confidence, scope overlap, evidence completeness, requirement-difference strength, and similarity. All validated findings are returned; the strongest three are not an artificial maximum.

## Human-Readable Answers

Each reported conflict must describe both requirements, explain the overlapping scope and contradiction, state supported precedence or uncertainty, and cite both clauses:

```text
[Doc: Password Policy, Clause: 4.3.2]
[Doc: Access Control Policy, Clause: 6.1]
```

The verifier checks that cited document/clause pairs exist in the retrieved evidence. Invalid or missing citations cause abstention.

## Limitations

The current implementation still has several conservative limitations:

- Candidate retrieval is primarily similarity-based rather than a fully separate hybrid conflict index.
- Comparator semantics such as `at least`, `at most`, and `no more than` are not fully normalized.
- Conditions and exceptions rely heavily on LLM interpretation.
- Reliable supersession requires complete version and effective-date metadata.
- Neo4j supports navigation and historical relationships but is not the primary semantic decision-maker.
- Citation verification confirms source identity, not full natural-language entailment.
