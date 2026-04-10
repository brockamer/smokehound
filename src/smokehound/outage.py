"""Outage detection logic."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import OutageConfig
from .db import Database


@dataclass
class OutageState:
    """Track rolling outage detection state."""

    config: OutageConfig
    db: Database
    run_id: int

    _in_outage: bool = field(default=False, init=False)
    _outage_id: int | None = field(default=None, init=False)
    _outage_start: float = field(default=0.0, init=False)
    _max_loss_pct: float = field(default=0.0, init=False)
    _window_losses: list[float] = field(default_factory=list, init=False)

    def update(self, ping_results: list[dict[str, Any]]) -> bool:
        """
        Feed new ping results. Returns True if outage state changed.
        """
        now = time.time()

        # Compute average packet loss across all targets this cycle
        losses = [r["loss_pct"] for r in ping_results if r.get("loss_pct") is not None]
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        # Determine if this cycle is "bad"
        is_bad = avg_loss >= self.config.loss_threshold_percent

        changed = False
        if is_bad and not self._in_outage:
            # Potential outage start — open tentatively
            self._in_outage = True
            self._outage_start = now
            self._outage_id = self.db.open_outage(self.run_id, now, f"loss={avg_loss:.0f}%")
            self._max_loss_pct = avg_loss
            changed = True

        elif is_bad and self._in_outage:
            # Continuing outage
            self._max_loss_pct = max(self._max_loss_pct, avg_loss)
            duration = now - self._outage_start
            if duration >= self.config.duration_threshold_seconds and self._outage_id:
                # Confirmed real outage (not a blip) — keep it open
                pass

        elif not is_bad and self._in_outage:
            # Recovery — close outage
            duration = now - self._outage_start
            if duration >= self.config.duration_threshold_seconds and self._outage_id:
                self.db.close_outage(self._outage_id, now, self._max_loss_pct)
            elif self._outage_id:
                # Was a blip, delete tentative record (treat as noise)
                self.db.execute("DELETE FROM outage_events WHERE id = ?", (self._outage_id,))
            self._in_outage = False
            self._outage_id = None
            self._outage_start = 0.0
            self._max_loss_pct = 0.0
            changed = True

        return changed

    def finalize(self) -> None:
        """Close any open outage on shutdown."""
        if self._in_outage and self._outage_id:
            self.db.close_outage(self._outage_id, time.time(), self._max_loss_pct)
