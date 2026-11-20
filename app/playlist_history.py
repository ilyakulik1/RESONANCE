"""In-memory undo/redo stack for playlist edits."""

from __future__ import annotations

from typing import Callable

Snapshot = dict
Capture = Callable[[], Snapshot]


class EditHistory:
    """Snapshots taken before an edit. Nested edits share one snapshot."""

    def __init__(self, limit: int = 40):
        self.undo_stack: list[Snapshot] = []
        self.redo_stack: list[Snapshot] = []
        self.limit = limit
        self.locked = False
        self.depth = 0

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.depth = 0

    def begin(self, capture: Capture) -> bool:
        """Start an edit. Returns True when this call opened the outer batch."""
        if self.locked:
            return False
        outer = self.depth == 0
        if outer:
            self.undo_stack.append(capture())
            if len(self.undo_stack) > self.limit:
                del self.undo_stack[: len(self.undo_stack) - self.limit]
            self.redo_stack.clear()
        self.depth += 1
        return outer

    def end(self, capture: Capture) -> bool:
        """Close an edit. Returns True when the outer edit actually changed state."""
        if self.locked or self.depth <= 0:
            return False
        self.depth -= 1
        if self.depth > 0 or not self.undo_stack:
            return False
        if capture() == self.undo_stack[-1]:
            self.undo_stack.pop()
            return False
        return True

    def can_undo(self) -> bool:
        return bool(self.undo_stack) and self.depth == 0 and not self.locked

    def can_redo(self) -> bool:
        return bool(self.redo_stack) and self.depth == 0 and not self.locked

    def pop_undo(self, current: Snapshot) -> Snapshot | None:
        if not self.can_undo():
            return None
        previous = self.undo_stack.pop()
        self.redo_stack.append(current)
        if len(self.redo_stack) > self.limit:
            del self.redo_stack[: len(self.redo_stack) - self.limit]
        return previous

    def pop_redo(self, current: Snapshot) -> Snapshot | None:
        if not self.can_redo():
            return None
        nxt = self.redo_stack.pop()
        self.undo_stack.append(current)
        if len(self.undo_stack) > self.limit:
            del self.undo_stack[: len(self.undo_stack) - self.limit]
        return nxt
