from celery import shared_task
from .models import UserDevice
from .services import sync_device_subscriptions, update_device_topic_subscriptions

@shared_task
def sync_device_subscriptions_task(device_id):
    try:
        device = UserDevice.objects.get(id=device_id)
        sync_device_subscriptions(device)
    except UserDevice.DoesNotExist:
        pass
    except Exception as e:
        print(f"Error in sync_device_subscriptions_task: {e}")

@shared_task
def update_device_topic_subscriptions_task(device_id, old_lang, new_lang):
    try:
        device = UserDevice.objects.get(id=device_id)
        update_device_topic_subscriptions(device, old_lang, new_lang)
    except UserDevice.DoesNotExist:
        pass
    except Exception as e:
        print(f"Error in update_device_topic_subscriptions_task: {e}")

@shared_task
def sync_device_topic_async_task(device_ids, prefix, item_id, is_subscribe=True):
    from notifications.services import NotificationService
    languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
    devices = UserDevice.objects.filter(id__in=device_ids)
    prefixes = ['match', 'fixture'] if prefix in ['match', 'fixture'] else [prefix]

    for dev in devices:
        if not dev.registration_id:
            continue
        lang = dev.language
        if (not lang or lang == 'en') and dev.user and hasattr(dev.user, 'fan_profile') and dev.user.fan_profile.language:
            lang = dev.user.fan_profile.language
        if not lang:
            lang = 'en'

        for p in prefixes:
            topic_lang = f"{p}_{item_id}_{lang}"
            topic_base = f"{p}_{item_id}"
            try:
                if is_subscribe:
                    NotificationService.subscribe_tokens_to_topic([dev.registration_id], topic_lang)
                    NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], topic_base)
                    for other_lang in languages:
                        if other_lang != lang:
                            NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"{p}_{item_id}_{other_lang}")
                else:
                    # Unsubscribe base topic and all language topics to prevent orphan subscriptions
                    all_topics = [f"{p}_{item_id}_{l}" for l in languages] + [topic_base]
                    for t in all_topics:
                        NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], t)
            except Exception as e:
                print(f"Failed to {'subscribe' if is_subscribe else 'unsubscribe'} device {dev.id} to {topic_lang}: {e}")

@shared_task(name='notifications.tasks.dispatch_service_method_async', ignore_result=True)
def dispatch_service_method_async(method_name, *args, **kwargs):
    """
    Asynchronously executes a NotificationService alert method on a Celery worker,
    keeping real-time match processing loops unblocked.
    """
    from notifications.services import NotificationService
    func = getattr(NotificationService, method_name, None)
    if func and callable(func):
        try:
            func(*args, **kwargs)
        except Exception as e:
            print(f"Error in dispatch_service_method_async for {method_name}: {e}")


@shared_task
def bulk_sync_device_favorites_task(user_id, team_ids, league_ids, fixture_ids):
    from notifications.services import NotificationService
    languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
    devices = UserDevice.objects.filter(user_id=user_id, active=True)
    if not devices.exists():
        return
    for dev in devices:
        if not dev.registration_id:
            continue
        lang = dev.language
        if (not lang or lang == 'en') and dev.user and hasattr(dev.user, 'fan_profile') and dev.user.fan_profile.language:
            lang = dev.user.fan_profile.language
        if not lang:
            lang = 'en'

        for tid in team_ids:
            try:
                NotificationService.subscribe_tokens_to_topic([dev.registration_id], f"team_{tid}_{lang}")
                NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"team_{tid}")
                for other_lang in languages:
                    if other_lang != lang:
                        NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"team_{tid}_{other_lang}")
            except Exception as e:
                print(f"Failed to subscribe user device {dev.id} to team_{tid}_{lang}: {e}")
        for lid in league_ids:
            try:
                NotificationService.subscribe_tokens_to_topic([dev.registration_id], f"league_{lid}_{lang}")
                NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"league_{lid}")
                for other_lang in languages:
                    if other_lang != lang:
                        NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"league_{lid}_{other_lang}")
            except Exception as e:
                print(f"Failed to subscribe user device {dev.id} to league_{lid}_{lang}: {e}")
        for fid in fixture_ids:
            try:
                NotificationService.subscribe_tokens_to_topic([dev.registration_id], f"match_{fid}_{lang}")
                NotificationService.subscribe_tokens_to_topic([dev.registration_id], f"fixture_{fid}_{lang}")
                NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"match_{fid}")
                NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"fixture_{fid}")
                for other_lang in languages:
                    if other_lang != lang:
                        NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"match_{fid}_{other_lang}")
                        NotificationService.unsubscribe_tokens_from_topic([dev.registration_id], f"fixture_{fid}_{other_lang}")
            except Exception as e:
                print(f"Failed to subscribe user device {dev.id} to match_{fid}_{lang}: {e}")


@shared_task
def unsubscribe_deactivated_device_task(device_id):
    try:
        from .models import UserDevice
        from .services import unsubscribe_deactivated_device
        device = UserDevice.objects.get(id=device_id)
        unsubscribe_deactivated_device(device)
    except Exception as e:
        print(f"Error in unsubscribe_deactivated_device_task: {e}")


@shared_task(name='notifications.tasks.prune_old_notification_logs')
def prune_old_notification_logs(days=3):
    """
    Deletes NotificationLog records older than `days`, along with APILog, ServerMetric,
    and resolved ErrorLog entries to maintain database speed and prevent disk bloat.
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import NotificationLog, UserHiddenNotification

    cutoff = timezone.now() - timedelta(days=days)

    # 1. Cascade cleanup on UserHiddenNotification first to satisfy foreign key
    try:
        stale_notif_ids = NotificationLog.objects.filter(created_at__lt=cutoff).values_list('id', flat=True)
        deleted_hidden, _ = UserHiddenNotification.objects.filter(notification_id__in=stale_notif_ids).delete()
    except Exception as e:
        print(f"⚠️ UserHiddenNotification prune warning: {e}")

    deleted_count, _ = NotificationLog.objects.filter(created_at__lt=cutoff).delete()
    print(f"🧹 [PRUNING] Deleted {deleted_count} stale notification logs older than {days} days.")

    # 2. Cleanup APILog and ServerMetric
    try:
        from monitoring.models import APILog, ServerMetric, ErrorLog
        api_cutoff = timezone.now() - timedelta(hours=24)
        deleted_api, _ = APILog.objects.filter(created_at__lt=api_cutoff).delete()
        deleted_metric, _ = ServerMetric.objects.filter(timestamp__lt=api_cutoff).delete()
        deleted_err, _ = ErrorLog.objects.filter(is_resolved=True, updated_at__lt=cutoff).delete()
        print(f"🧹 [PRUNING] Cleaned {deleted_api} APILogs, {deleted_metric} ServerMetrics, {deleted_err} resolved ErrorLogs.")
    except Exception as e:
        print(f"⚠️ Monitoring prune warning: {e}")

    return deleted_count
