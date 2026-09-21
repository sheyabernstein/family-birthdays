from django.urls import path

from family import views

app_name = "family"

urlpatterns = [
    path("help/", views.HelpView.as_view(), name="help"),
    path("ajax/gregorian-to-hebrew/", views.GregorianToHebrewView.as_view(), name="gregorian_to_hebrew"),
    path("ajax/hebrew-to-gregorian/", views.HebrewToGregorianView.as_view(), name="hebrew_to_gregorian"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("people/", views.PersonListView.as_view(), name="person_list"),
    path("people/new/", views.PersonCreateView.as_view(), name="person_create"),
    path("people/<uuid:uuid>/", views.PersonDetailView.as_view(), name="person_detail"),
    path("people/<uuid:uuid>/edit/", views.PersonUpdateView.as_view(), name="person_update"),
    path("people/<uuid:uuid>/delete/", views.PersonDeleteView.as_view(), name="person_delete"),
    path("people/<uuid:uuid>/tree/", views.FamilyTreeView.as_view(), name="family_tree"),
    path("people/<uuid:person_uuid>/spouse/new/", views.UnionCreateView.as_view(), name="union_create"),
    path("unions/<uuid:uuid>/edit/", views.UnionUpdateView.as_view(), name="union_update"),
    path("unions/<uuid:uuid>/delete/", views.UnionDeleteView.as_view(), name="union_delete"),
]
