# 🚨 SCORE_LIVE_PRO: BACKEND PUSH NOTIFICATION FORENSIC REPORT & FIX INSTRUCTIONS

**Target Audience:** Backend Developer / DevOps Engineer  
**Date:** October 20, 2026  
**Context:** The Firebase Cloud Messaging (FCM) pipeline is verified and operational, and notifications were successfully delivered before. However, after running for several days in production, push notifications silently ceased arriving.

---

## 📌 1. EXECUTIVE ROOT CAUSE SUMMARY

When a push notification engine works immediately after deployment but ceases after running uninterrupted for several days **with zero code changes**, the failure is almost always due to **state accumulation, resource starvation, or process degradation**.

Our forensic inspection of the backend repository (`scorelivepro-Real-time-Sports`) identified **6 critical failure mechanisms**:

| # | Vulnerability / Failure Mode | Likelihood | Symptom |
|---|-----------------------------|------------|---------|
| **1** | **Docker Log Files Exhausting VPS Disk** | 🔴 **CRITICAL** | Containers log every 15s to stdout. Uncapped JSON logs consume 10–50GB until disk is 100% full, causing Redis/PostgreSQL write freezes. |
| **2** | **Celery Worker Memory Leak / OOM Kill** | 🔴 **CRITICAL** | Celery worker runs `c 4` with **no** `--max-tasks-per-child`. After tens of thousands of tasks (5,760+ runs/day for `update_live_fixtures`), worker memory bloats until Linux OOM Killer terminates workers. |
| **3** | **API-Football Daily Quota Depleted** | 🟡 **HIGH** | `update_live_fixtures` (15s) + `fetch_live_events` (45s) + `fetch_live_statistics` (60s) = **8,000–12,000+ API calls/day**. If the plan limit is 7,500/day, all live tasks abort with `Skipped: API Error` every afternoon. |
| **4** | **Celery Beat Schedule Lock / Stale PID Crashloop** | 🟡 **HIGH** | `/tmp/celerybeat-schedule` or `/tmp/celerybeat.pid` in `celery-beat` container remains locked after container restart, causing Celery Beat to fail on boot and stop scheduling tasks. |
| **5** | **PostgreSQL Query Timeout on `NotificationLog`** | 🟡 **HIGH** | `lock_timeout=10000` & `statement_timeout=30000` in `prod.py` combined with unindexed JSON queries on `NotificationLog` (`data__match_id`) inside atomic transactions cause DB rollbacks as the table grows. |
| **6** | **Follower Gating Condition (`has_followers = False`)** | ⚪ **OPERATIONAL** | Every match event strictly gates on `has_followers`. If testing devices don't actively favorite the match/team or if `guest_id` rotated, backend skips alerts entirely. |

---

## 🛠️ 2. STEP-BY-STEP DIAGNOSTIC CHECKLIST ON THE VPS

Run the following commands directly on the VPS via SSH to pinpoint the exact failure point in under 5 minutes:

### Step 1: Check VPS Storage & Docker Disk Usage
```bash
# Check overall disk usage (look for /dev/root at 100%)
df -h

# Check size of Docker container logs
du -sh /var/lib/docker/containers/*/*.log 2>/dev/null | sort -rh | head -n 10
```
> **If disk is 100% full:** Docker container logs or PostgreSQL WAL files filled the disk. Redis and Postgres immediately reject all write operations.

---

### Step 2: Check API-Football Daily Request Quota
Run the built-in diagnostic script inside the `web` container:
```bash
docker compose -f docker-compose.prod.yml exec web python test_api_fetch.py
```
> **What to check:**  
> Look at `Calls today: X / Y`.  
> If `current >= limit_day`, your API quota is exhausted. The engine returns `Skipped: API Error` on every 15-second tick, halting all notification generation.

---

### Step 3: Check Celery Worker & Celery Beat Health
```bash
# Verify container statuses and uptime
docker compose -f docker-compose.prod.yml ps

# Check Celery Beat logs (is it actively scheduling tasks?)
docker compose -f docker-compose.prod.yml logs --tail=100 celery-beat

# Check Celery Worker logs (are tasks executing or throwing errors?)
docker compose -f docker-compose.prod.yml logs --tail=100 celery
```
> **Red Flags to Look For:**
> - `celery-beat` showing `ERROR: Pidfile (/tmp/celerybeat.pid) already exists` or repeating restart loops.
> - `celery` showing `OutOfMemoryError` or worker child processes dying (`WorkerLostError`).
> - Logs showing `⏭️ SKIPPED: Previous live task still running.` (Redis lock stuck).

---

### Step 4: Check Redis Health & Persistence Write Errors
```bash
docker compose -f docker-compose.prod.yml exec redis redis-cli info persistence | grep rdb_last_bgsave_status
```
> **Red Flag:** If `rdb_last_bgsave_status:err`, Redis has activated `stop-writes-on-bgsave-error`. Redis will **refuse all write commands** (including Celery task queueing and locks), silently stalling the entire background pipeline!

---

### Step 5: Verify Firebase SDK Initialization & Test Push
Test whether Firebase can send push notifications directly from inside the running Docker container:
```bash
docker compose -f docker-compose.prod.yml exec web python -c "
from notifications.services import NotificationService
from django.conf import settings
import firebase_admin

print('FIREBASE_CONFIGURED:', getattr(settings, 'FIREBASE_CONFIGURED', None))
print('FIREBASE_CREDENTIALS_PATH:', getattr(settings, 'FIREBASE_CREDENTIALS_PATH', None))
NotificationService.ensure_firebase_initialized()
print('Firebase Apps initialized:', len(firebase_admin._apps))
"
```
> **Red Flag:** If `FIREBASE_CONFIGURED: False` or `ERROR: firebase-credentials.json is a DIRECTORY`, Docker mounted a directory instead of a file.

Send a live test push to the global topic directly from the VPS:
```bash
curl -X POST http://localhost:8000/notifications/test-push/ \
  -H "Content-Type: application/json" \
  -d '{"topic": "global_en", "title": "Diagnostic Ping", "body": "Testing backend push pipeline"}'
```

---

## 🔧 3. REQUIRED PRODUCTION FIXES

Below are the exact code and configuration updates needed to make the notification pipeline resilient against multi-day degradation:

### Fix 1: Configure Docker Log Rotation (`docker-compose.prod.yml`)
Add standard log rotation across all containers to prevent host disk exhaustion:

```yaml
# Add to each service (web, asgi, celery, celery-beat, db, redis, nginx)
logging:
  driver: "json-file"
  options:
    max-size: "50m"
    max-file: "3"
```

---

### Fix 2: Prevent Celery Worker Memory Leaks (`docker-compose.prod.yml`)
Add `--max-tasks-per-child=1000` to automatically recycle Celery worker child processes before memory leaks cause OOM termination:

```yaml
  celery:
    build: .
    image: itzwasik/scorelivepro-web:latest
    restart: unless-stopped
    command: celery -A config worker -l info -Q high_priority,celery -c 4 --max-tasks-per-child=1000
    env_file:
      - .env
    depends_on:
      - redis
      - db
```

---

### Fix 3: Celery Beat Stale PID & Lock Cleanup (`docker-compose.prod.yml`)
Ensure stale lock and PID files are cleared before Celery Beat boots:

```yaml
  celery-beat:
    build: .
    image: itzwasik/scorelivepro-web:latest
    restart: unless-stopped
    command: sh -c "rm -f /tmp/celerybeat.pid /tmp/celerybeat-schedule* && celery -A config beat -l info -s /tmp/celerybeat-schedule"
    env_file:
      - .env
    depends_on:
      - redis
```

---

### Fix 4: Index `NotificationLog` to Prevent Statement Timeouts
In `apps/notifications/models.py`, `save_fixture_from_api` regularly checks:
```python
already_sent = NotificationLog.objects.filter(
    data__match_id=str(fixture.id),
    data__team_id=str(fixture.home_team.id),
    data__score=f"{new_goals_home} - {new_goals_away}",
    event_type='GOAL'
).exists()
```
Because `NotificationLog` has **no index** on `event_type` + `created_at` or `data`, table scans on 50,000+ rows trigger PostgreSQL's `statement_timeout=30000` (set in `prod.py`).

**Update `apps/notifications/models.py`:**
```python
class NotificationLog(models.Model):
    ...
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['topic', 'created_at']),
            models.Index(fields=['event_type', 'created_at']),
        ]
```
Run `python manage.py makemigrations notifications && python manage.py migrate` to apply.

---

### Fix 5: Periodic Pruning of Stale Notification Logs (`config/celery.py`)
Add an automated maintenance task to delete `NotificationLog` records older than 14 days so the table size remains bounded:

In `apps/notifications/tasks.py`:
```python
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

@shared_task
def prune_old_notification_logs_task():
    """Prune logs older than 14 days to keep DB fast and prevent storage bloat."""
    from .models import NotificationLog
    cutoff = timezone.now() - timedelta(days=14)
    deleted, _ = NotificationLog.objects.filter(created_at__lt=cutoff).delete()
    print(f"🧹 Pruned {deleted} old notification logs.")
    return f"Pruned {deleted} logs."
```

In `config/celery.py` (inside `app.conf.beat_schedule`):
```python
    'prune-old-notification-logs': {
        'task': 'notifications.tasks.prune_old_notification_logs_task',
        'schedule': crontab(hour=4, minute=0), # Daily at 4:00 AM
    },
```

---

### Fix 6: Verify Match Followers for Test Devices
Remember that the backend diff engine only pushes match events if `has_followers` is `True`:
```python
has_followers = FanProfile.objects.filter(
    Q(favorite_teams=home_team) | 
    Q(favorite_teams=away_team) | 
    Q(favorite_leagues=league_obj) |
    Q(favorite_fixtures=fixture)
).exists() or GuestFavorite.objects.filter(
    Q(favorite_teams=home_team) | 
    Q(favorite_teams=away_team) | 
    Q(favorite_leagues=league_obj) |
    Q(favorite_fixtures=fixture)
).exists()
```
If a tester does not star/favorite the fixture or its teams, the backend will **never** generate an alert for that match.

To verify whether your test guest device is registered and following matches:
```bash
docker compose -f docker-compose.prod.yml exec web python diagnose_inbox.py <YOUR_GUEST_UUID>
```
This script prints out:
1. All teams, leagues, and fixtures favorited by that UUID.
2. All `UserDevice` FCM tokens and active status.
3. All topic matching logs.
