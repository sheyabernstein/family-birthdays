from unittest.mock import MagicMock, patch

from django.test import override_settings

import config.observability.sentry as sentry_module
from config.exceptions import FamilyBirthdaysError
from config.observability.sentry import _record_exception_and_capture, init_sentry


class _DomainError(FamilyBirthdaysError):
    pass


@patch("config.observability.sentry._original_record_exception")
@patch("config.observability.sentry.sentry_sdk.capture_exception")
def test_record_exception_and_capture_reports_a_genuine_bug(mock_capture, mock_original):
    span = MagicMock()
    exc = ValueError("boom")

    _record_exception_and_capture(span, exc)

    mock_capture.assert_called_once_with(exc)
    mock_original.assert_called_once_with(span, exc)


@patch("config.observability.sentry._original_record_exception")
@patch("config.observability.sentry.sentry_sdk.capture_exception")
def test_record_exception_and_capture_skips_a_familybirthdayserror(mock_capture, mock_original):
    span = MagicMock()
    exc = _DomainError("already handled")

    _record_exception_and_capture(span, exc)

    mock_capture.assert_not_called()
    mock_original.assert_called_once_with(span, exc)


@override_settings(SENTRY_ENABLED=False)
def test_init_sentry_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(sentry_module, "_initialized", False)
    with patch("config.observability.sentry.sentry_sdk.init") as mock_init:
        init_sentry()

    mock_init.assert_not_called()


@override_settings(
    SENTRY_ENABLED=True,
    SENTRY_DSN="https://example.invalid/1",
    SENTRY_ENVIRONMENT="test",
    SENTRY_TRACES_ENABLED=True,
)
def test_init_sentry_initializes_once_and_is_idempotent(monkeypatch):
    """Also patches OTelSpan (a stand-in class, not the real
    opentelemetry.sdk.trace.Span) - init_sentry() reassigns
    OTelSpan.record_exception as a side effect, and letting that hit the
    real SDK class would leak the patch into every other test in the
    suite that touches a real span afterward."""
    monkeypatch.setattr(sentry_module, "_initialized", False)
    monkeypatch.setattr(sentry_module, "OTelSpan", type("FakeSpan", (), {}))
    with (
        patch("config.observability.sentry.sentry_sdk.init") as mock_init,
        patch("config.observability.sentry.otel_trace.get_tracer_provider") as mock_provider,
        patch("config.observability.sentry.set_global_textmap"),
    ):
        init_sentry()
        init_sentry()

    mock_init.assert_called_once()
    assert mock_init.call_args.kwargs["traces_sample_rate"] == 1.0
    mock_provider.assert_called_once()


@override_settings(
    SENTRY_ENABLED=True,
    SENTRY_DSN="https://example.invalid/1",
    SENTRY_ENVIRONMENT="test",
    SENTRY_TRACES_ENABLED=False,
)
def test_init_sentry_skips_span_mirroring_when_traces_disabled(monkeypatch):
    """Exceptions still reach Sentry either way (a separate code path -
    see the record_exception tests above) - this only covers the trace-
    mirroring half turning off cleanly, not exception capture itself."""
    monkeypatch.setattr(sentry_module, "_initialized", False)
    monkeypatch.setattr(sentry_module, "OTelSpan", type("FakeSpan", (), {}))
    with (
        patch("config.observability.sentry.sentry_sdk.init") as mock_init,
        patch("config.observability.sentry.otel_trace.get_tracer_provider") as mock_provider,
        patch("config.observability.sentry.set_global_textmap") as mock_textmap,
    ):
        init_sentry()

    assert mock_init.call_args.kwargs["traces_sample_rate"] == 0.0
    mock_provider.assert_not_called()
    mock_textmap.assert_not_called()
