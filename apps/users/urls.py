from django.urls import path
from .views import (
    RegistrationView,
    AdminRegistrationView,
    LoginView,
    GoogleLoginView,
    AppleLoginView,
    CustomTokenRefreshView,
    UserProfileView,
    VerifyEmailView,
    ResendActivationEmailView,
    PasswordResetRequestView,
    VerifyPasswordResetOTPView,
    PasswordResetConfirmView,
    UserActivityLogView,
    AdminLoginView,
    AdminVerifyView,
    UpdateSettingsView,
    ManageFavoriteTeamsView,
    ManageFavoriteLeaguesView,
    ManageFavoriteMatchesView,
    BulkSyncFavoritesView,
    AdminUserListView,
    AdminUserDetailView,
    AdminUserDeleteView,
    AdminUserActivityListView,
    ChangePasswordView,
    UserAccountDeleteView
)

urlpatterns = [
    # Auth & Registration
    path('register/', RegistrationView.as_view(), name='register'),
    path('register/admin/', AdminRegistrationView.as_view(), name='register-admin'),
    path('login/', LoginView.as_view(), name='login'),
    path('google/', GoogleLoginView.as_view(), name='google-login'),
    path('apple/', AppleLoginView.as_view(), name='apple-login'),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    
    # Admin 2FA Login
    path('login/admin/', AdminLoginView.as_view(), name='admin-login-initiate'),
    path('login/admin/verify/', AdminVerifyView.as_view(), name='admin-login-verify'),
    
    # 1. Basic Profile (GET Full Profile / PATCH Basic Info)
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('profile/delete/', UserAccountDeleteView.as_view(), name='profile-delete'),
    
    # 2. Settings (PATCH Notification booleans)
    path('profile/settings/', UpdateSettingsView.as_view(), name='profile-settings'),
    
    # 3. Favorites (POST / DELETE)
    path('favorites/teams/', ManageFavoriteTeamsView.as_view(), name='fav-teams-alias'),
    path('favorites/leagues/', ManageFavoriteLeaguesView.as_view(), name='fav-leagues-alias'),
    path('favorites/matches/', ManageFavoriteMatchesView.as_view(), name='fav-matches-alias'),
    path('favorites/sync/', BulkSyncFavoritesView.as_view(), name='fav-sync-alias'),
    path('guest/favorites/teams/', ManageFavoriteTeamsView.as_view(), name='guest-fav-teams-alias'),
    path('guest/favorites/leagues/', ManageFavoriteLeaguesView.as_view(), name='guest-fav-leagues-alias'),
    path('guest/favorites/matches/', ManageFavoriteMatchesView.as_view(), name='guest-fav-matches-alias'),
    path('profile/favorites/teams/', ManageFavoriteTeamsView.as_view(), name='manage-fav-teams'),
    path('profile/favorites/leagues/', ManageFavoriteLeaguesView.as_view(), name='manage-fav-leagues'),
    path('profile/favorites/matches/', ManageFavoriteMatchesView.as_view(), name='manage-fav-matches'),
    path('profile/favorites/sync/', BulkSyncFavoritesView.as_view(), name='favorites-bulk-sync'),

    # Email Verification
    path('verify-email/', VerifyEmailView.as_view(), name='verify-email'),
    path('resend-activation-code/', ResendActivationEmailView.as_view(), name='resend-activation-code'),
    
    # Password Reset
    path('password-reset-request/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password-reset-verify-otp/', VerifyPasswordResetOTPView.as_view(), name='password-reset-verify-otp'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),

    # Change Password (Authenticated)
    path('profile/change-password/', ChangePasswordView.as_view(), name='change-password'),

    # Activity Log
    path('activity/', UserActivityLogView.as_view(), name='user-activity'),

    # Admin User Management
    path('admin/users/', AdminUserListView.as_view(), name='admin-user-list'),
    path('admin/users/<uuid:id>/', AdminUserDetailView.as_view(), name='admin-user-detail'),
    path('admin/users/<uuid:id>/delete/', AdminUserDeleteView.as_view(), name='admin-user-delete'),
    
    # Admin User Activity Log
    path('admin/users/<uuid:id>/activities/', AdminUserActivityListView.as_view(), name='admin-user-activities'),
]