from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.RequestMagicLinkView.as_view(), name="request_link"),
    path("login/<str:token>/", views.VerifyMagicLinkView.as_view(), name="verify"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("validate-email/", views.ValidateEmailView.as_view(), name="validate_email"),
]
