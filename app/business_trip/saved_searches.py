from __future__ import annotations
from .models import SavedSearch
class SavedSearchRepository:
    def __init__(self) -> None: self._items: dict[str, SavedSearch] = {}
    def save(self, item: SavedSearch) -> SavedSearch:
        self._items[item.id]=item; return item
    def get(self, item_id: str) -> SavedSearch | None: return self._items.get(item_id)
