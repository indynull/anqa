"""Host environment checks (config home, catalog, control owner, HUD seat)."""

from __future__ import annotations

from .self_test import CheckResult, SelfTestReport, run_self_test

__all__ = ["CheckResult", "SelfTestReport", "run_self_test"]
