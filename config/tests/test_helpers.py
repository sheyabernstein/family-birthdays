import pytest
from django.core.exceptions import ImproperlyConfigured

from config.helpers import check_email_security_settings, get_env_bool, get_env_int, get_env_list


@pytest.mark.parametrize(
    ["env_value", "default", "expected"],
    [
        [None, False, False],
        [None, True, True],
        ["true", False, True],
        ["True", False, True],
        ["TRUE", False, True],
        ["1", False, True],
        ["yes", False, True],
        ["on", False, True],
        ["false", True, False],
        ["False", True, False],
        ["0", True, False],
        ["no", True, False],
        ["off", True, False],
        ["", True, True],
        ["   ", True, True],
        ["not-a-bool", True, False],
    ],
    ids=[
        "unset falls back to a false default",
        "unset falls back to a true default",
        "lowercase true",
        "titlecase true",
        "uppercase true",
        "numeric 1 is true",
        "yes is true",
        "on is true",
        "lowercase false overrides a true default",
        "titlecase false overrides a true default",
        "numeric 0 overrides a true default",
        "no overrides a true default",
        "off overrides a true default",
        "blank string falls back to the default",
        "whitespace-only string falls back to the default",
        "an unrecognized token is false regardless of the default",
    ],
)
def test_get_env_bool(monkeypatch, env_value, default, expected):
    if env_value is None:
        monkeypatch.delenv("SOME_FLAG", raising=False)
    else:
        monkeypatch.setenv("SOME_FLAG", env_value)

    assert get_env_bool("SOME_FLAG", default) is expected


@pytest.mark.parametrize(
    ["env_value", "default", "expected"],
    [
        [None, 5432, 5432],
        [None, None, None],
        ["587", 25, 587],
        ["-1", 0, -1],
        ["  42  ", 0, 42],
        ["", 25, 25],
        ["not-a-number", 25, 25],
        ["3.14", 25, 25],
    ],
    ids=[
        "unset falls back to an int default",
        "unset falls back to a none default",
        "a valid int overrides the default",
        "a negative int is valid",
        "surrounding whitespace is stripped",
        "blank string falls back to the default",
        "a non-numeric string falls back to the default",
        "a float-looking string falls back to the default",
    ],
)
def test_get_env_int(monkeypatch, env_value, default, expected):
    if env_value is None:
        monkeypatch.delenv("SOME_PORT", raising=False)
    else:
        monkeypatch.setenv("SOME_PORT", env_value)

    assert get_env_int("SOME_PORT", default) == expected


@pytest.mark.parametrize(
    ["env_value", "default", "expected"],
    [
        [None, None, []],
        [None, ["localhost"], ["localhost"]],
        ["a,b,c", None, ["a", "b", "c"]],
        ["a, b , c", None, ["a", "b", "c"]],
        ["a,,b", None, ["a", "b"]],
        ["", ["localhost"], ["localhost"]],
        ["   ", ["localhost"], ["localhost"]],
        ["a|b|c", None, ["a", "b", "c"]],
    ],
    ids=[
        "unset falls back to an empty list when no default given",
        "unset falls back to the given default",
        "a comma-delimited value is split",
        "surrounding whitespace around each entry is stripped",
        "empty entries between delimiters are dropped",
        "blank string falls back to the default",
        "whitespace-only string falls back to the default",
        "a custom delimiter is respected",
    ],
)
def test_get_env_list(monkeypatch, env_value, default, expected):
    if env_value is None:
        monkeypatch.delenv("SOME_LIST", raising=False)
    else:
        monkeypatch.setenv("SOME_LIST", env_value)

    delimiter = "|" if env_value == "a|b|c" else ","
    assert get_env_list("SOME_LIST", default, delimiter=delimiter) == expected


def test_get_env_list_default_is_not_shared_between_calls(monkeypatch):
    monkeypatch.delenv("SOME_LIST", raising=False)

    first = get_env_list("SOME_LIST")
    first.append("mutated")

    assert get_env_list("SOME_LIST") == []


@pytest.mark.parametrize(
    ["use_tls", "use_ssl"],
    [
        [False, False],
        [True, False],
        [False, True],
    ],
    ids=[
        "both off is a real working case - plain unencrypted SMTP",
        "tls only",
        "ssl only",
    ],
)
def test_check_email_security_settings_allows_non_conflicting_combinations(use_tls, use_ssl):
    check_email_security_settings(use_tls=use_tls, use_ssl=use_ssl)


def test_check_email_security_settings_rejects_both_true():
    with pytest.raises(ImproperlyConfigured):
        check_email_security_settings(use_tls=True, use_ssl=True)
