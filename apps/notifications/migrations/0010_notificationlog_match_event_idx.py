from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import F
from django.db.models.fields.json import KeyTransform


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction; this keeps the live table writable during index build.
    atomic = False

    dependencies = [
        ('notifications', '0009_add_notification_performance_indexes'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='notificationlog',
            index=models.Index(
                KeyTransform('match_id', 'data'), F('event_type'), F('created_at'),
                name='notif_match_event_idx',
            ),
        ),
    ]
