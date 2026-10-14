from django.urls import path
from .views import (
    FCMDeviceView, ScheduledNotificationListView, ScheduledNotificationDetailView,
    NotificationLogListView, NotificationInboxView, UnreadCountView, 
    MarkAllReadView, HideNotificationView, TestPushNotificationView
)

urlpatterns = [
    # Mobile App User Endpoints
    path('devices/', FCMDeviceView.as_view(), name='register-device-alias'),
    path('inbox/unread-count/', UnreadCountView.as_view(), name='notification-unread-count-alias'),
    path('inbox/read-all/', MarkAllReadView.as_view(), name='notification-mark-read-alias'),
    path('inbox/', NotificationInboxView.as_view(), name='notification-inbox'),
    path('unread-count/', UnreadCountView.as_view(), name='notification-unread-count'),
    path('mark-read/', MarkAllReadView.as_view(), name='notification-mark-read'),
    path('remove/<int:pk>/', HideNotificationView.as_view(), name='notification-remove'),
    path('devices/register/', FCMDeviceView.as_view(), name='register-device'),

    # Admin Dashboard Endpoints
    path('scheduled/', ScheduledNotificationListView.as_view(), name='scheduled-notifications'),
    path('scheduled/<int:pk>/', ScheduledNotificationDetailView.as_view(), name='scheduled-notification-detail'),
    path('logs/', NotificationLogListView.as_view(), name='notification-logs'),
    
    # Temporary Testing Endpoint
    path('test-push/', TestPushNotificationView.as_view(), name='test-push'),
]