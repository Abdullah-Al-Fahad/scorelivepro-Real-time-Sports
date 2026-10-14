import jwt
import requests # <--- REQUIRED FOR DEBUGGING
from rest_framework import generics, permissions, status, views, filters
from django.contrib.auth import get_user_model
from django.contrib.auth.models import update_last_login
from rest_framework.request import Request
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from .permissions import IsAdminGroup

# Social Auth
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from jose import jwt as jose_jwt
import string
import secrets

# Documentation
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiResponse, OpenApiParameter

# Imports for Notifications Sync
from notifications.models import UserDevice
from notifications.services import NotificationService

from .utils import send_otp_via_email, send_verification_email, log_activity
from .serializers import (
    SetNewPasswordSerializer,
    ChangePasswordSerializer,
    UserRegistrationSerializer,
    AdminRegistrationSerializer, 
    CustomTokenObtainPairSerializer, 
    UserProfileSerializer, 
    FanSettingsSerializer,
    AdminSettingsSerializer,
    ManageFavoriteSerializer,
    PasswordResetRequestSerializer,
    VerifyPasswordResetOTPSerializer,
    VerifyEmailSerializer,
    ResendActivationEmailSerializer,
    UserActivitySerializer,
    AdminLoginSerializer,
    AdminVerifySerializer,
    AdminUserManagementSerializer,
    BulkSyncRequestSerializer
)
from .models import OneTimePassword, UserActivity, GuestFavorite

User = get_user_model()

# =========================================================
#                    AUTHENTICATION
# =========================================================

@extend_schema(
    tags=['Authentication'],
    summary="User Login",
    responses={200: CustomTokenObtainPairSerializer}
)
class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@extend_schema(tags=['Authentication'], summary="Refresh Token")
class CustomTokenRefreshView(TokenRefreshView):
    pass

@extend_schema(
    tags=['Authentication'],
    summary="Register Fan User",
    request=UserRegistrationSerializer,
    responses={201: UserRegistrationSerializer}
)
class RegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            send_verification_email(user)
            return Response({'message': "Registered. Check email for OTP.", 'user': serializer.data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@extend_schema(
    tags=['Authentication'],
    summary="Register Admin User",
    responses={201: AdminRegistrationSerializer}
)
class AdminRegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = AdminRegistrationSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'message': 'Admin created.', 'user': serializer.data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# =========================================================
#                    ADMIN 2FA LOGIN
# =========================================================

@extend_schema(tags=['Authentication'], summary="Admin Login (Initiate)")
class AdminLoginView(generics.GenericAPIView):
    serializer_class = AdminLoginSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        send_otp_via_email(user.email, purpose='admin_login')
        return Response({'message': 'OTP sent.', 'email': user.email}, status=status.HTTP_200_OK)


@extend_schema(tags=['Authentication'], summary="Admin Login (Verify)")
class AdminVerifyView(generics.GenericAPIView):
    serializer_class = AdminVerifySerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        serializer.validated_data['otp_obj'].delete()

        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        log_activity(user, "ADMIN_LOGIN", "Admin 2FA Login Successful", request=request)

        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': {'id': user.id, 'email': user.email, 'role': 'Admin'}
        }, status=status.HTTP_200_OK)


# =========================================================
#                    SOCIAL AUTH
# =========================================================

@extend_schema(tags=['Social Auth'], summary="Google Login")
class GoogleLoginView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get('id_token')
        if not token:
            return Response({'error': 'ID token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # We don't specify the audience (client_id) here to allow any valid google token 
            id_info = id_token.verify_oauth2_token(token, google_requests.Request())

            email = id_info.get('email')
            first_name = id_info.get('given_name', '')
            last_name = id_info.get('family_name', '')
            picture_url = id_info.get('picture')
            
            if not email:
                return Response({'error': 'Email not found in token'}, status=status.HTTP_400_BAD_REQUEST)

            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'is_active': True,
                }
            )

            if created:
                alphabet = string.ascii_letters + string.digits
                password = ''.join(secrets.choice(alphabet) for i in range(20))
                user.set_password(password)

            if picture_url and not user.profile_image:
                try:
                    import requests as http_requests
                    from django.core.files.base import ContentFile
                    response = http_requests.get(picture_url)
                    if response.status_code == 200:
                        file_name = f"profile_{user.id}.jpg"
                        user.profile_image.save(file_name, ContentFile(response.content), save=False)
                except Exception as e:
                    print(f"Failed to download google profile image: {e}")

            user.save()

            # Ensure FanProfile is created for them
            from users.models import FanProfile
            FanProfile.objects.get_or_create(user=user)

            update_last_login(None, user)
            log_activity(user, "LOGIN", "Logged in via Google", request=request)

            refresh = RefreshToken.for_user(user)

            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {'id': str(user.id), 'email': user.email, 'role': user.role}
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': f'Invalid token: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            import logging
            logging.error(f"Google Auth Error: {str(e)}")
            return Response({'error': f'Authentication failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(tags=['Social Auth'], summary="Apple Login")
class AppleLoginView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        id_token_jwt = request.data.get('id_token')
        user_data = request.data.get('user')  # Optional dictionary from client on first login
        
        if not id_token_jwt:
            return Response({'error': 'ID token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 1. Fetch Apple's public keys
            apple_keys_url = "https://appleid.apple.com/auth/keys"
            jwks_client = requests.get(apple_keys_url)
            jwks_data = jwks_client.json()
            
            # 2. Decode header to find 'kid'
            header = jose_jwt.get_unverified_header(id_token_jwt)
            kid = header.get('kid')
            if not kid:
                 return Response({'error': 'Invalid token header'}, status=status.HTTP_400_BAD_REQUEST)
            
            # 3. Find the matching key
            key = None
            for k in jwks_data['keys']:
                if k['kid'] == kid:
                    key = k
                    break
            
            if not key:
                return Response({'error': 'Invalid token key'}, status=status.HTTP_400_BAD_REQUEST)
                
            # 4. Verify signature
            decoded = jose_jwt.decode(
                id_token_jwt, 
                key, 
                algorithms=['RS256'],
                options={"verify_aud": False} 
            )
            
            email = decoded.get('email')
            
            if not email and user_data and isinstance(user_data, dict):
                email = user_data.get('email')
            
            if not email:
                 return Response({'error': 'Email not found in token or user data'}, status=status.HTTP_400_BAD_REQUEST)

            first_name = ""
            last_name = ""
            if user_data and isinstance(user_data, dict):
                 name = user_data.get('name', {})
                 if name:
                     first_name = name.get('firstName', '')
                     last_name = name.get('lastName', '')

            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first_name or 'Apple',
                    'last_name': last_name or 'User',
                    'is_active': True,
                }
            )

            if created:
                alphabet = string.ascii_letters + string.digits
                password = ''.join(secrets.choice(alphabet) for i in range(20))
                user.set_password(password)
                user.save()
            else:
                 if (first_name or last_name) and (not user.first_name or user.first_name == 'Apple'):
                     user.first_name = first_name
                     user.last_name = last_name
                     user.save()

            from users.models import FanProfile
            FanProfile.objects.get_or_create(user=user)

            update_last_login(None, user)
            log_activity(user, "LOGIN", "Logged in via Apple", request=request)

            refresh = RefreshToken.for_user(user)

            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {'id': str(user.id), 'email': user.email, 'role': user.role}
            }, status=status.HTTP_200_OK)

        except Exception as e:
            import logging
            logging.error(f"Apple Auth Error: {str(e)}")
            return Response({'error': f'Authentication failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# =========================================================
#                  ACCOUNT VERIFICATION
# =========================================================

@extend_schema(tags=['Account Verification'], summary="Verify Email OTP")
class VerifyEmailView(generics.GenericAPIView):
    serializer_class = VerifyEmailSerializer
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Verified.'}, status=status.HTTP_200_OK)

@extend_schema(tags=['Account Verification'], summary="Resend Verification OTP")
class ResendActivationEmailView(generics.GenericAPIView):
    serializer_class = ResendActivationEmailSerializer
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        send_verification_email(serializer.validated_data['user'])
        return Response({'message': 'OTP resent.'}, status=status.HTTP_200_OK)


# =========================================================
#                  1. BASIC PROFILE (READ ALL / UPDATE USER)
# =========================================================

@extend_schema(
    tags=['Profile'],
    summary="Get/Update Basic Profile",
    description="GET returns full profile (including settings/favorites). PATCH only updates basic info (Name/Image/Email).",
    responses={200: UserProfileSerializer}
)
class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        super().perform_update(serializer)
        log_activity(self.request.user, "PROFILE_UPDATE", "Updated basic profile details", request=self.request)


# Delete user
@extend_schema(
    tags=['Profile'],
    summary="Delete Own Account",
    description="Allows an authenticated user to permanently delete their own account. This action cannot be undone and will cascade delete associated profile data.",
    responses={204: OpenApiResponse(description="Account successfully deleted")}
)
class UserAccountDeleteView(generics.DestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        # Strictly return the authenticated user making the request
        return self.request.user

    def perform_destroy(self, instance):
        # Any pre-deletion cleanup can happen here (e.g., unsubscribing from Firebase topics)
        # Note: UserActivity logs tied to this user will be wiped due to models.CASCADE
        email = instance.email
        super().perform_destroy(instance)
        print(f"User {email} deleted their own account.")

# =========================================================
#                  2. PROFILE SETTINGS (UPDATE ONLY)
# =========================================================

@extend_schema(
    tags=['Profile Settings'],
    summary="Update App Settings",
    description="Update notification preferences (Fan) or permissions (Admin).",
    methods=["PATCH"],
    responses={200: FanSettingsSerializer}
)
class UpdateSettingsView(generics.UpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        user = self.request.user
        if hasattr(user, 'fan_profile'):
            return user.fan_profile
        elif hasattr(user, 'admin_profile'):
            return user.admin_profile
        return None

    def get_serializer_class(self):
        user = self.request.user
        if hasattr(user, 'admin_profile'):
            return AdminSettingsSerializer
        return FanSettingsSerializer
    
    def perform_update(self, serializer):
        profile = self.get_object()
        
        # Capture old state before they are overwritten
        old_live = getattr(profile, 'receive_live_notifications', None)
        old_news = getattr(profile, 'receive_news_updates', None)
        old_lang = getattr(profile, 'language', 'en')
        
        updated_profile = serializer.save()
        
        # If it's a FanProfile, sync Firebase topic subscriptions explicitly
        if hasattr(updated_profile, 'favorite_teams'):
            from notifications.services import NotificationService
            
            # Sync user's devices language and subscriptions if language changed
            if old_lang != updated_profile.language:
                for device in updated_profile.user.devices.filter(active=True):
                    old_device_lang = device.language
                    if old_device_lang != updated_profile.language:
                        device.language = updated_profile.language
                        device.save(update_fields=['language'])
                        import sys
                        if 'test' in sys.argv:
                            NotificationService.update_device_topic_subscriptions(device, old_device_lang, updated_profile.language)
                        else:
                            try:
                                from notifications.tasks import update_device_topic_subscriptions_task
                                update_device_topic_subscriptions_task.delay(device.id, old_device_lang, updated_profile.language)
                            except Exception as e:
                                print(f"Failed to queue topic update on language change: {e}")

            devices = updated_profile.user.devices.filter(active=True)
            if devices.exists():
                # Handle Live Notifications Toggle
                if old_live is not None and old_live != updated_profile.receive_live_notifications:
                    for team in updated_profile.favorite_teams.all():
                        _sync_device_topic(devices, "team", team.id, is_subscribe=updated_profile.receive_live_notifications)
                
                # Handle News Updates Toggle
                if old_news is not None and old_news != updated_profile.receive_news_updates:
                    for league in updated_profile.favorite_leagues.all():
                        _sync_device_topic(devices, "league", league.id, is_subscribe=updated_profile.receive_news_updates)

        log_activity(self.request.user, "SETTINGS_UPDATE", "Updated profile settings", request=self.request)

# Helper function to subscribe/unsubscribe devices to language-suffixed topics asynchronously
def _sync_device_topic(devices, prefix, item_id, is_subscribe=True):
    from notifications.tasks import sync_device_topic_async_task
    import sys
    device_ids = list(devices.values_list('id', flat=True)) if hasattr(devices, 'values_list') else [dev.id for dev in devices]
    if not device_ids:
        return
    if 'test' in sys.argv:
        sync_device_topic_async_task(device_ids, prefix, item_id, is_subscribe=is_subscribe)
    else:
        try:
            sync_device_topic_async_task.delay(device_ids, prefix, item_id, is_subscribe=is_subscribe)
        except Exception:
            sync_device_topic_async_task(device_ids, prefix, item_id, is_subscribe=is_subscribe)

# =========================================================
#                  3. FAVORITES (LIST / ADD / REMOVE)
# =========================================================


def _extract_guest_id(request):
    """
    Extracts guest/device identifier from headers, query params, or body.
    Supports X-Guest-ID, X-Device-ID, X-DeviceId, guest_id, device_id.
    """
    if not request:
        return None
    header_val = (
        request.headers.get('X-Guest-ID')
        or request.headers.get('X-Device-ID')
        or request.headers.get('X-DeviceId')
        or request.headers.get('X-Device-Id')
    )
    if header_val:
        return str(header_val).strip()
    query_val = request.query_params.get('guest_id') or request.query_params.get('device_id')
    if query_val:
        return str(query_val).strip()
    if hasattr(request, 'data') and isinstance(request.data, dict):
        body_val = request.data.get('guest_id') or request.data.get('device_id')
        if body_val:
            return str(body_val).strip()
    return None

class ManageFavoriteTeamsView(views.APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Favorites'], summary="List Favorite Teams", responses={200: OpenApiResponse(description="List of favorite teams")})
    def get(self, request):
        from sports.serializers import TeamSerializer 
        
        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                teams = request.user.fan_profile.favorite_teams.all()
                serializer = TeamSerializer(teams, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response([], status=status.HTTP_200_OK)
        
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
        
        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        teams = guest_fav.favorite_teams.all()
        serializer = TeamSerializer(teams, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(tags=['Favorites'], summary="Add Favorite Team", request=ManageFavoriteSerializer)
    def post(self, request):
        from sports.models import Team 
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        team = get_object_or_404(Team, pk=serializer.validated_data['id'])
        
        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_teams.add(team)
                
                # Auto-Subscribe User Devices to Language-Suffixed Topic
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "team", team.id, is_subscribe=True)

                return Response({"message": f"Added {team.name}."}, status=200)
            return Response({"error": "Not a fan"}, status=400)
        
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
            
        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        guest_fav.favorite_teams.add(team)
        
        # Auto-Subscribe Guest Devices to Language-Suffixed Topic
        devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
        _sync_device_topic(devices, "team", team.id, is_subscribe=True)

        return Response({"message": f"Added {team.name} to guest favorites."}, status=200)

    @extend_schema(tags=['Favorites'], summary="Remove Favorite Team", request=ManageFavoriteSerializer)
    def delete(self, request):
        from sports.models import Team 
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        team = get_object_or_404(Team, pk=serializer.validated_data['id'])
        
        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_teams.remove(team)

                # Auto-Unsubscribe User Devices from Firebase Topic
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "team", team.id, is_subscribe=False)

            return Response({"message": f"Removed {team.name}."}, status=200)
            
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            guest_fav = GuestFavorite.objects.get(device_id=guest_id)
            guest_fav.favorite_teams.remove(team)
            
            # Auto-Unsubscribe Guest Devices from Firebase Topic
            devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
            _sync_device_topic(devices, "team", team.id, is_subscribe=False)
        except GuestFavorite.DoesNotExist:
            pass

        return Response({"message": f"Removed {team.name} from guest favorites."}, status=200)


class ManageFavoriteLeaguesView(views.APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Favorites'], summary="List Favorite Leagues", responses={200: OpenApiResponse(description="List of favorite leagues")})
    def get(self, request):
        from sports.serializers import LeagueSerializer 

        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                leagues = request.user.fan_profile.favorite_leagues.all()
                serializer = LeagueSerializer(leagues, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response([], status=status.HTTP_200_OK)
            
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
            
        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        leagues = guest_fav.favorite_leagues.all()
        serializer = LeagueSerializer(leagues, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(tags=['Favorites'], summary="Add Favorite League", request=ManageFavoriteSerializer)
    def post(self, request):
        from sports.models import League
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        league = get_object_or_404(League, pk=serializer.validated_data['id'])
        
        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_leagues.add(league)

                # Auto-Subscribe User Devices to Language-Suffixed Topic
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "league", league.id, is_subscribe=True)

            return Response({"message": f"Added {league.name}."}, status=200)
            
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
            
        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        guest_fav.favorite_leagues.add(league)
        
        # Auto-Subscribe Guest Devices to Language-Suffixed Topic
        devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
        _sync_device_topic(devices, "league", league.id, is_subscribe=True)

        return Response({"message": f"Added {league.name} to guest favorites."}, status=200)

    @extend_schema(tags=['Favorites'], summary="Remove Favorite League", request=ManageFavoriteSerializer)
    def delete(self, request):
        from sports.models import League
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        league = get_object_or_404(League, pk=serializer.validated_data['id'])
        
        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_leagues.remove(league)

                # Auto-Unsubscribe User Devices from Firebase Topic
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "league", league.id, is_subscribe=False)

            return Response({"message": f"Removed {league.name}."}, status=200)
            
        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            guest_fav = GuestFavorite.objects.get(device_id=guest_id)
            guest_fav.favorite_leagues.remove(league)
            
            # Auto-Unsubscribe Guest Devices from Firebase Topic
            devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
            _sync_device_topic(devices, "league", league.id, is_subscribe=False)
        except GuestFavorite.DoesNotExist:
            pass

        return Response({"message": f"Removed {league.name} from guest favorites."}, status=200)


class ManageFavoriteMatchesView(views.APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Favorites'], summary="List Favorite Matches/Fixtures", responses={200: OpenApiResponse(description="List of favorite matches/fixtures")})
    def get(self, request):
        from sports.serializers import FixtureSerializer

        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                fixtures = request.user.fan_profile.favorite_fixtures.all()
                serializer = FixtureSerializer(fixtures, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response([], status=status.HTTP_200_OK)

        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)

        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        fixtures = guest_fav.favorite_fixtures.all()
        serializer = FixtureSerializer(fixtures, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(tags=['Favorites'], summary="Add Favorite Match/Fixture", request=ManageFavoriteSerializer)
    def post(self, request):
        from sports.models import Fixture
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        fixture = get_object_or_404(Fixture, pk=serializer.validated_data['id'])

        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_fixtures.add(fixture)

                # Auto-Subscribe User Devices to Language-Suffixed Topics
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "match", fixture.id, is_subscribe=True)
                _sync_device_topic(devices, "fixture", fixture.id, is_subscribe=True)

                return Response({"message": f"Added match {fixture.id}."}, status=200)
            return Response({"error": "Not a fan"}, status=400)

        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)

        guest_fav, _ = GuestFavorite.objects.get_or_create(device_id=guest_id)
        guest_fav.favorite_fixtures.add(fixture)

        # Auto-Subscribe Guest Devices to Language-Suffixed Topics
        devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
        _sync_device_topic(devices, "match", fixture.id, is_subscribe=True)
        _sync_device_topic(devices, "fixture", fixture.id, is_subscribe=True)

        return Response({"message": f"Added match {fixture.id} to guest favorites."}, status=200)

    @extend_schema(tags=['Favorites'], summary="Remove Favorite Match/Fixture", request=ManageFavoriteSerializer)
    def delete(self, request):
        from sports.models import Fixture
        serializer = ManageFavoriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        fixture = get_object_or_404(Fixture, pk=serializer.validated_data['id'])

        if request.user and request.user.is_authenticated:
            if hasattr(request.user, 'fan_profile'):
                request.user.fan_profile.favorite_fixtures.remove(fixture)

                # Auto-Unsubscribe User Devices from Firebase Topics
                devices = UserDevice.objects.filter(user=request.user, active=True)
                _sync_device_topic(devices, "match", fixture.id, is_subscribe=False)
                _sync_device_topic(devices, "fixture", fixture.id, is_subscribe=False)

            return Response({"message": f"Removed match {fixture.id}."}, status=200)

        # Guest User
        guest_id = _extract_guest_id(request)
        if not guest_id:
            return Response({"error": "guest_id, X-Guest-ID, or X-Device-ID header is required for anonymous requests"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            guest_fav = GuestFavorite.objects.get(device_id=guest_id)
            guest_fav.favorite_fixtures.remove(fixture)

            # Auto-Unsubscribe Guest Devices from Firebase Topics
            devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
            _sync_device_topic(devices, "match", fixture.id, is_subscribe=False)
            _sync_device_topic(devices, "fixture", fixture.id, is_subscribe=False)
        except GuestFavorite.DoesNotExist:
            pass

        return Response({"message": f"Removed match {fixture.id} from guest favorites."}, status=200)


class BulkSyncFavoritesView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=['Favorites'],
        summary="Bulk Sync Guest Favorites to Logged In User",
        request=BulkSyncRequestSerializer,
        responses={200: OpenApiResponse(description="Successfully synchronized favorites.")}
    )
    def post(self, request):
        serializer = BulkSyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        guest_id = serializer.validated_data['guest_id']
        
        if not hasattr(request.user, 'fan_profile'):
            return Response({"error": "User does not have a fan profile"}, status=status.HTTP_400_BAD_REQUEST)
            
        profile = request.user.fan_profile
        
        # 1. Fetch guest favorites
        teams = []
        leagues = []
        fixtures = []
        try:
            guest_fav = GuestFavorite.objects.get(device_id=guest_id)
            # Add all teams
            teams = list(guest_fav.favorite_teams.all())
            for team in teams:
                profile.favorite_teams.add(team)
            
            # Add all leagues
            leagues = list(guest_fav.favorite_leagues.all())
            for league in leagues:
                profile.favorite_leagues.add(league)

            # Add all fixtures
            fixtures = list(guest_fav.favorite_fixtures.all())
            for fixture in fixtures:
                profile.favorite_fixtures.add(fixture)
                
            # Delete the guest favorite record
            guest_fav.delete()
        except GuestFavorite.DoesNotExist:
            pass
            
        # 2. Transfer device registrations
        # Update devices with guest_id to link to the logged-in user and clear guest_id
        devices = UserDevice.objects.filter(guest_id=guest_id)
        for device in devices:
            device.user = request.user
            device.guest_id = None
            device.save()
            
        # 3. For any team/league/fixture favorites added, dispatch background sync for device topic subscriptions
        team_ids = [t.id for t in teams]
        league_ids = [l.id for l in leagues]
        fixture_ids = [f.id for f in fixtures]
        if team_ids or league_ids or fixture_ids:
            from notifications.tasks import bulk_sync_device_favorites_task
            try:
                bulk_sync_device_favorites_task.delay(request.user.id, team_ids, league_ids, fixture_ids)
            except Exception:
                bulk_sync_device_favorites_task(request.user.id, team_ids, league_ids, fixture_ids)

        return Response({
            "message": "Favorites and devices successfully synchronized.",
            "synced_teams_count": len(teams),
            "synced_leagues_count": len(leagues),
            "synced_fixtures_count": len(fixtures)
        }, status=status.HTTP_200_OK)


# =========================================================
#                  USER ACTIVITY & PASSWORDS
# =========================================================

@extend_schema(tags=['Profile'], summary="User Activity Log")
class UserActivityLogView(generics.ListAPIView):
    serializer_class = UserActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
    def get_queryset(self):
        return UserActivity.objects.filter(user=self.request.user)

@extend_schema(tags=['Password Management'], summary="Request Password Reset")
class PasswordResetRequestView(generics.GenericAPIView):
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': "OTP sent."}, status=status.HTTP_200_OK)

@extend_schema(tags=['Password Management'], summary="Verify OTP for Password Reset")
class VerifyPasswordResetOTPView(generics.GenericAPIView):
    serializer_class = VerifyPasswordResetOTPSerializer
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        # If the serializer is valid, it means the OTP is correct and not expired.
        serializer.is_valid(raise_exception=True)
        return Response({'message': 'OTP is valid.'}, status=status.HTTP_200_OK)

@extend_schema(tags=['Password Management'], summary="Confirm Password Reset")
class PasswordResetConfirmView(generics.GenericAPIView):
    serializer_class = SetNewPasswordSerializer
    permission_classes = [permissions.AllowAny]
    
    # Changed from patch to post since we are sending sensitive data over an unauthenticated endpoint
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Password reset successful.'}, status=status.HTTP_200_OK)
    
@extend_schema(
    tags=['Password Management'], 
    summary="Change Password (Authenticated)",
    description="Allows a logged-in user to change their password by providing their old password.",
    responses={200: OpenApiResponse(description="Password changed successfully.")}
)
class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        # We pass context={'request': request} so the serializer can access request.user
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Password changed successfully.'}, status=status.HTTP_200_OK)


# =========================================================
#                  ADMIN USER MANAGEMENT
# =========================================================

@extend_schema(
    tags=['Admin User Management'],
    summary="List all users",
    description="Allows admins to view all users, their nested profiles, and favorites. Supports filtering by role.",
    parameters=[
        OpenApiParameter(
            name='role', 
            description='Filter users by role. Options: "admin" or "fan"', 
            required=False, 
            type=str
        )
    ],
    responses={200: AdminUserManagementSerializer(many=True)}
)
class AdminUserListView(generics.ListAPIView):
    serializer_class = AdminUserManagementSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminGroup]
    
    filter_backends = [filters.SearchFilter]
    search_fields = ['email', 'first_name', 'last_name']

    def get_queryset(self):
        """
        Optimize the database queries for nested data and apply role filtering.
        """
        # 1. Optimize Queries (Crucial for performance)
        queryset = User.objects.select_related(
            'fan_profile', 
            'admin_profile'
        ).prefetch_related(
            'groups',
            'fan_profile__favorite_teams', 
            'fan_profile__favorite_leagues'
        ).order_by('-date_joined')

        # 2. Handle Custom Role Filtering
        role_param = self.request.query_params.get('role', None)
        
        if role_param:
            role_param = role_param.lower().strip()
            if role_param == 'admin':
                queryset = queryset.filter(groups__name='Admin')
            elif role_param == 'fan':
                queryset = queryset.filter(groups__name='Fan')

        return queryset

@extend_schema(
    tags=['Admin User Management'],
    summary="Retrieve or Update a user",
    description="Allows admins to view or update specific user details (e.g., toggling the 'is_active' status).",
    responses={200: AdminUserManagementSerializer}
)
class AdminUserDetailView(generics.RetrieveUpdateAPIView):
    queryset = User.objects.all()
    serializer_class = AdminUserManagementSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminGroup]
    lookup_field = 'id'

    def perform_update(self, serializer):
        updated_user = serializer.save()
        # Log the admin action
        log_activity(
            self.request.user, 
            "ADMIN_USER_UPDATE", 
            f"Admin updated details for user {updated_user.email}", 
            request=self.request
        )

@extend_schema(
    tags=['Admin User Management'],
    summary="Delete a user",
    description="Separate endpoint for admins to permanently delete a user account.",
    responses={204: OpenApiResponse(description="User successfully deleted")}
)
class AdminUserDeleteView(generics.DestroyAPIView):
    queryset = User.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsAdminGroup]
    lookup_field = 'id'

    def perform_destroy(self, instance):
        email = instance.email
        super().perform_destroy(instance)
        # Log the deletion (useful for audit trails)
        log_activity(
            self.request.user, 
            "ADMIN_USER_DELETE", 
            f"Admin deleted user account: {email}", 
            request=self.request
        )
        
@extend_schema(
    tags=['Admin User Management'],
    summary="Get specific user's activity log",
    description="Allows admins to view a detailed trail of all activities (viewing matches, stats, logins) for a specific user.",
    responses={200: UserActivitySerializer(many=True)}
)
class AdminUserActivityListView(generics.ListAPIView):
    serializer_class = UserActivitySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminGroup]

    def get_queryset(self):
        """
        Retrieves the user ID from the URL and filters their activities.
        Orders by most recent first.
        """
        user_id = self.kwargs.get('id')
        get_object_or_404(User, id=user_id)
        
        return UserActivity.objects.filter(user=user_id).order_by('-timestamp')