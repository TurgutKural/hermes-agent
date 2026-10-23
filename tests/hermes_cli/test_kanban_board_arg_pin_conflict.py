"""An explicit board= inside an env-pinned execution must not be silently pinned.

Regression cover for the incident shape: a dispatched worker (or a delegated
child) called ``kanban_create(board="bugbounty")``, the tool answered ``ok: true``
with a task id, and the card was written to the pinned board instead. The pin
still fences the execution — the request is now refused instead of re-routed.
"""
import pytest

from hermes_cli import kanban_db as kb


def _pin(monkeypatch, path):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(path))


def test_dispatched_worker_own_board_resolves_to_the_pin(monkeypatch, tmp_path):
    """The correct board keeps working: a worker's own board is the pin."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    own = kb.kanban_db_path(board="workerboard")
    _pin(monkeypatch, own)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_3f1c63a5")
    assert kb.kanban_db_path(board="workerboard") == own


def test_dispatched_worker_foreign_board_is_refused(monkeypatch, tmp_path):
    """A board the worker was not granted is refused, never silently pinned.

    The dispatcher grants both pins, so both are set here.
    """
    _pin(monkeypatch, tmp_path / "worker.db")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(tmp_path / "worker-ws"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_3f1c63a5")
    with pytest.raises(kb.BoardPinMismatch):
        kb.kanban_db_path(board="other-board")
    with pytest.raises(kb.BoardPinMismatch):
        kb.workspaces_root(board="other-board")
    # No explicit board still resolves through the pin (worker's normal traffic).
    assert kb.kanban_db_path() == tmp_path / "worker.db"


def test_delegated_child_foreign_board_is_refused(monkeypatch, tmp_path):
    """A delegated child is fenced the same way as a worker."""
    from agent.delegation_context import DELEGATED_CHILD_ENV_MARKER

    _pin(monkeypatch, tmp_path / "worker.db")
    monkeypatch.setenv(DELEGATED_CHILD_ENV_MARKER, str(tmp_path))
    with pytest.raises(kb.BoardPinMismatch):
        kb.kanban_db_path(board="other-board")


def test_scoped_board_flag_keeps_the_pin(monkeypatch, tmp_path):
    """The scoped --board context is not a board= argument: unchanged behaviour.

    PR #102692 owns making that context outrank the pin; this change must not
    pre-empt it, so the pinned path is still returned.
    """
    pinned = tmp_path / "worker.db"
    _pin(monkeypatch, pinned)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_3f1c63a5")
    with kb.scoped_current_board("other-board"):
        assert kb.kanban_db_path() == pinned


def test_refused_board_creates_no_task_in_any_board(monkeypatch, tmp_path):
    """The incident shape: nothing may be written anywhere by a refused call."""
    from hermes_cli import kanban_db_connect as kbc

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(kb.Path, "home", lambda: tmp_path)
    pinned = tmp_path / "worker.db"
    _pin(monkeypatch, pinned)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_3f1c63a5")
    kb.init_db()
    with kbc.connect() as conn:
        kb.create_task(conn, title="pinned task", assignee="worker")

    with pytest.raises(kb.BoardPinMismatch):
        with kbc.connect(board="bugbounty") as conn:
            kb.create_task(conn, title="must not exist", assignee="worker")

    with kbc.connect() as conn:
        titles = [row[0] for row in conn.execute("SELECT title FROM tasks")]
    assert titles == ["pinned task"], f"the pinned board gained a foreign card: {titles}"
    assert not (home / "kanban" / "boards" / "bugbounty" / "kanban.db").exists(), (
        "the refused call still created a database for the foreign board"
    )
