"""Shared service package initialization."""
from __future__ import annotations

from app.services.options_legacy_patch import install_trade_idea_options_patch

install_trade_idea_options_patch()
