import types

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.views.generic import ListView

from accounts.models import Account
from family.models import Person, Union
from tenants.mixins import FamilyScopedMixin
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ["family_names", "expected_redirect"],
    [
        [[], "/family/no-family/"],
        [["Family A", "Family B"], "/family/switch/"],
    ],
    ids=[
        "no membership and no add_family permission is sent to the no-access page",
        "two memberships is sent to switch family",
    ],
)
def test_redirect_based_on_membership_count(client, family_names, expected_redirect):
    account = Account.objects.create_user(email="test@example.com")
    for name in family_names:
        FamilyMembership.objects.create(account=account, family=Family.objects.create(name=name))
    client.force_login(account)

    resp = client.get("/", follow=True)

    assert resp.redirect_chain[-1][0] == expected_redirect


def test_no_membership_with_add_family_permission_is_sent_to_create_a_family(client):
    # There's no self-service tenant creation (see AGENTS.md) - only an
    # account a site admin has explicitly granted tenants.add_family to
    # (e.g. Django staff) gets sent to create one instead of the plain
    # "you don't have access yet" page.
    account = Account.objects.create_user(email="staff@example.com")
    account.user_permissions.add(
        Permission.objects.get(codename="add_family", content_type__app_label="tenants")
    )
    client.force_login(account)

    resp = client.get("/", follow=True)

    assert resp.redirect_chain[-1][0] == "/family/create/"


def test_family_scoped_mixin_requires_family_lookup_to_be_set():
    # Fails at class-definition time (import time, in practice) - not on
    # a request - so a future view that forgets this can't even boot the
    # app, let alone silently serve another family's record.
    with pytest.raises(ImproperlyConfigured):

        class MissingLookup(FamilyScopedMixin, ListView):
            model = Person


def test_family_scoped_mixin_filters_by_a_single_lookup():
    family_a = Family.objects.create(name="A")
    family_b = Family.objects.create(name="B")
    person_a = Person.objects.create(family=family_a, first_name_en="A", last_name_en="Person")
    Person.objects.create(family=family_b, first_name_en="B", last_name_en="Person")

    class PersonView(FamilyScopedMixin, ListView):
        model = Person
        family_lookup = "family"

    view = PersonView()
    view.request = types.SimpleNamespace(family=family_a)

    assert list(view.get_queryset()) == [person_a]


def test_family_scoped_mixin_ors_multiple_lookups():
    family_a = Family.objects.create(name="A")
    family_b = Family.objects.create(name="B")
    family_c = Family.objects.create(name="C")
    person_a = Person.objects.create(family=family_a, first_name_en="A", last_name_en="Person")
    person_b = Person.objects.create(family=family_b, first_name_en="B", last_name_en="Person")
    shared_union = Union.objects.create(person_a=person_a, person_b=person_b)
    unrelated = Person.objects.create(family=family_c, first_name_en="C", last_name_en="Person")
    other_unrelated = Person.objects.create(family=family_c, first_name_en="D", last_name_en="Person")
    Union.objects.create(person_a=unrelated, person_b=other_unrelated)

    class UnionView(FamilyScopedMixin, ListView):
        model = Union
        family_lookup = ["person_a__family", "person_b__family"]

    view = UnionView()
    view.request = types.SimpleNamespace(family=family_a)

    assert list(view.get_queryset()) == [shared_union]
