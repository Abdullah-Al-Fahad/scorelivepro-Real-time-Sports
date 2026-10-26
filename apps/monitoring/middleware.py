# apps/monitoring/middleware.py
import time
import re
import redis
import json
from django.conf import settings
from django.utils import timezone
from .models import APILog, ErrorLog, GuestDevice

# Redis Connection
redis_client = redis.StrictRedis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)

class MonitoringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Compiles regex once: Matches numbers or UUIDs in paths
        self.id_pattern = re.compile(r'\/(\d+|[a-f0-9-]{36})')

    def __call__(self, request):
        start_time = time.time()
        
        # 1. Identity Resolution
        user = request.user if request.user.is_authenticated else None
        device_id = request.headers.get('X-Device-ID')

        # 2. Live Tracking & Guest Registration
        self.track_presence(request, user, device_id)

        response = self.get_response(request)
        
        duration = (time.time() - start_time) * 1000 

        # 3. Logging (Only API routes, ignore static/admin to save DB space)
        if request.path.startswith('/api/') or request.path.startswith('/sports/'):
            self.log_request(request, response, duration, user, device_id)

        return response

    def track_presence(self, request, user, device_id):
        """
        Handles Real-time presence (Redis) and Guest Registration (DB).
        """
        try:
            timestamp = time.time()
            path = request.path

            # A. Guest Registration (Idempotent)
            if device_id and not user:
                # Cache check optimization: Don't hit DB if we know this device
                cache_key = f"known_device:{device_id}"
                if not redis_client.exists(cache_key):
                    GuestDevice.objects.get_or_create(
                        device_id=device_id,
                        defaults={'ip_address': self.get_client_ip(request)}
                    )
                    redis_client.setex(cache_key, 86400, "1") # Cache for 24h

            # B. Active Users (Redis ZSET)
            # We use two sets: one for Registered, one for Guests
            if user:
                redis_client.zadd('active_users:registered', {str(user.id): timestamp})
                # Rich Presence (What are they doing?)
                redis_client.setex(f"presence:user:{user.id}", 300, json.dumps({
                    "status": "Online", "path": path, "last_seen": str(timezone.now())
                }))
            elif device_id:
                redis_client.zadd('active_users:guests', {str(device_id): timestamp})
                redis_client.setex(f"presence:device:{device_id}", 300, json.dumps({
                    "status": "Guest", "path": path, "last_seen": str(timezone.now())
                }))

        except Exception as e:
            # Fail silently to not crash the request
            print(f"Tracking Middleware Error: {e}")

    def log_request(self, request, response, duration, user, device_id):
        """
        Logs the API request and handles errors if necessary.
        """
        try:
            # Normalize Path: /api/matches/152 -> /api/matches/{id}
            clean_path = self.id_pattern.sub('/{id}', request.path)
            ip = self.get_client_ip(request)

            # 1. Save Access Log (Only log failed requests / errors to prevent DB bloat)
            if response.status_code >= 400:
                APILog.objects.create(
                    endpoint=clean_path,
                    method=request.method,
                    response_time_ms=duration,
                    status_code=response.status_code,
                    ip_address=ip,
                    device_id=device_id,
                    user=user
                )
                # 2. Error Aggregation logic
                self.handle_error_log(request, response, clean_path)

        except Exception as e:
            print(f"Logging Error: {e}")

    def handle_error_log(self, request, response, clean_path):
        """
        Aggregates errors into the ErrorLog model.
        """
        try:
            # Determine error level
            level = 'medium'
            if response.status_code >= 500:
                level = 'critical'
            
            # Use the exception message if available (from context), otherwise generic
            message = f"HTTP {response.status_code} on {request.method} {clean_path}"
            
            # Update existing unresolved error or create new one
            error_log, created = ErrorLog.objects.get_or_create(
                path=clean_path,
                level=level,
                is_resolved=False,
                defaults={'message': message}
            )
            
            if not created:
                error_log.count += 1
                error_log.save()
                
        except Exception as e:
            print(f"Error Logging Failed: {e}")

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip