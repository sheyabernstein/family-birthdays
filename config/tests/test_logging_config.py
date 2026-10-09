import logging

from config.logging_config import add_source_location, render_exception


def _raise_from_two_frames() -> None:
    def inner() -> None:
        raise ValueError("boom")

    inner()


def test_render_exception_is_a_no_op_without_exc_info() -> None:
    event_dict = {"event": "something happened", "foo": "bar"}

    result = render_exception(None, "warning", dict(event_dict))

    assert result == event_dict


def test_render_exception_builds_a_structured_object_from_an_exception_instance() -> None:
    try:
        _raise_from_two_frames()
    except ValueError as exc:
        result = render_exception(None, "warning", {"exc_info": exc})

    exception = result["exception"]
    assert exception["type"] == "ValueError"
    assert exception["message"] == "boom"
    assert exception["file"] == __file__
    assert exception["line"] == 8  # the `raise` line inside inner()


def test_render_exception_captures_the_full_call_chain_in_order() -> None:
    try:
        _raise_from_two_frames()
    except ValueError as exc:
        result = render_exception(None, "warning", {"exc_info": exc})

    functions = [frame["function"] for frame in result["exception"]["stack"]]
    assert functions == [
        "test_render_exception_captures_the_full_call_chain_in_order",
        "_raise_from_two_frames",
        "inner",
    ]


def test_render_exception_handles_exc_info_true_via_sys_exc_info() -> None:
    try:
        _raise_from_two_frames()
    except ValueError:
        result = render_exception(None, "warning", {"exc_info": True})

    assert result["exception"]["type"] == "ValueError"
    assert result["exception"]["message"] == "boom"


def test_render_exception_pops_exc_info_so_it_never_reaches_the_json_renderer() -> None:
    try:
        _raise_from_two_frames()
    except ValueError as exc:
        result = render_exception(None, "warning", {"exc_info": exc})

    assert "exc_info" not in result


def test_add_source_location_is_a_no_op_below_warning() -> None:
    event_dict = {"level": "info", "event": "quiet"}

    result = add_source_location(None, "info", dict(event_dict))

    assert result == event_dict


def test_add_source_location_stamps_the_structlog_call_site() -> None:
    result = add_source_location(None, "warning", {"level": "warning"})

    source = result["source"]
    assert source["file"] == __file__
    assert source["function"] == "test_add_source_location_stamps_the_structlog_call_site"
    assert "lineno" not in result
    assert "pathname" not in result
    assert "func_name" not in result


def test_add_source_location_reads_a_foreign_log_records_own_location() -> None:
    record = logging.LogRecord(
        name="django",
        level=logging.WARNING,
        pathname="/app/django/core/handlers.py",
        lineno=42,
        msg="something",
        args=(),
        exc_info=None,
        func="dispatch",
    )
    event_dict = {"level": "warning", "_record": record, "_from_structlog": False}

    result = add_source_location(None, "warning", event_dict)

    assert result["source"] == {
        "file": "/app/django/core/handlers.py",
        "line": 42,
        "function": "dispatch",
    }
