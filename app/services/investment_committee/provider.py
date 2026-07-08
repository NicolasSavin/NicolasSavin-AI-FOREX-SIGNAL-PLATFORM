from __future__ import annotations

from typing import Protocol

from app.services.investment_committee.models import CommitteeInput, InvestmentCommitteeReport


class InvestmentCommitteeProvider(Protocol):
    name: str

    def evaluate(self, context: CommitteeInput) -> InvestmentCommitteeReport:
        """Build the final provider-independent committee report."""
