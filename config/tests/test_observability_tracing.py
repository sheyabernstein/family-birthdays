import pytest
from django.test import override_settings

import config.observability.tracing as tracing_module
from config.observability.tracing import _normalized_headers, flush_tracing


@pytest.mark.parametrize(
    ["header_pairs", "expected"],
    [
        [[], {}],
        [["k=v"], {"k": "v"}],
        [["k=v", "k2=v2"], {"k": "v", "k2": "v2"}],
        [[" k = v "], {"k": "v"}],
        [["novalue="], {"novalue": ""}],
    ],
    ids=[
        "empty list produces an empty dict",
        "a single pair",
        "several pairs",
        "surrounding whitespace is stripped",
        "an empty value is kept, not dropped",
    ],
)
def test_normalized_headers_parses_key_value_pairs(header_pairs, expected):
    with override_settings(OTEL_EXPORTER_OTLP_HEADERS=header_pairs):
        assert _normalized_headers() == expected


def test_normalized_headers_skips_a_pair_with_no_key():
    with override_settings(OTEL_EXPORTER_OTLP_HEADERS=["=novalue"]):
        assert _normalized_headers() == {}


def test_flush_tracing_is_a_no_op_before_init_tracing_has_run(monkeypatch):
    """init_tracing() itself isn't unit-tested here: it sets the process-
    wide OTel TracerProvider (the SDK deliberately refuses to let that be
    overridden more than once) and permanently instruments Django/Celery/
    Redis/psycopg2/botocore - real, hard-to-reset global state that's a
    poor fit for an in-process test suite. Verified live instead (see
    AGENTS.md's Observability section) against a running docker stack."""
    monkeypatch.setattr(tracing_module, "_PROVIDER", None)

    flush_tracing()  # must not raise
