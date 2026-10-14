from datetime import timedelta
from rest_framework import generics, permissions, status, filters, views
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema

from .models import UserDevice, ScheduledNotification, NotificationLog, UserHiddenNotification
from .serializers import UserDeviceSerializer, ScheduledNotificationSerializer, NotificationLogSerializer

# =========================================================
#                    PAGINATION CONFIG
# =========================================================

class StandardPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000

# =========================================================
#                  MOBILE USER VIEWS
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

class NotificationInboxView(generics.ListAPIView):
    """
    Returns a personalized feed of notifications based on the user's
    Favorite Teams, Leagues, and Fixtures, EXCLUDING ones they removed.
    Supports both authenticated users and guests.
    """
    serializer_class = NotificationLogSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        guest_id = _extract_guest_id(self.request)

        fav_team_ids = []
        fav_league_ids = []
        fav_fixture_ids = []
        topics = []
        hidden_ids = []
        created_since = None

        if user and user.is_authenticated:
            if not hasattr(user, 'fan_profile'):
                return NotificationLog.objects.none()
            profile = user.fan_profile
            fav_team_ids = list(profile.favorite_teams.values_list('id', flat=True))
            fav_league_ids = list(profile.favorite_leagues.values_list('id', flat=True))
            fav_fixture_ids = list(profile.favorite_fixtures.values_list('id', flat=True))
            topics.append(f"user_{user.id}")
            hidden_ids = list(UserHiddenNotification.objects.filter(user=user).values_list('notification_id', flat=True))
            created_since = user.date_joined
        elif guest_id:
            from users.models import GuestFavorite
            guest_fav = GuestFavorite.objects.filter(device_id=guest_id).first()
            if guest_fav:
                fav_team_ids = list(guest_fav.favorite_teams.values_list('id', flat=True))
                fav_league_ids = list(guest_fav.favorite_leagues.values_list('id', flat=True))
                fav_fixture_ids = list(guest_fav.favorite_fixtures.values_list('id', flat=True))
                created_since = guest_fav.created_at
            else:
                device = UserDevice.objects.filter(guest_id=guest_id).first()
                if device:
                    created_since = device.created_at
                else:
                    created_since = timezone.now()

            topics.append(f"guest_{guest_id}")
            hidden_ids = list(UserHiddenNotification.objects.filter(guest_id=guest_id).values_list('notification_id', flat=True))
        else:
            return NotificationLog.objects.none()

        # Build Topic List
        for tid in fav_team_ids:
            topics.append(f"team_{tid}")
        for lid in fav_league_ids:
            topics.append(f"league_{lid}")
        for fid in fav_fixture_ids:
            topics.append(f"match_{fid}")

        topics.append("global")

        qs = NotificationLog.objects.filter(
            topic__in=topics,
            status='SENT'
        ).exclude(id__in=hidden_ids)

        if created_since:
            qs = qs.filter(created_at__gte=created_since - timedelta(minutes=5))

        return qs.order_by('-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        lang = None

        # 1. Query Params
        if self.request:
            lang = self.request.query_params.get('language') or self.request.query_params.get('lang')

        # 2. Request Headers
        if not lang and self.request:
            accept_lang = self.request.headers.get('Accept-Language') or self.request.headers.get('X-Language')
            if accept_lang:
                clean_lang = accept_lang.split(',')[0].split('-')[0].strip().lower()
                if clean_lang in ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']:
                    lang = clean_lang

        # 3. Authenticated User Profile
        if not lang and self.request and self.request.user and self.request.user.is_authenticated:
            if hasattr(self.request.user, 'fan_profile') and self.request.user.fan_profile.language:
                lang = self.request.user.fan_profile.language

        # 4. Device Language by Guest ID or User Devices
        if not lang and self.request:
            guest_id = _extract_guest_id(self.request)
            if guest_id:
                device = UserDevice.objects.filter(guest_id=guest_id, active=True).first()
                if device and device.language:
                    lang = device.language
            elif self.request.user and self.request.user.is_authenticated:
                device = UserDevice.objects.filter(user=self.request.user, active=True).first()
                if device and device.language:
                    lang = device.language

        supported_languages = [choice[0] for choice in UserDevice.LANGUAGE_CHOICES]
        if not lang or lang not in supported_languages:
            lang = 'en'

        context['language'] = lang
        return context


class UnreadCountView(views.APIView):
    """
    Returns the count of notifications since the last check, excluding hidden ones.
    Supports both authenticated users and guests.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses={200: {"unread_count": 5}})
    def get(self, request):
        user = request.user
        guest_id = _extract_guest_id(request)

        fav_team_ids = []
        fav_league_ids = []
        fav_fixture_ids = []
        topics = []
        hidden_ids = []
        last_check = None
        created_since = None

        if user and user.is_authenticated:
            if not hasattr(user, 'fan_profile'):
                return Response({"unread_count": 0})
            profile = user.fan_profile
            fav_team_ids = list(profile.favorite_teams.values_list('id', flat=True))
            fav_league_ids = list(profile.favorite_leagues.values_list('id', flat=True))
            fav_fixture_ids = list(profile.favorite_fixtures.values_list('id', flat=True))
            topics.append(f"user_{user.id}")
            hidden_ids = list(UserHiddenNotification.objects.filter(user=user).values_list('notification_id', flat=True))
            last_check = profile.last_inbox_check
            created_since = user.date_joined
        elif guest_id:
            from users.models import GuestFavorite
            guest_fav = GuestFavorite.objects.filter(device_id=guest_id).first()
            if guest_fav:
                fav_team_ids = list(guest_fav.favorite_teams.values_list('id', flat=True))
                fav_league_ids = list(guest_fav.favorite_leagues.values_list('id', flat=True))
                fav_fixture_ids = list(guest_fav.favorite_fixtures.values_list('id', flat=True))
                last_check = guest_fav.last_inbox_check
                created_since = guest_fav.created_at
            else:
                device = UserDevice.objects.filter(guest_id=guest_id).first()
                if device:
                    created_since = device.created_at
                else:
                    created_since = timezone.now()

            topics.append(f"guest_{guest_id}")
            hidden_ids = list(UserHiddenNotification.objects.filter(guest_id=guest_id).values_list('notification_id', flat=True))
        else:
            return Response({"unread_count": 0})

        # Re-calculate topics
        for tid in fav_team_ids:
            topics.append(f"team_{tid}")
        for lid in fav_league_ids:
            topics.append(f"league_{lid}")
        for fid in fav_fixture_ids:
            topics.append(f"match_{fid}")
        
        topics.append("global")

        qs = NotificationLog.objects.filter(topic__in=topics, status='SENT').exclude(id__in=hidden_ids)
        
        if last_check:
            qs = qs.filter(created_at__gt=last_check)
        elif created_since:
            qs = qs.filter(created_at__gte=created_since - timedelta(minutes=5))
        
        count = qs.count()
        return Response({"unread_count": count})


class MarkAllReadView(views.APIView):
    """
    Updates the 'last_inbox_check' timestamp to NOW.
    Supports both authenticated users and guests.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        user = request.user
        guest_id = _extract_guest_id(request)

        if user and user.is_authenticated:
            if hasattr(user, 'fan_profile'):
                user.fan_profile.last_inbox_check = timezone.now()
                user.fan_profile.save()
                return Response({"message": "Marked as read"}, status=status.HTTP_200_OK)
            return Response({"error": "No profile"}, status=status.HTTP_400_BAD_REQUEST)
        elif guest_id:
            from users.models import GuestFavorite
            guest_fav, created = GuestFavorite.objects.get_or_create(device_id=guest_id)
            guest_fav.last_inbox_check = timezone.now()
            guest_fav.save()
            return Response({"message": "Marked as read"}, status=status.HTTP_200_OK)
        
        return Response({"error": "Authentication or guest_id required"}, status=status.HTTP_400_BAD_REQUEST)


class HideNotificationView(views.APIView):
    """
    'Removes' a single notification from the user/guest inbox by marking it as hidden.
    Supports both authenticated users and guests.
    """
    permission_classes = [permissions.AllowAny]

    def delete(self, request, pk):
        notification = get_object_or_404(NotificationLog, pk=pk)
        user = request.user
        guest_id = _extract_guest_id(request)

        if user and user.is_authenticated:
            UserHiddenNotification.objects.get_or_create(user=user, notification=notification)
            return Response({"message": "Notification removed"}, status=status.HTTP_204_NO_CONTENT)
        elif guest_id:
            UserHiddenNotification.objects.get_or_create(guest_id=guest_id, notification=notification)
            return Response({"message": "Notification removed"}, status=status.HTTP_204_NO_CONTENT)
            
        return Response({"error": "Authentication or guest_id required"}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Notifications'])
class FCMDeviceView(views.APIView):
    """
    Register or update a device FCM token.
    """
    serializer_class = UserDeviceSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        registration_id = request.data.get('registration_id')
        device_type = request.data.get('type', 'android')
        
        if not registration_id:
            return Response({"error": "registration_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        if request.user and request.user.is_authenticated:
            user = request.user
            guest_id = None
        else:
            user = None
            guest_id = _extract_guest_id(request)
            if not guest_id:
                import uuid
                guest_id = str(uuid.uuid5(uuid.NAMESPACE_OID, registration_id))

        # Get language and validate
        language = request.data.get('language') or request.query_params.get('language')
        if not language and user and hasattr(user, 'fan_profile') and user.fan_profile.language:
            language = user.fan_profile.language
        if not language:
            language = 'en'

        supported_languages = [choice[0] for choice in UserDevice.LANGUAGE_CHOICES]
        if language not in supported_languages:
            language = 'en'

        # Find existing device to check for language changes
        existing_device = UserDevice.objects.filter(registration_id=registration_id).first()
        old_lang = existing_device.language if existing_device else None

        active_val = request.data.get('active', True)
        if isinstance(active_val, str):
            active_val = active_val.lower() not in ('false', '0')
        elif not isinstance(active_val, bool):
            active_val = bool(active_val)

        device, created = UserDevice.objects.update_or_create(
            registration_id=registration_id,
            defaults={
                'user': user,
                'guest_id': guest_id,
                'type': device_type,
                'language': language,
                'active': active_val
            }
        )
        
        if not active_val:
            try:
                from .tasks import unsubscribe_deactivated_device_task
                unsubscribe_deactivated_device_task.delay(device.id)
            except Exception as e:
                print(f"Failed to queue topic unsubscribe: {e}")
            return Response({'status': 'Device deactivated and unsubscribed', 'device_id': device.id}, status=status.HTTP_200_OK)
        
        # Sync user profile language if logged in and profile language was empty
        if user and hasattr(user, 'fan_profile'):
            profile = user.fan_profile
            if not profile.language or (request.data.get('language') and profile.language != language):
                profile.language = language
                profile.save()

        # If language changed, migrate subscriptions; otherwise, perform a full sync to catch any new/existing favorites.
        if old_lang and old_lang != language:
            try:
                from .tasks import update_device_topic_subscriptions_task
                update_device_topic_subscriptions_task.delay(device.id, old_lang, language)
            except Exception as e:
                print(f"Failed to queue topic subscriptions migration: {e}")
        else:
            try:
                from .tasks import sync_device_subscriptions_task
                sync_device_subscriptions_task.delay(device.id)
            except Exception as e:
                print(f"Failed to queue topic subscriptions sync: {e}")
        
        return Response({'status': 'Device registered', 'device_id': device.id}, status=status.HTTP_201_CREATED)

    def delete(self, request, *args, **kwargs):
        registration_id = request.data.get('registration_id') or request.query_params.get('registration_id')
        if not registration_id:
            return Response({"error": "registration_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        device = UserDevice.objects.filter(registration_id=registration_id).first()
        if device:
            device.active = False
            device.save(update_fields=['active'])
            try:
                from .tasks import unsubscribe_deactivated_device_task
                unsubscribe_deactivated_device_task.delay(device.id)
            except Exception as e:
                print(f"Failed to queue topic unsubscribe: {e}")
        return Response({"status": "Device deactivated and unsubscribed"}, status=status.HTTP_200_OK)

class TestPushNotificationView(views.APIView):
    """
    TEMPORARY TESTING ENDPOINT: Sends a test push notification to devices.
    Allows targeting all devices, a specific token, a user, or a guest.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get('token')
        user_id = request.data.get('user_id')
        email = request.data.get('email')
        guest_id = request.data.get('guest_id')

        if not any([token, user_id, email, guest_id]):
            return Response(
                {"error": "At least one of 'token', 'user_id', 'email', or 'guest_id' is required. Set token='all' to target everyone."},
                status=status.HTTP_400_BAD_REQUEST
            )

        title = request.data.get('title', '⚽ Goal by Real Madrid!')
        body = request.data.get('body', 'Current Score: Real Madrid 1 - 0 Barcelona')
        event_type = request.data.get('event_type', 'GOAL')
        
        from .services import NotificationService, translate_notification

        # Determine target devices
        if token == 'all':
            devices = UserDevice.objects.filter(active=True)
        elif user_id:
            devices = UserDevice.objects.filter(user_id=user_id, active=True)
        elif email:
            devices = UserDevice.objects.filter(user__email=email, active=True)
        elif guest_id:
            devices = UserDevice.objects.filter(guest_id=guest_id, active=True)
        else:
            # Build custom payload according to routing guidelines
            data_payload = request.data.get('data') or {}
            if not isinstance(data_payload, dict):
                data_payload = {}
            for key in ['type', 'event_type', 'click_action', 'match_id', 'league_id', 'team_id', 'player_id']:
                if key in request.data:
                    data_payload[key] = request.data[key]
            if 'event_type' not in data_payload:
                data_payload['event_type'] = event_type

            device = UserDevice.objects.filter(registration_id=token, active=True).first()
            if device:
                devices = UserDevice.objects.filter(id=device.id)
            else:
                # Fallback to direct token send if device record is not in database
                t_title, t_body = translate_notification(title, body, event_type, 'en')
                result = NotificationService.send_push_to_token(token, t_title, t_body, data=data_payload)
                if result.get("success"):
                    return Response(result, status=status.HTTP_200_OK)
                return Response(result, status=status.HTTP_400_BAD_REQUEST)

        # Build custom payload according to routing guidelines
        data_payload = request.data.get('data') or {}
        if not isinstance(data_payload, dict):
            data_payload = {}
        for key in ['type', 'event_type', 'click_action', 'match_id', 'league_id', 'team_id', 'player_id']:
            if key in request.data:
                data_payload[key] = request.data[key]
        if 'event_type' not in data_payload:
            data_payload['event_type'] = event_type

        results = []
        for device in devices:
            t_title, t_body = translate_notification(title, body, event_type, device.language)
            res = NotificationService.send_push_to_token(
                device.registration_id,
                t_title,
                t_body,
                data=data_payload
            )
            results.append({
                "device_id": device.id,
                "guest_id": device.guest_id,
                "user": device.user.email if device.user else None,
                "language": device.language,
                "success": res.get("success"),
                "message_id": res.get("message_id"),
                "error": res.get("error")
            })
        return Response({
            "message": f"Sent test push to {len(results)} matching active devices.",
            "results": results
        }, status=status.HTTP_200_OK)


# =========================================================
#                  ADMIN DASHBOARD VIEWS
# =========================================================

@extend_schema(tags=['Notifications - Admin'], summary="List or Schedule Notifications")
class ScheduledNotificationListView(generics.ListCreateAPIView):
    queryset = ScheduledNotification.objects.all().order_by('-scheduled_time')
    serializer_class = ScheduledNotificationSerializer
    permission_classes = [permissions.IsAdminUser] 
    pagination_class = StandardPagination
    
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['is_sent']
    ordering_fields = ['scheduled_time', 'created_at']

@extend_schema(tags=['Notifications - Admin'], summary="Edit or Delete Scheduled Notification")
class ScheduledNotificationDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ScheduledNotification.objects.all()
    serializer_class = ScheduledNotificationSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_update(self, serializer):
        # Prevent editing if the notification has already been sent
        if self.get_object().is_sent:
            raise ValidationError("You cannot edit a notification that has already been sent.")
        serializer.save()

    def perform_destroy(self, instance):
        # Prevent deleting if it's already sent (preserves historical record)
        if instance.is_sent:
            raise ValidationError("You cannot delete a notification that has already been sent.")
        instance.delete()

@extend_schema(tags=['Notifications - Admin'], summary="View Notification Logs")
class NotificationLogListView(generics.ListAPIView):
    queryset = NotificationLog.objects.all().order_by('-created_at')
    serializer_class = NotificationLogSerializer
    permission_classes = [permissions.IsAdminUser] 
    pagination_class = StandardPagination
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'topic']
    search_fields = ['title', 'body', 'error_message']