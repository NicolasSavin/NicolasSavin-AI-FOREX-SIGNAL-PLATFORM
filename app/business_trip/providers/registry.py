from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ProviderCapabilities:
    supports_schedule: bool=False; supports_availability: bool=False; supports_carriages: bool=False; supports_place_map: bool=False; supports_compartment_rules: bool=False; supports_gender_restrictions: bool=False

@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str; configured: bool; health: str; description: str; capabilities: ProviderCapabilities=field(default_factory=ProviderCapabilities)

class ProviderRegistry:
    def __init__(self) -> None:
        caps=ProviderCapabilities(True, True, True, True, True, True)
        self._statuses={
            'myagent': ProviderStatus('myagent', False, 'disabled', 'Требуется партнёрский доступ', caps),
            'teletrain': ProviderStatus('teletrain', False, 'disabled', 'Требуется партнёрский доступ', caps),
        }
    def status(self, provider_id: str) -> ProviderStatus: return self._statuses[provider_id]
    def all_statuses(self) -> list[ProviderStatus]: return list(self._statuses.values())
