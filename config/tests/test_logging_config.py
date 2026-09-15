from config.logging_config import render_exception


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
    assert exception["line"] == 6  # the `raise` line inside inner()


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
