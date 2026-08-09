"""Disk-backed JSON cache with a TTL.

Used to avoid hammering external APIs on repeated --live runs and to make
consecutive runs within the TTL window return identical numbers (useful
when comparing protocols/domains on "the same" input in mpc_bench).
"""

import json
import time
from pathlib import Path


class Cache:
    def __init__(self, directory, ttl_seconds=300):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.directory.mkdir(parents=True, exist_ok=True)

    def _file(self, key):
        return self.directory / f"{key}.json"

    def get(self, key):
        f = self._file(key)
        if not f.exists():
            return None
        record = json.loads(f.read_text())
        if time.time() - record["_cached_at"] > self.ttl_seconds:
            return None
        return record["value"]

    def set(self, key, value):
        record = {"_cached_at": time.time(), "value": value}
        self._file(key).write_text(json.dumps(record))
