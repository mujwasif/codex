#!/usr/bin/env python3
"""
History tests: server-side conversational memory from persisted queries/answers.
"""

import os
import sys
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_fake_session(queries_with_answers):
    """Create a mock DB session that returns the given (query, answer) pairs."""
    class _FakeQuery:
        def __init__(self, q_text, a_text, a_verdict, created_at):
            self.id = "qid"
            self.question = q_text
            self.created_at = created_at
            self.answers = [MagicMock(answer=a_text, verdict=a_verdict)]

    class _FakeSession:
        def __init__(self):
            self._queries = queries_with_answers
            self._offset = 0

        def query(self, model):
            return self

        def filter(self, *args):
            return self

        def options(self, *args):
            return self

        def order_by(self, *args):
            return self

        def limit(self, n):
            self._limit = n
            return self

        def all(self):
            return self._queries[:self._limit]

    return _FakeSession()


def test_load_user_history_empty():
    """No history returns empty list."""
    from services.api.routers.query import _load_user_history

    fake_session = _make_fake_session([])
    history = _load_user_history(fake_session, "user1")
    assert history == []


def test_load_user_history_single_turn():
    """One Q&A turn loads correctly."""
    from services.api.routers.query import _load_user_history
    from datetime import datetime

    q = MagicMock(
        question="What is the password policy?",
        created_at=datetime(2025, 1, 1, 10, 0),
        answers=[MagicMock(answer="Passwords must be 12+ chars.", verdict="clear")]
    )
    fake_session = _make_fake_session([q])
    history = _load_user_history(fake_session, "user1")
    assert len(history) == 1
    assert history[0]["question"] == "What is the password policy?"
    assert history[0]["answer"] == "Passwords must be 12+ chars."


def test_load_user_history_multiple_turns_order():
    """Multiple turns return oldest first (conversation order)."""
    from services.api.routers.query import _load_user_history
    from datetime import datetime

    q1 = MagicMock(
        question="First question",
        created_at=datetime(2025, 1, 1, 10, 0),
        answers=[MagicMock(answer="First answer", verdict="clear")]
    )
    q2 = MagicMock(
        question="Second question",
        created_at=datetime(2025, 1, 1, 11, 0),
        answers=[MagicMock(answer="Second answer", verdict="clear")]
    )
    q3 = MagicMock(
        question="Third question",
        created_at=datetime(2025, 1, 1, 12, 0),
        answers=[MagicMock(answer="Third answer", verdict="clear")]
    )
    fake_session = _make_fake_session([q3, q2, q1])  # DB returns newest first
    history = _load_user_history(fake_session, "user1", limit=5)
    assert len(history) == 3
    assert history[0]["question"] == "First question"
    assert history[1]["question"] == "Second question"
    assert history[2]["question"] == "Third question"


def test_load_user_history_limit():
    """Limit parameter caps the number of returned turns."""
    from services.api.routers.query import _load_user_history
    from datetime import datetime

    queries = []  # newest first, as the DB returns them
    for i in range(10):
        q = MagicMock(
            question=f"Question {i}",
            created_at=datetime(2025, 1, 1, 10 + i, 0),
            answers=[MagicMock(answer=f"Answer {i}", verdict="clear")]
        )
        queries.insert(0, q)  # Question 9 ends up first (newest)
    fake_session = _make_fake_session(queries)
    history = _load_user_history(fake_session, "user1", limit=5)
    assert len(history) == 5
    # Should be the 5 most recent (Questions 5-9), oldest of those first
    assert history[0]["question"] == "Question 5"
    assert history[4]["question"] == "Question 9"


def test_load_user_history_skips_queries_without_answers():
    """Queries that have no answer row are skipped."""
    from services.api.routers.query import _load_user_history
    from datetime import datetime

    q_with_answer = MagicMock(
        question="Has answer",
        created_at=datetime(2025, 1, 1, 10, 0),
        answers=[MagicMock(answer="Yes", verdict="clear")]
    )
    q_no_answer = MagicMock(
        question="No answer",
        created_at=datetime(2025, 1, 1, 11, 0),
        answers=[]
    )
    fake_session = _make_fake_session([q_with_answer, q_no_answer])
    history = _load_user_history(fake_session, "user1")
    assert len(history) == 1
    assert history[0]["question"] == "Has answer"


def test_build_pipeline_question_no_history():
    """Raw question returned when no history."""
    from services.api.routers.query import _build_pipeline_question
    assert _build_pipeline_question("What is X?", []) == "What is X?"


def test_build_pipeline_question_with_history():
    """Conversation history is prepended correctly."""
    from services.api.routers.query import _build_pipeline_question
    history = [
        {"question": "What is the password policy?", "answer": "Passwords must be 12+ chars."},
        {"question": "What about sharing?", "answer": "Passwords shall not be shared."}
    ]
    result = _build_pipeline_question("What about length?", history)
    assert "Conversation History:" in result
    assert "User: What is the password policy?" in result
    assert "Codex: Passwords must be 12+ chars." in result
    assert "User: What about sharing?" in result
    assert "Codex: Passwords shall not be shared." in result
    assert "Question: What about length?" in result


def test_build_pipeline_question_respects_token_budget():
    """Older turns are dropped when the working-memory budget is exceeded."""
    from services.api.routers.query import _build_pipeline_question

    history = [
        {"question": "Old question", "answer": "old answer"},
        {"question": "Recent question", "answer": "recent answer"},
    ]
    result = _build_pipeline_question("Current question", history, max_history_tokens=8)
    assert "Recent question" in result
    assert "Current question" in result
    assert "Old question" not in result


def test_build_pipeline_question_keeps_chronological_order_after_budgeting():
    """Selected recent turns remain in conversation order."""
    from services.api.routers.query import _build_pipeline_question

    history = [
        {"question": "Turn one", "answer": "Answer one"},
        {"question": "Turn two", "answer": "Answer two"},
        {"question": "Turn three", "answer": "Answer three"},
    ]
    result = _build_pipeline_question("Current", history, max_history_tokens=20)
    assert "Turn one" not in result
    assert result.index("Turn two") < result.index("Turn three")


def main():
    print("\n" + "=" * 60)
    print("HISTORY TESTS")
    print("=" * 60)
    tests = [
        ("Empty history", test_load_user_history_empty),
        ("Single turn", test_load_user_history_single_turn),
        ("Multiple turns order", test_load_user_history_multiple_turns_order),
        ("Limit parameter", test_load_user_history_limit),
        ("Skips queries without answers", test_load_user_history_skips_queries_without_answers),
        ("Build question no history", test_build_pipeline_question_no_history),
        ("Build question with history", test_build_pipeline_question_with_history),
        ("History token budget", test_build_pipeline_question_respects_token_budget),
        ("History chronological order", test_build_pipeline_question_keeps_chronological_order_after_budgeting),
    ]
    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True))
            print(f"{name}: ✅ PASSED")
        except AssertionError as e:
            results.append((name, False))
            print(f"{name}: ❌ FAILED ({e})")

    all_passed = all(p for _, p in results)
    print("=" * 60)
    if all_passed:
        print("🎉 All history tests PASSED!")
    else:
        print("⚠️ Some history tests FAILED")
    return all_passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
