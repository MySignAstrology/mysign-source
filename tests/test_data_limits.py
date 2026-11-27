"""
Storage ceilings, measured the way DynamoDB measures them — in UTF-8 bytes.
"""
import datetime as dt
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-limits-"))

from engine import memory as M
from engine import store_dynamo
from engine.store import keys, load


def test_non_latin_text_is_stored_as_itself_not_escapes():
    blob = store_dynamo.dumps({"t": "почему он не отвечает"})
    assert "почему" in blob and "\\u" not in blob


def test_size_is_counted_in_bytes():
    import pytest
    big = "я" * 200_000                     # 200k characters, 400k bytes
    with pytest.raises(ValueError):
        store_dynamo._check_size("u", "k", store_dynamo.dumps(big))


def test_questions_stay_under_their_byte_budget_in_any_script():
    uid = "q-cyrillic"
    for i in range(400):
        M.remember_question(uid, "почему я всегда так много объясняю " * 8)
    qs = load(uid, "questions", [])
    size = len(store_dynamo.dumps(qs).encode("utf-8"))
    assert size <= M.QUESTIONS_BYTES and len(qs) > 50


def test_old_episodes_move_to_a_yearly_archive_and_stay_searchable():
    uid = "ep-archive"
    start = dt.date(2024, 1, 1)
    for i in range(M.EPISODES_KEEP + 40):
        M.upsert_episode(uid, {"date": (start + dt.timedelta(days=i)).isoformat(),
                               "summary": f"day {i}", "her_words": []})
    assert len(load(uid, "episodes", [])) == M.EPISODES_KEEP
    assert "episodes-2024" in keys(uid)
    every = M.all_episodes(uid)
    assert len(every) == M.EPISODES_KEEP + 40
    assert every[0]["date"] == "2024-01-01"
    dates = [e["date"] for e in every]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_error_logs_never_carry_the_message(capsys):
    from app import log
    words = "i miss him " + "so much"          # her text, arriving at runtime
    try:
        raise ValueError(f"input_value={words!r}")
    except ValueError as e:
        log.error("memory.extract.failed", exc=e)
        try:
            raise
        except ValueError:
            log.exception("request.failed")
    out = capsys.readouterr().out
    assert "i miss him" not in out and "ValueError" in out


def test_mentions_keep_only_recent_weeks():
    from engine.store import log_mention
    uid = "mentions-cap"
    for i in range(80):
        log_mention(uid, (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(), ["a"])
    assert len(load(uid, "mentions", {})) == 45
