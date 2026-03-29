"""Tests for the audit trail and cryptographic hash chain."""

import pytest
from sentinel_ai.utils.crypto import HashChain, compute_record_fingerprint
from sentinel_ai.models.audit import create_audit_record, AgentDecisionRecord


class TestHashChain:
    """Test the SHA-256 hash chain."""

    def test_genesis_hash(self):
        chain = HashChain()
        genesis = chain.get_last_hash()
        assert len(genesis) == 64  # SHA-256 hex length

    def test_add_record(self):
        chain = HashChain()
        hash1 = chain.add_record({"test": "data1"})
        hash2 = chain.add_record({"test": "data2"})
        assert hash1 != hash2
        assert len(hash1) == 64
        assert len(hash2) == 64

    def test_chain_deterministic(self):
        # Same data should produce different hashes due to timestamps
        chain = HashChain()
        hash1 = chain.add_record({"test": "data"})
        hash2 = chain.add_record({"test": "data"})
        # Different because previous_hash and timestamp differ
        assert hash1 != hash2

    def test_verify_chain_valid(self):
        chain = HashChain()
        records = []
        prev_hash = chain.get_last_hash()

        for i in range(5):
            data = {"index": i, "value": f"test_{i}"}
            record_hash, chain_ts = chain.add_record_full(data)

            records.append({
                "record_hash": record_hash,
                "previous_hash": prev_hash,
                "chain_timestamp": chain_ts,
                "record_data": data,
            })
            prev_hash = record_hash

        result = HashChain.verify_chain(records)
        assert result["valid"]
        assert result["verified_records"] == 5

    def test_verify_chain_tampered(self):
        chain = HashChain()
        records = []
        prev_hash = chain.get_last_hash()

        for i in range(3):
            data = {"index": i}
            record_hash, chain_ts = chain.add_record_full(data)
            records.append({
                "record_hash": record_hash,
                "previous_hash": prev_hash,
                "chain_timestamp": chain_ts,
                "record_data": data,
            })
            prev_hash = record_hash

        # Tamper with middle record
        records[1]["record_data"]["index"] = 999

        result = HashChain.verify_chain(records)
        assert not result["valid"]
        assert result["first_invalid_index"] == 1

    def test_fingerprint(self):
        fp1 = compute_record_fingerprint({"a": 1, "b": 2})
        fp2 = compute_record_fingerprint({"b": 2, "a": 1})
        assert fp1 == fp2  # Order-independent
        assert len(fp1) == 16


class TestAuditRecord:
    """Test Agent Decision Record creation."""

    def test_create_record(self):
        record = create_audit_record(
            agent="test_agent",
            trigger_event="test_event",
            context="Test context",
            decision="Approve",
            reasoning="All checks passed",
            confidence=0.95,
            action_taken="Processed request",
            why="Met all criteria",
            trade_offs="None",
        )
        assert record.agent == "test_agent"
        assert record.confidence == 0.95
        assert record.record_hash  # Hash should be populated
        assert record.previous_hash  # Previous hash should be populated

    def test_record_spec_format(self):
        record = create_audit_record(
            agent="policy",
            trigger_event="compliance_check",
            context="Invoice validation",
            decision="Approve",
            reasoning="All rules passed",
            confidence=0.88,
            action_taken="Invoice approved",
        )
        spec = record.to_spec_format()
        assert "decision_id" in spec
        assert "timestamp" in spec
        assert spec["agent"] == "policy"
        assert spec["confidence"] == 0.88
