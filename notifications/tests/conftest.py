import pytest

from accounts.models import Account
from notifications.models import EventType
from tenants.models import FamilyMembership


@pytest.fixture
def birthday_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)


@pytest.fixture
def yahrzeit_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.YAHRZEIT)


def member(family, email="member@example.com"):
    account = Account.objects.create_user(email=email)
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.MEMBER)
    return account
