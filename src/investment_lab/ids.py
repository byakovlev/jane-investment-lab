from __future__ import annotations

import hashlib


def stable_bigint(namespace: str, key: str) -> int:
    """Deterministic positive 63-bit ID. Stable across machines/re-ingests."""
    digest = hashlib.sha256(f"{namespace}\0{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
