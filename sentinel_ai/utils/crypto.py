"""
Sentinel-AI Cryptographic Audit Utilities.

Implements SHA-256 hash chain for tamper-evident Agent Decision Records.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional


class HashChain:
    """
    SHA-256 hash chain for cryptographically verifiable audit trail.
    
    Each record's hash is computed from its contents plus the previous
    record's hash, creating a tamper-evident chain similar to blockchain.
    """

    def __init__(self, algorithm: str = "sha256"):
        self.algorithm = algorithm
        self._last_hash: str = self._genesis_hash()

    def _genesis_hash(self) -> str:
        """Create the genesis (initial) hash for the chain."""
        genesis = {
            "type": "genesis",
            "timestamp": "2026-01-01T00:00:00Z",
            "system": "sentinel-ai",
            "version": "1.0.0",
        }
        return self._compute_hash(json.dumps(genesis, sort_keys=True))

    def _compute_hash(self, data: str) -> str:
        """Compute hash of data using configured algorithm."""
        h = hashlib.new(self.algorithm)
        h.update(data.encode("utf-8"))
        return h.hexdigest()

    def add_record(self, record: dict) -> str | tuple[str, str]:
        """
        Add a record to the chain and return its hash.
        
        The hash is computed from:
        - The serialized record content
        - The previous record's hash
        - A timestamp
        
        Returns the record hash string. Use add_record_full() for the full tuple.
        """
        ts = datetime.now(timezone.utc).isoformat()
        chain_data = {
            "previous_hash": self._last_hash,
            "record": record,
            "chain_timestamp": ts,
        }
        record_hash = self._compute_hash(
            json.dumps(chain_data, sort_keys=True, default=str)
        )
        self._last_hash = record_hash
        self._last_chain_timestamp = ts
        return record_hash

    def add_record_full(self, record: dict) -> tuple[str, str]:
        """Add a record and return (hash, chain_timestamp)."""
        record_hash = self.add_record(record)
        return record_hash, self._last_chain_timestamp

    def get_last_hash(self) -> str:
        """Get the most recent hash in the chain."""
        return self._last_hash

    @staticmethod
    def verify_record(record: dict, previous_hash: str, expected_hash: str,
                      chain_timestamp: str, algorithm: str = "sha256") -> bool:
        """
        Verify that a single record in the chain has not been tampered with.
        
        Args:
            record: The original record data
            previous_hash: The hash of the previous record in the chain
            expected_hash: The hash that was stored for this record
            chain_timestamp: The timestamp when the record was chained
            algorithm: Hash algorithm used
            
        Returns:
            True if the record is valid, False if tampered
        """
        chain_data = {
            "previous_hash": previous_hash,
            "record": record,
            "chain_timestamp": chain_timestamp,
        }
        h = hashlib.new(algorithm)
        h.update(json.dumps(chain_data, sort_keys=True, default=str).encode("utf-8"))
        computed = h.hexdigest()
        return computed == expected_hash

    @staticmethod
    def verify_chain(records: list[dict], algorithm: str = "sha256") -> dict:
        """
        Verify the integrity of an entire hash chain.
        
        Args:
            records: Ordered list of audit records, each containing:
                - 'record_hash': the stored hash
                - 'previous_hash': hash of the previous record
                - 'chain_timestamp': when it was chained
                - 'record_data': the original record content
                
        Returns:
            Dictionary with verification results:
            {
                "valid": bool,
                "total_records": int,
                "verified_records": int,
                "first_invalid_index": int | None,
                "first_invalid_reason": str | None,
            }
        """
        result = {
            "valid": True,
            "total_records": len(records),
            "verified_records": 0,
            "first_invalid_index": None,
            "first_invalid_reason": None,
        }

        for i, rec in enumerate(records):
            chain_data = {
                "previous_hash": rec["previous_hash"],
                "record": rec["record_data"],
                "chain_timestamp": rec["chain_timestamp"],
            }
            h = hashlib.new(algorithm)
            h.update(json.dumps(chain_data, sort_keys=True, default=str).encode("utf-8"))
            computed = h.hexdigest()

            if computed != rec["record_hash"]:
                result["valid"] = False
                result["first_invalid_index"] = i
                result["first_invalid_reason"] = f"Hash mismatch at record {i}: expected {rec['record_hash']}, got {computed}"
                break

            # Check chain continuity (except for the first record)
            if i > 0 and rec["previous_hash"] != records[i - 1]["record_hash"]:
                result["valid"] = False
                result["first_invalid_index"] = i
                result["first_invalid_reason"] = f"Chain break at record {i}: previous_hash doesn't match prior record"
                break

            result["verified_records"] = i + 1

        return result


def compute_record_fingerprint(record: dict) -> str:
    """Compute a deterministic fingerprint of a record for deduplication."""
    canonical = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
