import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from notifications.sms import (
    ConsoleSmsBackend,
    SmsRateLimitedError,
    SmsUnrecoverableError,
    SnsSmsBackend,
    _check_sns_publish_rate_limit,
)


def test_console_backend_logs_and_returns_status():
    result = ConsoleSmsBackend().send(to="+15551234567", body="Hi", sender_id="RokachFam")

    assert result == {"status": "logged", "to": "+15551234567", "sender_id": "RokachFam"}


def test_console_backend_validate_settings_is_a_no_op():
    ConsoleSmsBackend.validate_settings()


@override_settings(AWS_ACCESS_KEY_ID="key", AWS_SECRET_ACCESS_KEY="secret", AWS_SNS_REGION="us-east-1")
def test_sns_backend_validate_settings_allows_everything_set():
    SnsSmsBackend.validate_settings()


@pytest.mark.parametrize(
    ["aws_access_key_id", "aws_secret_access_key", "aws_region"],
    [
        ["", "secret", "us-east-1"],
        ["key", "", "us-east-1"],
        ["key", "secret", ""],
        ["", "", ""],
    ],
)
def test_sns_backend_validate_settings_rejects_any_missing_aws_setting(
    aws_access_key_id, aws_secret_access_key, aws_region
):
    with override_settings(
        AWS_ACCESS_KEY_ID=aws_access_key_id,
        AWS_SECRET_ACCESS_KEY=aws_secret_access_key,
        AWS_SNS_REGION=aws_region,
    ):
        with pytest.raises(ImproperlyConfigured):
            SnsSmsBackend.validate_settings()


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "Publish")


@patch("notifications.sms.boto3.client")
def test_sns_backend_sends_and_returns_message_id(mock_boto_client):
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "abc123"}
    mock_boto_client.return_value = mock_client

    result = SnsSmsBackend().send(to="+15551234567", body="Hi", sender_id="RokachFam")

    assert result == {"message_id": "abc123"}
    mock_client.publish.assert_called_once()
    kwargs = mock_client.publish.call_args.kwargs
    assert kwargs["PhoneNumber"] == "+15551234567"
    assert kwargs["Message"] == "Hi"
    assert kwargs["MessageAttributes"]["AWS.SNS.SMS.SenderID"]["StringValue"] == "RokachFam"
    assert kwargs["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"


@patch("notifications.sms.boto3.client")
def test_sns_backend_reuses_one_client_across_sends(mock_boto_client):
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "abc123"}
    mock_boto_client.return_value = mock_client

    backend = SnsSmsBackend()
    backend.send(to="+15551234567", body="Hi", sender_id="RokachFam")
    backend.send(to="+15559876543", body="Bye", sender_id="RokachFam")

    mock_boto_client.assert_called_once()
    assert mock_client.publish.call_count == 2


@pytest.mark.parametrize(
    "code",
    [
        "InvalidParameterException",
        "ParameterValueInvalidException",
        "AuthorizationErrorException",
        "OptInRequiredException",
    ],
)
@patch("notifications.sms.boto3.client")
def test_sns_backend_raises_unrecoverable_for_permanent_error_codes(mock_boto_client, code):
    mock_client = MagicMock()
    mock_client.publish.side_effect = _client_error(code)
    mock_boto_client.return_value = mock_client

    with pytest.raises(SmsUnrecoverableError):
        SnsSmsBackend().send(to="+15551234567", body="Hi", sender_id="RokachFam")


@patch("notifications.sms.boto3.client")
def test_sns_backend_reraises_client_error_for_unrecognized_codes(mock_boto_client):
    mock_client = MagicMock()
    mock_client.publish.side_effect = _client_error("ThrottledException")
    mock_boto_client.return_value = mock_client

    with pytest.raises(ClientError):
        SnsSmsBackend().send(to="+15551234567", body="Hi", sender_id="RokachFam")


@override_settings(SNS_PUBLISH_RATE_LIMIT_PER_SECOND=2)
def test_rate_limit_allows_up_to_the_configured_limit_in_one_window():
    _check_sns_publish_rate_limit()
    _check_sns_publish_rate_limit()


@override_settings(SNS_PUBLISH_RATE_LIMIT_PER_SECOND=2)
def test_rate_limit_raises_once_the_configured_limit_is_exceeded():
    _check_sns_publish_rate_limit()
    _check_sns_publish_rate_limit()

    with pytest.raises(SmsRateLimitedError):
        _check_sns_publish_rate_limit()


@override_settings(SNS_PUBLISH_RATE_LIMIT_PER_SECOND=1)
def test_rate_limit_resets_in_the_next_second_window():
    _check_sns_publish_rate_limit()
    with pytest.raises(SmsRateLimitedError):
        _check_sns_publish_rate_limit()

    time.sleep(1.1)
    _check_sns_publish_rate_limit()


@override_settings(AWS_ACCESS_KEY_ID="key", AWS_SECRET_ACCESS_KEY="secret", AWS_SNS_REGION="us-east-1")
@override_settings(SNS_PUBLISH_RATE_LIMIT_PER_SECOND=0)
@patch("notifications.sms.boto3.client")
def test_sns_backend_checks_the_rate_limit_before_publishing(mock_boto_client):
    mock_client = MagicMock()
    mock_boto_client.return_value = mock_client

    with pytest.raises(SmsRateLimitedError):
        SnsSmsBackend().send(to="+15551234567", body="Hi", sender_id="RokachFam")

    mock_client.publish.assert_not_called()
