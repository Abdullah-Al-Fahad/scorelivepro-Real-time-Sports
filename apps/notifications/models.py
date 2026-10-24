from django.db import models
from django.conf import settings

class NotificationLog(models.Model):
    # e.g., "team_33", "league_39", "user_101", "global"
    topic = models.CharField(max_length=255, db_index=True)
    
    title = models.CharField(max_length=255)
    body = models.TextField()
    
    # JSON payload (match_id, type="GOAL", reason="Favorites", etc.)
    data = models.JSONField(default=dict, blank=True)
    
    status = models.CharField(max_length=50, default='PENDING') # SENT, FAILED
    event_type = models.CharField(max_length=50, default='CUSTOM') # GOAL, FT, LINEUPS
    error_message = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['topic', 'created_at']),
            models.Index(fields=['status', 'topic', 'created_at'], name='notif_stat_topic_created_idx'),
            models.Index(fields=['event_type', 'created_at'], name='notif_event_created_idx'),
        ]

    def __str__(self):
        return f"[{self.event_type}] {self.topic}: {self.title}"


class UserDevice(models.Model):
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('es', 'Spanish'),
        ('fr', 'French'),
        ('de', 'German'),
        ('it', 'Italian'),
        ('pt', 'Portuguese'),
        ('tr', 'Turkish'),
    ]                                 
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='devices', null=True, blank=True)
    guest_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    registration_id = models.CharField(max_length=512, unique=True)
    type = models.CharField(max_length=10, choices=[('ios', 'iOS'), ('android', 'Android'), ('web', 'Web')], default='android')
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default='en')
    active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        owner = self.user.email if self.user else f"Guest ({self.guest_id})"
        return f"{owner} - {self.type}"

    def save(self, *args, **kwargs):
        # Sync the owner user's FanProfile language if it differs
        if self.user and self.active:
            try:
                from users.models import FanProfile
                profile, created = FanProfile.objects.get_or_create(user=self.user)
                if profile.language != self.language:
                    profile.language = self.language
                    profile.save()
            except Exception as e:
                print(f"Failed to sync user FanProfile language: {e}")
        super().save(*args, **kwargs)


class UserHiddenNotification(models.Model):
    """
    Tracks notifications a specific user or guest has chosen to 'remove' from their feed.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hidden_notifications', null=True, blank=True)
    guest_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    notification = models.ForeignKey(NotificationLog, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'notification'],
                name='unique_user_notification_hide',
                condition=models.Q(user__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['guest_id', 'notification'],
                name='unique_guest_notification_hide',
                condition=models.Q(guest_id__isnull=False)
            ),
        ]


class ScheduledNotification(models.Model):
    title = models.CharField(max_length=255)
    body = models.TextField()
    
    # Changed to a free-text string field without choices
    event_type = models.CharField(max_length=50, default='CUSTOM', help_text="A string identifier for the app to parse (e.g. NEWS, UPDATE, CUSTOM)")
    
    scheduled_time = models.DateTimeField()
    is_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Scheduled: {self.title} (Global)"