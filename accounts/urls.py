from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.RequestMagicLinkView.as_view(), name="request_link"),
    # Ahead of login/<str:token>/ below - otherwise "code" itself would
    # match that catch-all pattern as a (bogus) token value.
    path("login/code/", views.VerifyCodeView.as_view(), name="verify_code"),
    path("login/<str:token>/", views.VerifyMagicLinkView.as_view(), name="verify"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("validate-email/", views.ValidateEmailView.as_view(), name="validate_email"),
]
