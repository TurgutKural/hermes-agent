"""Kanban board path resolution for an execution that runs under env pins.

Split out of :mod:`hermes_cli.kanban_db` because that facade is over the
code-health line cap and may not grow; the behaviour lives here and the facade
keeps a one-line entry point.

The rule this module adds: when a caller passes an explicit ``board=`` argument
inside a fenced execution (a dispatcher-spawned worker or a delegated child, so
``HERMES_KANBAN_DB``-style pins are authoritative) and that board is *not* the
pinned one, resolution raises :class:`BoardPinMismatch` instead of quietly
returning the pin. The old silent form reported success while the write landed
on the pinned board.

Isolation is unchanged: the pin still wins, and no path outside it is ever
produced. Only the silence is gone.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class BoardPinMismatch(RuntimeError):
    """Explicit ``board=`` names a board the pinned execution cannot reach.

    Raised instead of resolving to the pin, so a write can never land on a board
    the caller did not name (``kanban_create(board="bugbounty")`` used to return
    ``ok: true`` while the card was written to the pinned ``default`` board).
    """


def _argument_slug(kb, board: Optional[str]) -> Optional[str]:
    """Slug from a direct ``board=`` argument only.

    The scoped ``--board`` context is *not* caller intent here: PR #102692 owns
    that path and resolves it to the flagged board, so it must not raise.
    """
    if board is None:
        return None
    return kb._normalize_board_slug(board)


def _slug_path(kb, slug: str, default_parts: tuple, leaf: str) -> Path:
    """Where ``slug`` lives, ignoring every env pin."""
    if slug == kb.DEFAULT_BOARD:
        return kb.kanban_home().joinpath(*default_parts)
    return kb.board_dir(slug) / leaf


def _paths_agree(pinned: Path, target: Path) -> bool:
    """Same physical file? Tolerant of ``~``, relative pins and symlinks."""
    try:
        return pinned.expanduser().resolve() == target.expanduser().resolve()
    except OSError:  # pragma: no cover - unreadable symlink chain
        return pinned.expanduser() == target.expanduser()


def pin_spawned_worker_env(env: dict, board: Optional[str]) -> None:
    """Pin a spawned worker's board paths to the board the dispatcher ticks.

    Machine flow, not caller intent: ``board`` is the enumerated slug, so a
    dispatcher that is itself env-pinned grants the pin it runs on instead of
    tripping the explicit-board refusal against itself.
    """
    from hermes_cli import kanban_db as kb

    with kb.pin_first_board_resolution():
        env["HERMES_KANBAN_DB"] = str(kb.kanban_db_path(board=board))
        env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(kb.workspaces_root(board=board))
        # Defense-in-depth pin if a path is resolved without the env vars above.
        env["HERMES_KANBAN_BOARD"] = kb._normalize_board_slug(board) or kb.get_current_board()


def resolve_board_path(
    env_var: Optional[str], board: Optional[str], default_parts: tuple, leaf: str,
) -> Path:
    """Resolve one board-scoped path; see the module docstring for the contract."""
    from hermes_cli import kanban_db as kb

    pin = os.environ.get(env_var, "").strip() if env_var else ""
    slug = kb._explicit_board_slug(board)
    if pin:
        pinned = Path(pin).expanduser()
        if slug is None:
            return pinned
        if kb._PIN_FIRST_BOARD_RESOLUTION.get():
            # Machine enumeration (notifier / watcher / dispatcher ticks): the
            # enumerated slug is not caller intent, so every slug maps to the pin.
            return pinned
        fenced = kb._explicit_board_intent_pinned()
        if fenced:
            argument_slug = _argument_slug(kb, board)
            if argument_slug is not None and not _paths_agree(
                pinned, _slug_path(kb, argument_slug, default_parts, leaf)
            ):
                raise BoardPinMismatch(
                    f"kanban board {argument_slug!r} is not available to this execution: "
                    f"{env_var} pins this session to {pinned}. Dispatched workers and "
                    f"delegated children cannot reach another board, so the request is "
                    f"refused instead of being written to the pinned board. Run it from "
                    f"a session that owns {argument_slug!r}, or unset {env_var}."
                )
            return pinned
        return _slug_path(kb, slug, default_parts, leaf)
    if slug is None:
        slug = kb.get_current_board()
    return _slug_path(kb, slug, default_parts, leaf)
