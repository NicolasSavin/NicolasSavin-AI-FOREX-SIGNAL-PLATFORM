from __future__ import annotations
import os
from dataclasses import dataclass
@dataclass(frozen=True)
class TeletrainConfiguration:
    enabled: bool = False; base_url: str = ''; login: str = ''; password: str = ''; timeout_seconds: int = 20
    @classmethod
    def from_env(cls) -> 'TeletrainConfiguration':
        return cls(os.getenv('TELETRAIN_ENABLED','false').lower()=='true', os.getenv('TELETRAIN_BASE_URL',''), os.getenv('TELETRAIN_LOGIN',''), os.getenv('TELETRAIN_PASSWORD',''), int(os.getenv('TELETRAIN_TIMEOUT_SECONDS','20')))
    @property
    def configured(self) -> bool: return self.enabled and bool(self.base_url and self.login and self.password)
    def safe_dict(self) -> dict[str, object]: return {'enabled': self.enabled, 'base_url_configured': bool(self.base_url), 'login_configured': bool(self.login), 'password_configured': bool(self.password), 'timeout_seconds': self.timeout_seconds}
