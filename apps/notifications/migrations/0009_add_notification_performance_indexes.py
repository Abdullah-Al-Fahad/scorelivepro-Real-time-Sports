# Generated for performance indexes on NotificationLog

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0008_alter_userhiddennotification_unique_together_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(fields=['status', 'topic', 'created_at'], name='notif_stat_topic_created_idx'),
        ),
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(fields=['event_type', 'created_at'], name='notif_event_created_idx'),
        ),
    ]
