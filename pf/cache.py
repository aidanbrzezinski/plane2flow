"""On-disk cache for relation lookups.

One request per work item against a 60/minute limit means a full pull of a
173-item project takes about three minutes. Almost nothing changes between
runs, so each item's relation blob is cached against its `updated_at` stamp:
touch a work item in Plane and it is re-fetched, leave it alone and it is free.

This is a cache, not a store -- delete the file and the only cost is time.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path


class RelationCache:
    VERSION = 1

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self.dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if blob.get("version") == self.VERSION:
            self._data = blob.get("items", {})

    def get(self, item_id: str, stamp: str) -> dict | None:
        row = self._data.get(item_id)
        if row is not None and row.get("stamp") == stamp:
            self.hits += 1
            return row.get("blob") or {}
        self.misses += 1
        return None

    def put(self, item_id: str, stamp: str, blob: dict) -> None:
        with self._lock:
            self._data[item_id] = {"stamp": stamp, "blob": blob}
            self.dirty = True

    def prune(self, live_ids: set[str]) -> None:
        with self._lock:
            for gone in set(self._data) - live_ids:
                del self._data[gone]
                self.dirty = True

    def save(self) -> None:
        if not self.path or not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(
                {"version": self.VERSION, "items": self._data}),
                encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass          # a cache that cannot be written is not an error
