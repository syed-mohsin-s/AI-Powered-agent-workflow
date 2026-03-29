"""Tests for structured logger behavior."""

import logging

from sentinel_ai.utils.logger import JSONFormatter, SentinelLogger


def test_json_formatter_handles_boolean_exc_info_safely():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="sentinel.test",
        level=logging.ERROR,
        pathname="test",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=True,
    )

    output = formatter.format(record)
    assert "test message" in output


def test_sentinel_logger_converts_exc_info_true_without_crashing():
    logger = SentinelLogger("test.logger")

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.error("failure path", exc_info=True)

    assert True
