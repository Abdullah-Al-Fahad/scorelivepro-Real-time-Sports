import requests
import json
import hashlib
import time
import redis
from datetime import datetime, timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Q 
from django.utils import timezone
from django.core.cache import cache
from celery import shared_task, chain
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# Local Imports
from .models import (Fixture, HeadToHead, League, Season, Country, Team, Venue, 
                     Standing, Timezone, FixtureLineup, FixtureStatistic, TeamSquad, PlayerProfile, PlayerStatList,
                     TeamStatistic, TeamCoachList)
from .serializers import FixtureSerializer
from notifications.models import ScheduledNotification
from notifications.services import NotificationService

BASE_URL = "https://v3.football.api-sports.io"

def get_headers():
    return {
        'x-rapidapi-host': "v3.football.api-sports.io",
        'x-apisports-key': settings.API_FOOTBALL_KEY
    }

# =========================================================
#  🛡️ ROBUST REDIS LOCKING (Prevents Deadlocks)
# =========================================================

def get_redis_client():
    """
    Connects directly to the Redis container using the CELERY_BROKER_URL.
    This bypasses Django's 'LocMemCache' default to ensure locks are 
    truly shared across all worker processes.
    """
    return redis.from_url(settings.CELERY_BROKER_URL)

def acquire_lock(lock_id, expire=60):
    """
    Attempts to acquire a lock using Redis atomic SETNX.
    Returns True if lock acquired, False if already locked.
    """
    try:
        r = get_redis_client()
        # set(nx=True) is atomic. Only one worker can succeed.
        is_locked = r.set(lock_id, "LOCKED", nx=True, ex=expire)
        return bool(is_locked)
    except Exception as e:
        print(f"⚠️ REDIS LOCK ERROR: {e}")
        # If Redis is down, we fail OPEN (True) to keep the system running.
        return True

def release_lock(lock_id):
    try:
        r = get_redis_client()
        r.delete(lock_id)
    except Exception:
        pass

# =========================================================
#  1. CORE API PARSER & CONFEDERATION ENGINE
# =========================================================

EUROPEAN_COUNTRIES = {
    'england', 'spain', 'italy', 'germany', 'france', 'portugal', 'netherlands',
    'belgium', 'turkey', 'scotland', 'norway', 'sweden', 'denmark', 'poland',
    'greece', 'switzerland', 'austria', 'russia', 'ukraine', 'croatia', 'serbia',
    'czech-republic', 'czech republic', 'romania', 'hungary', 'bulgaria', 'cyprus',
    'israel', 'slovakia', 'slovenia', 'finland', 'ireland', 'wales', 'northern-ireland',
    'northern ireland', 'iceland', 'bosnia', 'albania', 'belarus', 'armenia',
    'azerbaijan', 'georgia', 'kazakhstan', 'moldova', 'malta', 'luxembourg',
    'estonia', 'latvia', 'lithuania', 'faroe-islands', 'faroe islands', 'gibraltar',
    'andorra', 'san-marino', 'san marino', 'kosovo', 'liechtenstein', 'montenegro',
    'north-macedonia', 'north macedonia'
}

SOUTH_AMERICAN_COUNTRIES = {
    'brazil', 'argentina', 'colombia', 'chile', 'uruguay', 'ecuador',
    'peru', 'paraguay', 'bolivia', 'venezuela', 'guyana', 'suriname'
}

NORTH_CENTRAL_AMERICAN_COUNTRIES = {
    'usa', 'united-states', 'mexico', 'canada', 'costa-rica', 'costa rica',
    'honduras', 'jamaica', 'panama', 'guatemala', 'el-salvador', 'el salvador',
    'trinidad-and-tobago', 'trinidad and tobago', 'haiti', 'cuba', 'curacao',
    'curaçao', 'dominican-republic', 'dominican republic', 'nicaragua', 'martinique',
    'guadeloupe', 'barbados', 'bermuda', 'belize', 'suriname', 'puerto-rico'
}

ASIAN_COUNTRIES = {
    'saudi-arabia', 'saudi arabia', 'japan', 'south-korea', 'south korea',
    'china', 'australia', 'uae', 'united-arab-emirates', 'qatar', 'iran',
    'iraq', 'india', 'thailand', 'indonesia', 'uzbekistan', 'vietnam',
    'jordan', 'bahrain', 'kuwait', 'oman', 'lebanon', 'syria', 'palestine',
    'singapore', 'malaysia', 'philippines', 'myanmar', 'hong-kong', 'hong kong',
    'tajikistan', 'turkmenistan', 'kyrgyzstan'
}

AFRICAN_COUNTRIES = {
    'egypt', 'morocco', 'algeria', 'tunisia', 'south-africa', 'south africa',
    'nigeria', 'ghana', 'senegal', 'ivory-coast', 'ivory coast', 'cote-d-ivoire',
    'cameroon', 'angola', 'congo', 'congo dr', 'dr congo', 'kenya', 'uganda',
    'tanzania', 'zambia', 'zimbabwe', 'mali', 'burkina-faso', 'burkina faso',
    'guinea', 'benin', 'togo', 'gambia', 'sudan', 'ethiopia', 'rwanda',
    'libya', 'mauritania', 'seychelles', 'madagascar', 'mozambique', 'botswana',
    'namibia', 'malawi', 'eswatini', 'lesotho', 'cape-verde', 'sierra-leone'
}

OCEANIAN_COUNTRIES = {
    'new-zealand', 'new zealand', 'fiji', 'papua-new-guinea', 'papua new guinea',
    'solomon-islands', 'tahiti', 'vanuatu', 'samoa', 'tonga', 'new-caledonia'
}

KNOWN_TOURNAMENTS = {
    2: 'Europe', 3: 'Europe', 848: 'Europe', 5: 'Europe', 525: 'Europe',
    4: 'Europe', 849: 'Europe', 765: 'Europe', 823: 'Europe',
    12: 'Africa', 20: 'Africa', 6: 'Africa', 1164: 'Africa',
    17: 'Asia', 18: 'Asia', 7: 'Asia', 1140: 'Asia', 1162: 'Asia',
    11: 'South America', 13: 'South America', 14: 'South America', 9: 'South America',
    16: 'North & Central America', 22: 'North & Central America', 1058: 'North & Central America', 856: 'North & Central America',
    27: 'Oceania', 24: 'Oceania', 25: 'Oceania', 1045: 'Oceania',
    1: 'World', 15: 'World', 10: 'World', 667: 'World'
}

def resolve_league_region(league_id, league_name, country_name):
    if league_id in KNOWN_TOURNAMENTS:
        return KNOWN_TOURNAMENTS[league_id]

    lname = (league_name or '').lower()
    cname = (country_name or '').lower().strip()

    if any(k in lname for k in ['uefa', 'euro ']): return 'Europe'
    if any(k in lname for k in ['caf ', 'africa']): return 'Africa'
    if any(k in lname for k in ['afc ', 'asian ']): return 'Asia'
    if any(k in lname for k in ['conmebol', 'libertadores', 'sudamericana', 'copa américa', 'copa america']): return 'South America'
    if any(k in lname for k in ['concacaf', 'leagues cup']): return 'North & Central America'
    if 'ofc ' in lname: return 'Oceania'
    if any(k in lname for k in ['fifa', 'world cup', 'olympics']): return 'World'

    if cname in EUROPEAN_COUNTRIES: return 'Europe'
    if cname in SOUTH_AMERICAN_COUNTRIES: return 'South America'
    if cname in NORTH_CENTRAL_AMERICAN_COUNTRIES: return 'North & Central America'
    if cname in ASIAN_COUNTRIES: return 'Asia'
    if cname in AFRICAN_COUNTRIES: return 'Africa'
    if cname in OCEANIAN_COUNTRIES: return 'Oceania'

    return 'World'

def save_fixture_from_api(item):
    """
    Parses a single fixture object from the API response.
    Saves to the central Fixture table.
    
    UPDATES:
    - Implements 'Idempotency Checks' to prevent duplicate alerts.
    - triggers NotificationService immediately instead of returning a list.
    """
    # Local imports to prevent circular dependency issues
    from notifications.models import NotificationLog
    from notifications.services import NotificationService
    from django.db import transaction

    def _dispatch_alert(func, *args, **kwargs):
        import sys
        if 'test' in sys.argv:
            transaction.on_commit(lambda: func(*args, **kwargs))
        else:
            import threading
            from django.db import connection
            def _runner():
                try:
                    func(*args, **kwargs)
                except Exception as err:
                    print(f"⚠️ [Alert Dispatch Error]: {err}")
                finally:
                    connection.close()
            transaction.on_commit(lambda: threading.Thread(target=_runner, daemon=True).start())
    
    try:
        f = item.get('fixture', {})
        l = item.get('league', {})
        t = item.get('teams', {})
        g = item.get('goals', {})
        s = item.get('score', {})
        e = item.get('events', []) 
        
        if not f or not l or not t or 'id' not in f:
            return None, []

        fixture_id = f['id']
        new_status = f.get('status', {}).get('short')
        new_goals_home = g.get('home')
        new_goals_away = g.get('away')
        
        # --- CAPTURE OLD STATE ---
        old_status = None
        old_goals_home = None
        old_goals_away = None
        old_events = []
        fixture_exists = False
        
        try:
            existing_fixture = Fixture.objects.get(id=fixture_id)
            old_status = existing_fixture.status_short
            old_goals_home = existing_fixture.goals.get('home')
            old_goals_away = existing_fixture.goals.get('away')
            old_events = existing_fixture.events or []
            fixture_exists = True
        except Fixture.DoesNotExist:
            fixture_exists = False

        # --- RESOLVE RELATIONS ---
        country_obj = None
        if l.get('country'):
            country_obj, _ = Country.objects.get_or_create(
                name=l['country'],
                defaults={'flag': l.get('flag')}
            )

        season_obj, _ = Season.objects.get_or_create(year=l['season'])

        league_obj = League.objects.filter(id=l['id']).first()
        if not league_obj:
            league_obj, _ = League.objects.get_or_create(
                id=l['id'],
                defaults={
                    'name': l.get('name', 'Unknown'),
                    'country': country_obj,
                    'logo': l.get('logo'),
                    'season_year': l['season'],
                    'type': l.get('type', 'League'),
                    'region': resolve_league_region(l['id'], l.get('name'), l.get('country'))
                }
            )

        venue_obj = None
        if f.get('venue'):
            v_data = f['venue']
            v_id = v_data.get('id')
            v_name = v_data.get('name')
            
            if not v_id and v_name:
                # API sometimes omits venue ID but sends name/city. Generate deterministic negative ID.
                hash_str = f"{v_name}_{v_data.get('city', '')}".encode('utf-8')
                v_id = -(int(hashlib.md5(hash_str).hexdigest()[:8], 16) % 100000000)
                if v_id == 0: v_id = -1
                
            if v_id:
                venue_name = v_name or f"Venue {abs(v_id)}"
                venue_obj = Venue.objects.filter(id=v_id).first()
                if not venue_obj:
                    venue_obj, _ = Venue.objects.get_or_create(
                        id=v_id,
                        defaults={'name': venue_name, 'city': v_data.get('city')}
                    )

        home_team = Team.objects.filter(id=t['home']['id']).first()
        if not home_team:
            home_team, _ = Team.objects.get_or_create(
                id=t['home']['id'],
                defaults={'name': t['home'].get('name', ''), 'logo': t['home'].get('logo'), 'venue': venue_obj}
            )
        away_team = Team.objects.filter(id=t['away']['id']).first()
        if not away_team:
            away_team, _ = Team.objects.get_or_create(
                id=t['away']['id'],
                defaults={'name': t['away'].get('name', ''), 'logo': t['away'].get('logo')}
            )

        # --- SAVE FIXTURE TO DB ---
        status_data = f.get('status', {})
        
        # --- MONOTONIC STATUS GUARD ---
        # A finished match (FT, AET, PEN, ABD, AWD, WO) must never regress to an in-play (2H, 1H, HT) or pre-match (NS) status
        if fixture_exists and old_status in Fixture.FINISHED_STATUSES:
            if new_status not in Fixture.FINISHED_STATUSES:
                print(f"🛡️ [MONOTONIC GUARD] Prevented status regression for Match {fixture_id}: attempted {old_status} -> {new_status}")
                new_status = old_status
                status_data['short'] = old_status
                status_data['long'] = existing_fixture.status_long
                status_data['elapsed'] = existing_fixture.elapsed or 90
                status_data['extra'] = existing_fixture.extra
                g = existing_fixture.goals or g
                s = existing_fixture.score or s

        fixture, created = Fixture.objects.update_or_create(
            id=f['id'],
            defaults={
                'date': f.get('date'),
                'timestamp': f.get('timestamp', 0),
                'timezone': f.get('timezone', 'UTC'),
                'referee': f.get('referee'),
                'round': l.get('round'),
                'status_long': status_data.get('long'),
                'status_short': new_status,
                'elapsed': status_data.get('elapsed'),
                'extra': status_data.get('extra'),
                'league': league_obj,
                'season': season_obj,
                'home_team': home_team,
                'away_team': away_team,
                'venue': venue_obj,
                'periods': f.get('periods', {}),
                'goals': g,
                'score': s,
                'events': e
            }
        )

        # =========================================================
        #  NOTIFICATIONS & TRIGGERS (UPDATED)
        # =========================================================
        
        from users.models import FanProfile, GuestFavorite
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

        alert_eligible_statuses = ['1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT', 'LIVE']
        finished_statuses = ['FT', 'AET', 'PEN']
        event_lookback = timezone.now() - timedelta(hours=4)

        # 1. Goal Alerts
        if has_followers and fixture_exists and (new_status in alert_eligible_statuses or new_status in finished_statuses):
            # Normalize None to 0 for initial match state
            old_h = 0 if old_goals_home is None else old_goals_home
            old_a = 0 if old_goals_away is None else old_goals_away

            # A. Check Home Team Goal
            if new_goals_home is not None and new_goals_home > old_h:
                # [IDEMPOTENCY CHECK] Verify if we already sent this specific score for this team
                already_sent = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    data__team_id=str(fixture.home_team.id),
                    data__score=f"{new_goals_home} - {new_goals_away}",
                    event_type='GOAL',
                    created_at__gte=event_lookback
                ).exists()

                if not already_sent:
                    _dispatch_alert(
                        NotificationService.send_goal_alert,
                        scoring_team_name=fixture.home_team.name, 
                        home_team_name=fixture.home_team.name,
                        away_team_name=fixture.away_team.name,
                        score=f"{new_goals_home} - {new_goals_away}", 
                        home_team_id=fixture.home_team.id,
                        away_team_id=fixture.away_team.id,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

            # B. Check Away Team Goal
            if new_goals_away is not None and new_goals_away > old_a:
                # [IDEMPOTENCY CHECK]
                already_sent = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    data__team_id=str(fixture.away_team.id),
                    data__score=f"{new_goals_home} - {new_goals_away}",
                    event_type='GOAL',
                    created_at__gte=event_lookback
                ).exists()

                if not already_sent:
                    _dispatch_alert(
                        NotificationService.send_goal_alert,
                        scoring_team_name=fixture.away_team.name, 
                        home_team_name=fixture.home_team.name,
                        away_team_name=fixture.away_team.name,
                        score=f"{new_goals_home} - {new_goals_away}", 
                        home_team_id=fixture.home_team.id,
                        away_team_id=fixture.away_team.id,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

            # C. Check VAR Disallowed Goal (Score decreased due to VAR overturn)
            if (new_goals_home is not None and old_h is not None and new_goals_home < old_h) or \
               (new_goals_away is not None and old_a is not None and new_goals_away < old_a):
                disallowed_team = fixture.home_team if (new_goals_home < old_h) else fixture.away_team
                new_score_str = f"{new_goals_home} - {new_goals_away}"
                already_sent_disallowed = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    data__score=new_score_str,
                    event_type='DISALLOWED_GOAL',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_disallowed:
                    _dispatch_alert(
                        NotificationService.send_disallowed_goal_alert,
                        team_name=disallowed_team.name,
                        home_team_name=fixture.home_team.name,
                        away_team_name=fixture.away_team.name,
                        score=new_score_str,
                        home_team_id=fixture.home_team.id,
                        away_team_id=fixture.away_team.id,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.5. Card, Substitution, and VAR Alerts
        if has_followers and fixture_exists:
            def make_event_key(ev):
                time_elapsed = ev.get('time', {}).get('elapsed') or 0
                time_extra = ev.get('time', {}).get('extra') or ''
                team_id = ev.get('team', {}).get('id') or ''
                ev_type = ev.get('type') or ''
                player_id = ev.get('player', {}).get('id') or ev.get('player', {}).get('name') or ''
                detail = ev.get('detail') or ''
                return f"{time_elapsed}_{time_extra}_{team_id}_{ev_type}_{player_id}_{detail}"

            old_event_keys = {make_event_key(ev) for ev in old_events}
            
            for ev in e:
                ev_key = make_event_key(ev)
                if ev_key not in old_event_keys:
                    elapsed = ev.get('time', {}).get('elapsed') or 0
                    current_fixture_elapsed = fixture.elapsed or 0
                    
                    if not old_events and elapsed < (current_fixture_elapsed - 3):
                        continue
                        
                    ev_type = ev.get('type')
                    team_name = ev.get('team', {}).get('name') or ''
                    team_id = ev.get('team', {}).get('id') or ''
                    detail = ev.get('detail') or ''
                    player_name = ev.get('player', {}).get('name') or ''
                    assist_name = ev.get('assist', {}).get('name') or ''
                    
                    if ev_type == 'Card':
                        already_sent = NotificationLog.objects.filter(
                            data__match_id=str(fixture.id),
                            data__player_name=player_name,
                            data__card_type=detail,
                            event_type='CARD',
                            created_at__gte=event_lookback
                        ).exists()
                        if not already_sent:
                            _dispatch_alert(
                                NotificationService.send_card_alert,
                                player_name=player_name,
                                card_type=detail,
                                team_name=team_name,
                                team_id=team_id,
                                match_id=fixture.id,
                                elapsed_time=elapsed,
                                league_id=fixture.league_id
                            )
                            
                    elif ev_type == 'subst':
                        already_sent = NotificationLog.objects.filter(
                            data__match_id=str(fixture.id),
                            data__player_in=player_name,
                            data__player_out=assist_name,
                            event_type='SUBSTITUTION',
                            created_at__gte=event_lookback
                        ).exists()
                        if not already_sent:
                            _dispatch_alert(
                                NotificationService.send_substitution_alert,
                                player_in=player_name,
                                player_out=assist_name,
                                team_name=team_name,
                                team_id=team_id,
                                match_id=fixture.id,
                                elapsed_time=elapsed,
                                league_id=fixture.league_id
                            )
                            
                    elif ev_type == 'Var':
                        already_sent = NotificationLog.objects.filter(
                            data__match_id=str(fixture.id),
                            data__detail=detail,
                            data__elapsed=str(elapsed),
                            event_type='VAR',
                            created_at__gte=event_lookback
                        ).exists()
                        if not already_sent:
                            _dispatch_alert(
                                NotificationService.send_var_alert,
                                detail=detail,
                                team_name=team_name,
                                team_id=team_id,
                                match_id=fixture.id,
                                elapsed_time=elapsed,
                                league_id=fixture.league_id
                            )

                    elif ev_type == 'Goal' and detail == 'Missed Penalty':
                        already_sent = NotificationLog.objects.filter(
                            data__match_id=str(fixture.id),
                            data__player_name=player_name,
                            data__elapsed=str(elapsed),
                            event_type='MISSED_PENALTY',
                            created_at__gte=event_lookback
                        ).exists()
                        if not already_sent:
                            _dispatch_alert(
                                NotificationService.send_missed_penalty_alert,
                                player_name=player_name,
                                team_name=team_name,
                                team_id=team_id,
                                match_id=fixture.id,
                                elapsed_time=elapsed,
                                league_id=fixture.league_id
                            )

                    elif ev_type == 'Goal' and detail == 'Own Goal':
                        already_sent = NotificationLog.objects.filter(
                            data__match_id=str(fixture.id),
                            data__player_name=player_name,
                            data__elapsed=str(elapsed),
                            event_type='OWN_GOAL',
                            created_at__gte=event_lookback
                        ).exists()
                        if not already_sent:
                            og_score = f"{new_goals_home if new_goals_home is not None else 0} - {new_goals_away if new_goals_away is not None else 0}"
                            _dispatch_alert(
                                NotificationService.send_own_goal_alert,
                                player_name=player_name,
                                team_name=team_name,
                                team_id=team_id,
                                home_team_name=fixture.home_team.name,
                                away_team_name=fixture.away_team.name,
                                score=og_score,
                                match_id=fixture.id,
                                elapsed_time=elapsed,
                                league_id=fixture.league_id
                            )

        # 1.6. Kick-off (1H) Whistle Trigger
        if fixture_exists and old_status in ['NS', 'TBD', 'PST', None] and new_status in ['1H', 'LIVE']:
            try:
                # Evict live scores hash and REST cache to ensure immediate live broadcast and prevent stale NS overrides
                get_redis_client().delete("live_scores_last_hash")
            except Exception:
                pass
            if has_followers:
                already_sent_ko = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='KICKOFF',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_ko:
                    _dispatch_alert(
                        NotificationService.send_kickoff_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.7. Second Half (2H) Whistle Trigger
        if fixture_exists and old_status == 'HT' and new_status == '2H':
            if has_followers:
                already_sent_2h = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='SECOND_HALF',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_2h:
                    cur_score = f"{new_goals_home if new_goals_home is not None else 0} - {new_goals_away if new_goals_away is not None else 0}"
                    _dispatch_alert(
                        NotificationService.send_second_half_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=cur_score,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.8. Half-Time (HT) Whistle Trigger
        if fixture_exists and old_status != 'HT' and new_status == 'HT':
            if has_followers:
                already_sent_ht = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='HALF_TIME',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_ht:
                    ht_score = f"{new_goals_home if new_goals_home is not None else 0} - {new_goals_away if new_goals_away is not None else 0}"
                    _dispatch_alert(
                        NotificationService.send_half_time_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=ht_score,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.9. Extra Time (ET) Trigger
        if fixture_exists and old_status in ['2H', 'FT', 'BT'] and new_status == 'ET':
            if has_followers:
                already_sent_et = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='EXTRA_TIME',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_et:
                    cur_score = f"{new_goals_home if new_goals_home is not None else 0} - {new_goals_away if new_goals_away is not None else 0}"
                    _dispatch_alert(
                        NotificationService.send_extra_time_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=cur_score,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.10. Penalty Shootout (P) Trigger
        if fixture_exists and old_status in ['ET', 'BT'] and new_status == 'P':
            if has_followers:
                already_sent_pen = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='PENALTY_SHOOTOUT',
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_pen:
                    _dispatch_alert(
                        NotificationService.send_penalty_shootout_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )

        # 1.11. Match Disruption / Postponement / Suspension / Abandonment Triggers
        disruption_statuses = ['PST', 'SUSP', 'INT', 'ABD', 'CANC', 'AWD', 'WO']
        if fixture_exists and old_status != new_status and new_status in disruption_statuses:
            if has_followers:
                disruption_code_map = {
                    'PST': 'POSTPONED',
                    'SUSP': 'SUSPENDED',
                    'INT': 'INTERRUPTED',
                    'ABD': 'ABANDONED',
                    'CANC': 'CANCELLED',
                    'AWD': 'WALKOVER',
                    'WO': 'WALKOVER',
                }
                ev_code = disruption_code_map.get(new_status, 'POSTPONED')
                already_sent_disrupt = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type=ev_code,
                    created_at__gte=event_lookback
                ).exists()
                if not already_sent_disrupt:
                    _dispatch_alert(
                        NotificationService.send_match_disruption_alert,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        status_short=new_status,
                        match_id=fixture.id,
                        league_id=fixture.league_id
                    )


        # 2. Match Finished (FT) Trigger
        if fixture_exists and old_status not in finished_statuses and new_status in finished_statuses:
            if has_followers:
                # [IDEMPOTENCY CHECK] Ensure we haven't already sent FT for this match
                already_sent_ft = NotificationLog.objects.filter(
                    data__match_id=str(fixture.id),
                    event_type='FULL_TIME',
                    created_at__gte=event_lookback
                ).exists()
                
                if not already_sent_ft:
                    _dispatch_alert(
                        NotificationService.send_match_result_alert,
                        home_team=fixture.home_team, 
                        away_team=fixture.away_team,
                        score=f"{new_goals_home}-{new_goals_away}", 
                        match_id=fixture.id, 
                        league_id=fixture.league_id
                    )
                
            # EVENT-DRIVEN H2H UPDATE (always runs regardless of followers)
            try:
                print(f"🏁 Match {fixture.id} finished. Triggering Smart H2H update.")
                update_h2h_single_pair.delay(fixture.home_team.id, fixture.away_team.id, fixture.id)
            except NameError:
                print("Warning: update_h2h_single_pair task not found/imported.")

        # Return fixture and empty list (to maintain compatibility if caller expects tuple)
        return fixture, []

    except Exception as e:
        print(f"Error saving fixture: {e}")
        return None, []

# =========================================================
#  2. LIVE UPDATES & MAINTENANCE
# =========================================================

@shared_task
def update_live_fixtures():
    """
    Robust Live Update with Zombie Cleanup & Direct Redis Locking.
    """
    print("🔄 TASK START: update_live_fixtures")
    lock_id = "task-lock-live-updates"
    
    if not acquire_lock(lock_id, expire=15):
        print("⏭️ SKIPPED: Previous live task still running.")
        return "Skipped: Locked"

    try:
        url = f"{BASE_URL}/fixtures"
        live_statuses = Fixture.LIVE_STATUSES
        # Optimize API usage: Only monitor matches started within the last 4 hours for zombie cleanup.
        cutoff_time = timezone.now() - timedelta(hours=4)
        db_live_ids = set(Fixture.objects.filter(
            status_short__in=live_statuses,
            date__gte=cutoff_time
        ).values_list('id', flat=True))

        print("🌐 Fetching API data...")
        response = requests.get(url, headers=get_headers(), params={'live': 'all'}, timeout=10)
        resp_json = response.json()
        
        if resp_json.get('errors'):
            print(f"⚠️ API Error: {resp_json['errors']}")
            return "Skipped: API Error"

        api_data = resp_json.get('response', [])
        api_live_ids = set()
        all_pending_alerts = []

        print(f"📦 API returned {len(api_data)} matches.")

        if api_data:
            print("💾 Saving Live Matches...")
            with transaction.atomic():
                for item in api_data:
                    fixture, alerts = save_fixture_from_api(item)
                    if fixture:
                        api_live_ids.add(fixture.id)
                        all_pending_alerts.extend(alerts)
        
        # ZOMBIE CLEANUP: matches stuck as LIVE in DB but not in API response
        zombie_ids = list(db_live_ids - api_live_ids)
        if zombie_ids:
            print(f"🧟 Found {len(zombie_ids)} finished matches. Force updating...")
            chunk_size = 20
            for i in range(0, len(zombie_ids), chunk_size):
                if i > 0:
                    time.sleep(1.0)
                chunk = zombie_ids[i:i + chunk_size]
                ids_str = '-'.join(map(str, chunk))
                try:
                    z_response = requests.get(url, headers=get_headers(), params={'ids': ids_str}, timeout=10)
                    z_data = z_response.json().get('response', [])
                    with transaction.atomic():
                        for item in z_data:
                            fixture, alerts = save_fixture_from_api(item)
                            if fixture:
                                all_pending_alerts.extend(alerts)
                except Exception as e:
                    print(f"❌ Error updating finished matches {chunk}: {e}")

        # STUCK NS RECOVERY: matches that should have kicked off but API never promoted to live
        # Grace period: 5 minutes after scheduled kickoff. Max lookback: 3 hours to limit API calls.
        stuck_ns_cutoff = timezone.now() - timedelta(minutes=5)
        stuck_ns_max_age = timezone.now() - timedelta(hours=3)
        stuck_ns_ids = list(Fixture.objects.filter(
            status_short__in=['NS', 'TBD'],
            date__lte=stuck_ns_cutoff,
            date__gte=stuck_ns_max_age,
        ).exclude(id__in=api_live_ids).values_list('id', flat=True))

        if stuck_ns_ids:
            print(f"⏰ Found {len(stuck_ns_ids)} fixtures stuck as NS past kickoff. Re-fetching...")
            chunk_size = 20
            for i in range(0, len(stuck_ns_ids), chunk_size):
                if i > 0:
                    time.sleep(1.0)
                chunk = stuck_ns_ids[i:i + chunk_size]
                ids_str = '-'.join(map(str, chunk))
                try:
                    ns_response = requests.get(url, headers=get_headers(), params={'ids': ids_str}, timeout=10)
                    ns_data = ns_response.json().get('response', [])
                    with transaction.atomic():
                        for item in ns_data:
                            fixture, alerts = save_fixture_from_api(item)
                            if fixture:
                                all_pending_alerts.extend(alerts)
                    print(f"  ✅ Re-synced {len(ns_data)} stuck-NS fixtures from API.")
                except Exception as e:
                    print(f"❌ Error re-syncing stuck NS chunk {chunk}: {e}")

        # Broadcast Logic
        fixtures_queryset = Fixture.objects.filter(status_short__in=live_statuses).select_related(
            'league', 'league__country', 'season', 'home_team', 'away_team', 'venue'
        ).order_by('date')
        
        serialized_matches = FixtureSerializer(fixtures_queryset, many=True).data
        print(f"📡 Broadcast Preparation: {len(serialized_matches)} active matches found in DB.")

        serialized_matches.sort(key=lambda x: x['id'])
        current_data_json = json.dumps(serialized_matches, sort_keys=True)
        current_hash = hashlib.md5(current_data_json.encode('utf-8')).hexdigest()
        cache_key = "live_scores_last_hash"
        
        # Use Redis directly for the hash check too, to be safe
        try:
            last_hash = get_redis_client().get(cache_key)
            if last_hash:
                last_hash = last_hash.decode('utf-8')
        except Exception:
            last_hash = None

        if current_hash != last_hash:
            print("⚡ HASH CHANGE DETECTED: Sending to Channel Layer...")
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "live_scores", {"type": "live_score_update", "data": serialized_matches}
            )
            try:
                get_redis_client().set(cache_key, current_hash, ex=600)
            except Exception:
                pass
            print("✅ Broadcast Sent.")
        else:
            print("💤 Data unchanged. No broadcast.")

        # (Pending alerts are now handled directly inside save_fixture_from_api)

        return f"Done. Matches: {len(serialized_matches)}"

    except Exception as e:
        return f"Failed: {e}"
    finally:
        release_lock(lock_id)

@shared_task
def fetch_teams_for_active_leagues():
    """
    Fetches teams for all currently active leagues.
    Protected by a DIRECT REDIS LOCK to prevent database deadlocks.
    """
    lock_id = "task-lock-fetch-teams"
    if not acquire_lock(lock_id, expire=600):
        print("⏭️ SKIPPED: Previous team update still running.")
        return "Skipped: Locked"

    try:
        # Optimize active leagues selection: Only fetch teams for leagues with upcoming/recent fixtures
        # or favorited leagues, AND that do not have any teams in the DB yet (teams__isnull=True).
        now = timezone.now()
        upcoming_league_ids = Fixture.objects.filter(
            date__range=(now - timedelta(days=2), now + timedelta(days=7))
        ).values_list('league_id', flat=True).distinct()

        from users.models import FanProfile
        favorited_league_ids = FanProfile.objects.filter(
            favorite_leagues__isnull=False
        ).values_list('favorite_leagues__id', flat=True).distinct()

        relevant_league_ids = set(upcoming_league_ids) | set(favorited_league_ids)

        active_leagues = League.objects.filter(
            id__in=relevant_league_ids,
            teams__isnull=True
        )
        count = 0
        print(f"🔄 START: Updating teams for {active_leagues.count()} leagues (out of {len(relevant_league_ids)} relevant)...")

        for league in active_leagues:
            url = f"{BASE_URL}/teams"
            params = {'league': league.id, 'season': league.season_year}
            try:
                response = requests.get(url, headers=get_headers(), params=params)
                time.sleep(0.5) 
                
                data = response.json().get('response', [])
                
                # Atomic transaction PER LEAGUE
                with transaction.atomic():
                    for item in data:
                        t = item['team']
                        v = item['venue']
                        
                        venue_obj = None
                        if v:
                            v_id = v.get('id')
                            v_name = v.get('name')
                            if not v_id and v_name:
                                hash_str = f"{v_name}_{v.get('city', '')}".encode('utf-8')
                                v_id = -(int(hashlib.md5(hash_str).hexdigest()[:8], 16) % 100000000)
                                if v_id == 0: v_id = -1
                                
                            if v_id:
                                venue_name = v_name or f"Venue {abs(v_id)}" 
                                venue_obj, _ = Venue.objects.update_or_create(
                                    id=v_id, 
                                    defaults={'name': venue_name, 'city': v.get('city')}
                                )
                        
                        # Save Team Basic Info
                        team_obj, created = Team.objects.update_or_create(
                            id=t['id'], 
                            defaults={
                                'name': t['name'], 
                                'code': t['code'], 
                                'country': t['country'], 
                                'logo': t['logo'], 
                                'venue': venue_obj
                            }
                        )
                        
                        # Link Team to this League
                        team_obj.leagues.add(league)

                count += 1
            except Exception as e:
                print(f"❌ Error fetching teams for league {league.id}: {e}")

        return f"Updated teams for {count} leagues."

    except Exception as e:
        return f"Failed: {e}"
    finally:
        release_lock(lock_id)
        print("🏁 Team update finished. Lock released.")

@shared_task
def initial_boot_sequence():
    """
    Runs ONCE on server start. Protected by DIRECT REDIS LOCK.
    """
    # Check if database is already populated
    if Country.objects.exists() and League.objects.exists():
        print("💾 Database already populated. Skipping initial boot sequence.")
        return "Skipped: Already populated"

    lock_id = "system-boot-lock"
    # 5-minute lock prevents duplicate boots
    if not acquire_lock(lock_id, expire=300):
        print("🛑 Boot sequence ignored (Already running or ran recently).")
        return "Skipped: Locked"

    print("🚀 SYSTEM STARTUP: Initiating Boot Chain...")
    
    workflow = chain(
        fetch_timezones.si(),
        fetch_countries.si(),
        fetch_available_seasons.si(),
        fetch_leagues.si(),
        fetch_teams_for_active_leagues.si(),
        fetch_upcoming_fixtures.si(days=3),
        fetch_standings_hourly.si()
    )
    workflow.apply_async()
    return "Boot sequence initiated."

@shared_task
def daily_maintenance_workflow():
    print("DAILY MAINTENANCE: Starting sequential update...")
    workflow = chain(
        fetch_countries.si(),
        fetch_available_seasons.si(),
        fetch_leagues.si(),
        fetch_teams_for_active_leagues.si(), 
        fetch_upcoming_fixtures.si(days=15)
    )
    workflow.apply_async()
    return "Daily maintenance started."

# --- OTHER TASKS ---

def update_fixture_details(fixture_id, type='lineups'):
    url = f"{BASE_URL}/fixtures/{type}"
    params = {'fixture': fixture_id}
    try:
        response = requests.get(url, headers=get_headers(), params=params)
        data = response.json().get('response', [])
        if not data: return False
        try:
            fixture = Fixture.objects.get(id=fixture_id)
        except Fixture.DoesNotExist: return False

        if type == 'lineups':
            already_had_lineups = FixtureLineup.objects.filter(fixture=fixture).exists()
            FixtureLineup.objects.update_or_create(
                fixture=fixture, defaults={'home': data[0] if len(data) > 0 else {}, 'away': data[1] if len(data) > 1 else {}}
            )
            if not already_had_lineups:
                from users.models import FanProfile, GuestFavorite
                has_followers = FanProfile.objects.filter(
                    Q(favorite_teams=fixture.home_team) | 
                    Q(favorite_teams=fixture.away_team) | 
                    Q(favorite_leagues=fixture.league) |
                    Q(favorite_fixtures=fixture)
                ).exists() or GuestFavorite.objects.filter(
                    Q(favorite_teams=fixture.home_team) | 
                    Q(favorite_teams=fixture.away_team) | 
                    Q(favorite_leagues=fixture.league) |
                    Q(favorite_fixtures=fixture)
                ).exists()

                if has_followers:
                    NotificationService.send_lineup_alert(fixture.home_team, fixture.away_team, fixture.id, fixture.league_id)
        elif type == 'statistics':
             FixtureStatistic.objects.update_or_create(fixture=fixture, defaults={'data': data})
        elif type == 'events':
            # --- NOTIFICATION-AWARE EVENTS UPDATE ---
            from notifications.models import NotificationLog
            
            old_events = fixture.events or []
            new_events = data
            
            # Save the new events to the database first
            fixture.events = new_events
            fixture.save(update_fields=['events'])
            
            # Check if this match has followers before doing expensive notification work
            from users.models import FanProfile, GuestFavorite
            has_followers = FanProfile.objects.filter(
                Q(favorite_teams=fixture.home_team) | 
                Q(favorite_teams=fixture.away_team) | 
                Q(favorite_leagues=fixture.league) |
                Q(favorite_fixtures=fixture)
            ).exists() or GuestFavorite.objects.filter(
                Q(favorite_teams=fixture.home_team) | 
                Q(favorite_teams=fixture.away_team) | 
                Q(favorite_leagues=fixture.league) |
                Q(favorite_fixtures=fixture)
            ).exists()

            def _dispatch_evt(func, *args, **kwargs):
                import sys
                if 'test' in sys.argv:
                    func(*args, **kwargs)
                else:
                    import threading
                    from django.db import connection
                    def _runner():
                        try:
                            func(*args, **kwargs)
                        except Exception as err:
                            print(f"⚠️ [Event Alert Error]: {err}")
                        finally:
                            connection.close()
                    threading.Thread(target=_runner, daemon=True).start()
            
            if has_followers and new_events:
                # Build keys for old events to detect genuinely new ones
                def _make_event_key(ev):
                    time_elapsed = ev.get('time', {}).get('elapsed') or 0
                    time_extra = ev.get('time', {}).get('extra') or 0
                    team_id = ev.get('team', {}).get('id') or 0
                    ev_type = ev.get('type') or ''
                    player_id = ev.get('player', {}).get('id') or 0
                    detail = ev.get('detail') or ''
                    return f"{time_elapsed}_{time_extra}_{team_id}_{ev_type}_{player_id}_{detail}"
                
                old_event_keys = {_make_event_key(ev) for ev in (old_events or [])}
                evt_lookback = timezone.now() - timedelta(hours=4)
                
                for ev in new_events:
                    ev_key = _make_event_key(ev)
                    if ev_key not in old_event_keys:
                        elapsed = ev.get('time', {}).get('elapsed') or 0
                        ev_type = ev.get('type')
                        team_name = ev.get('team', {}).get('name') or ''
                        team_id = ev.get('team', {}).get('id') or ''
                        detail = ev.get('detail') or ''
                        player_name = ev.get('player', {}).get('name') or ''
                        assist_name = ev.get('assist', {}).get('name') or ''
                        
                        if ev_type == 'Card':
                            already_sent = NotificationLog.objects.filter(
                                data__match_id=str(fixture.id),
                                data__player_name=player_name,
                                data__card_type=detail,
                                event_type='CARD',
                                created_at__gte=evt_lookback
                            ).exists()
                            if not already_sent:
                                _dispatch_evt(
                                    NotificationService.send_card_alert,
                                    player_name=player_name,
                                    card_type=detail,
                                    team_name=team_name,
                                    team_id=team_id,
                                    match_id=fixture.id,
                                    elapsed_time=elapsed,
                                    league_id=fixture.league_id
                                )
                                
                        elif ev_type == 'subst':
                            already_sent = NotificationLog.objects.filter(
                                data__match_id=str(fixture.id),
                                data__player_in=player_name,
                                data__player_out=assist_name,
                                event_type='SUBSTITUTION',
                                created_at__gte=evt_lookback
                            ).exists()
                            if not already_sent:
                                _dispatch_evt(
                                    NotificationService.send_substitution_alert,
                                    player_in=player_name,
                                    player_out=assist_name,
                                    team_name=team_name,
                                    team_id=team_id,
                                    match_id=fixture.id,
                                    elapsed_time=elapsed,
                                    league_id=fixture.league_id
                                )
                                
                        elif ev_type == 'Var':
                            already_sent = NotificationLog.objects.filter(
                                data__match_id=str(fixture.id),
                                data__detail=detail,
                                data__elapsed=str(elapsed),
                                event_type='VAR',
                                created_at__gte=evt_lookback
                            ).exists()
                            if not already_sent:
                                _dispatch_evt(
                                    NotificationService.send_var_alert,
                                    detail=detail,
                                    team_name=team_name,
                                    team_id=team_id,
                                    match_id=fixture.id,
                                    elapsed_time=elapsed,
                                    league_id=fixture.league_id
                                )
        return True
    except Exception as e:
        print(f"Error updating {type} for {fixture_id}: {e}")
        return False

@shared_task
def fetch_timezones():
    url = f"{BASE_URL}/timezone"
    try:
        response = requests.get(url, headers=get_headers())
        data = response.json().get('response', [])
        for tz_name in data:
            Timezone.objects.get_or_create(name=tz_name)
        return f"Fetched {len(data)} timezones."
    except Exception as e:
        return f"Failed: {e}"

@shared_task
def fetch_countries():
    url = f"{BASE_URL}/countries"
    try:
        response = requests.get(url, headers=get_headers())
        data = response.json().get('response', [])
        for item in data:
            if item.get('name'):
                Country.objects.update_or_create(name=item['name'], defaults={'code': item.get('code'), 'flag': item.get('flag')})
        return f"Fetched {len(data)} countries."
    except Exception as e:
        return f"Failed: {e}"

@shared_task
def fetch_available_seasons():
    url = f"{BASE_URL}/leagues/seasons"
    try:
        response = requests.get(url, headers=get_headers())
        years = response.json().get('response', [])
        for year in years:
            Season.objects.get_or_create(year=year)
        return f"Fetched {len(years)} seasons."
    except Exception as e:
        return f"Failed: {e}"

@shared_task
def fetch_leagues():
    url = f"{BASE_URL}/leagues"
    try:
        response = requests.get(url, headers=get_headers())
        data = response.json().get('response', [])
        count = 0
        for item in data:
            l = item['league']
            c = item['country']
            seasons = item['seasons']
            if not c.get('name'): continue
            country_obj, _ = Country.objects.get_or_create(name=c['name'], defaults={'code': c.get('code'), 'flag': c.get('flag')})
            active_season = next((s for s in seasons if s['current']), seasons[-1] if seasons else None)
            if not active_season: continue
            League.objects.update_or_create(
                id=l['id'], defaults={
                    'name': l['name'],
                    'type': l['type'],
                    'logo': l['logo'],
                    'country': country_obj,
                    'season_year': active_season['year'],
                    'has_standings': active_season['coverage'].get('standings', False),
                    'region': resolve_league_region(l['id'], l['name'], c.get('name'))
                }
            )
            count += 1
        return f"Updated {count} leagues."
    except Exception as e:
        return f"Failed: {e}"

@shared_task
def fetch_standings_hourly():
    now = timezone.now()
    range_start = now - timedelta(days=1)
    range_end = now + timedelta(days=1)
    active_league_ids = Fixture.objects.filter(date__range=(range_start, range_end)).values_list('league_id', flat=True).distinct()
    leagues = League.objects.filter(has_standings=True, id__in=active_league_ids)
    if not leagues.exists(): return "No active leagues for standings update."
    count = 0
    for league in leagues:
        url = f"{BASE_URL}/standings"
        params = {'league': league.id, 'season': league.season_year}
        try:
            response = requests.get(url, headers=get_headers(), params=params)
            data = response.json().get('response', [])
            if data:
                standing_data = data[0]['league']['standings']
                season_obj, _ = Season.objects.get_or_create(year=league.season_year)
                Standing.objects.update_or_create(league=league, season=season_obj, defaults={'data': standing_data})
                count += 1
        except Exception: continue
    return f"Updated standings for {count} active leagues."

@shared_task
def fetch_fixtures_for_date(date_str):
    url = f"{BASE_URL}/fixtures"
    params = {'date': date_str}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=20)
        data = response.json().get('response', [])
        with transaction.atomic():
            for item in data:
                save_fixture_from_api(item)

        # --- RESCHEDULED & DISPLACED FIXTURES RECONCILIATION ---
        # When tournaments/leagues publish preliminary draw dates (e.g. preliminary UCL draw fixtures),
        # and later officially reschedule matches to future dates (Oct, Nov, Dec),
        # querying /fixtures?date=... returns only the actual matches playing on this date.
        # Any fixtures remaining in DB for this date that are NOT in the provider's response are re-synced
        # so their dates, rounds, and statuses are updated to their new official schedule.
        api_ids = {item['fixture']['id'] for item in data}
        db_ids = set(Fixture.objects.filter(date__date=date_str).values_list('id', flat=True))
        displaced_ids = list(db_ids - api_ids)
        if displaced_ids:
            print(f"🔄 Reconciling {len(displaced_ids)} displaced/rescheduled fixtures for date {date_str}...")
            chunk_size = 20
            for i in range(0, len(displaced_ids), chunk_size):
                if i > 0:
                    time.sleep(0.3)
                chunk = displaced_ids[i:i + chunk_size]
                ids_str = '-'.join(map(str, chunk))
                try:
                    c_res = requests.get(url, headers=get_headers(), params={'ids': ids_str}, timeout=15)
                    c_data = c_res.json().get('response', [])
                    returned_ids = set()
                    with transaction.atomic():
                        for item in c_data:
                            save_fixture_from_api(item)
                            returned_ids.add(item['fixture']['id'])
                    # Delete any fixtures that the provider completely removed
                    missing_in_api = set(chunk) - returned_ids
                    if missing_in_api:
                        Fixture.objects.filter(id__in=missing_in_api).delete()
                except Exception as c_err:
                    print(f"⚠️ Error re-syncing displaced fixtures chunk {chunk}: {c_err}")

        print(f"✅ Synced {len(data)} fixtures for date: {date_str}")
        return len(data)
    except Exception as e:
        print(f"❌ Error fetching schedule for date {date_str}: {e}")
        return 0

@shared_task
def reconcile_stuck_suspended_fixtures():
    """
    Periodically checks fixtures stuck in SUSP or INT status to verify
    whether they were officially abandoned (ABD), postponed (PST), cancelled (CANC),
    rescheduled, or completed (FT/AET/PEN).
    """
    lock_id = "task-lock-reconcile-suspended"
    if not acquire_lock(lock_id, expire=60):
        return "Skipped: Locked"

    try:
        cutoff = timezone.now() - timedelta(minutes=15)
        stuck_fixtures = list(Fixture.objects.filter(
            status_short__in=['SUSP', 'INT'],
            updated_at__lte=cutoff
        ).values_list('id', flat=True))

        if not stuck_fixtures:
            return "No stuck suspended fixtures."

        print(f"🔄 Reconciling {len(stuck_fixtures)} suspended/interrupted fixtures...")
        url = f"{BASE_URL}/fixtures"
        chunk_size = 20
        updated_count = 0

        for i in range(0, len(stuck_fixtures), chunk_size):
            if i > 0:
                time.sleep(0.5)
            chunk = stuck_fixtures[i:i + chunk_size]
            ids_str = '-'.join(map(str, chunk))
            try:
                res = requests.get(url, headers=get_headers(), params={'ids': ids_str}, timeout=15)
                res_data = res.json().get('response', [])
                with transaction.atomic():
                    for item in res_data:
                        save_fixture_from_api(item)
                        updated_count += 1
            except Exception as e:
                print(f"❌ Error reconciling suspended fixtures chunk {chunk}: {e}")

        # Invalidate live hash if any fixtures were updated
        if updated_count > 0:
            try:
                get_redis_client().delete("live_scores_last_hash")
            except Exception:
                pass

        return f"Reconciled {updated_count} suspended fixtures."

    except Exception as e:
        return f"Failed: {e}"
    finally:
        release_lock(lock_id)

@shared_task
def fetch_upcoming_fixtures(days=7, include_yesterday=False):
    start_date = timezone.now().date()
    if include_yesterday:
        start_date = start_date - timedelta(days=1)
    total = 0
    iterations = days + 1 if include_yesterday else days
    for i in range(iterations):
        target_date = start_date + timedelta(days=i)
        date_str = target_date.strftime('%Y-%m-%d')
        print(f"Fetching schedule for: {date_str}")
        total += fetch_fixtures_for_date(date_str)
    return f"Schedule updated. Total matches: {total}"

@shared_task
def fetch_live_statistics():
    lock_id = "task-lock-live-stats"
    if not acquire_lock(lock_id, expire=60):
        print("⏭️ SKIPPED: Previous stats update still running.")
        return "Skipped: Locked"

    try:
        live_statuses = Fixture.LIVE_STATUSES
        finished_statuses = Fixture.FINISHED_STATUSES
        cutoff_time = timezone.now() - timedelta(hours=4)
        
        # Exclude finished fixtures that already have statistics populated
        candidates = Fixture.objects.filter(
            Q(status_short__in=live_statuses) | 
            Q(status_short__in=finished_statuses, date__gte=cutoff_time, statistic__isnull=True)
        ).values_list('id', flat=True)
        
        if not candidates: 
            return "No live/recent fixtures for stats."
            
        count = 0
        for fid in candidates:
            try:
                if update_fixture_details(fid, type='statistics'): 
                    count += 1
                # Throttle requests to respect the rate limit per minute
                time.sleep(1.0)
            except Exception as e: 
                print(f"Error in statistic loop for {fid}: {e}")
                continue
        return f"Updated stats for {count} fixtures."
    finally:
        release_lock(lock_id)

@shared_task
def fetch_lineups_near_kickoff():
    lock_id = "task-lock-kickoff-lineups"
    if not acquire_lock(lock_id, expire=300):
        print("⏭️ SKIPPED: Previous lineup update still running.")
        return "Skipped: Locked"

    try:
        now = timezone.now()
        cutoff_future = now + timedelta(minutes=45)
        cutoff_past = now - timedelta(hours=4) 
        live_statuses = Fixture.LIVE_STATUSES
        finished_statuses = Fixture.FINISHED_STATUSES
        
        # Only select fixtures that do NOT have a lineup record saved yet (lineup__isnull=True)
        candidates = Fixture.objects.filter(
            Q(status_short='NS', date__lte=cutoff_future, date__gte=now) | 
            Q(status_short__in=live_statuses) | 
            Q(status_short__in=finished_statuses, date__gte=cutoff_past)
        ).filter(lineup__isnull=True).values_list('id', flat=True)
        
        if not candidates: 
            return "No lineups to fetch."
            
        count = 0
        for fid in candidates:
            try:
                if update_fixture_details(fid, type='lineups'): 
                    count += 1
                # Throttle requests to respect the rate limit per minute
                time.sleep(1.0)
            except Exception as e: 
                print(f"Error in lineup loop for {fid}: {e}")
                continue
        return f"Updated lineups for {count} fixtures."
    finally:
        release_lock(lock_id)

@shared_task
def fetch_live_events():
    """
    Periodically polls /fixtures/events for all live matches that have followers.
    Uses the upgraded update_fixture_details(type='events') which now detects
    new events (cards, substitutions, VAR) and dispatches push notifications.
    
    Protected by DIRECT REDIS LOCK to prevent overlapping runs.
    """
    lock_id = "task-lock-live-events"
    if not acquire_lock(lock_id, expire=40):
        print("⏭️ SKIPPED: Previous live events update still running.")
        return "Skipped: Locked"

    try:
        live_statuses = Fixture.LIVE_STATUSES
        
        # Only fetch events for live matches that have followers
        from users.models import FanProfile, GuestFavorite
        
        # Collect IDs of teams, leagues, and fixtures that anyone follows
        followed_team_ids = set(
            FanProfile.objects.filter(favorite_teams__isnull=False)
            .values_list('favorite_teams__id', flat=True)
        ) | set(
            GuestFavorite.objects.filter(favorite_teams__isnull=False)
            .values_list('favorite_teams__id', flat=True)
        )
        
        followed_league_ids = set(
            FanProfile.objects.filter(favorite_leagues__isnull=False)
            .values_list('favorite_leagues__id', flat=True)
        ) | set(
            GuestFavorite.objects.filter(favorite_leagues__isnull=False)
            .values_list('favorite_leagues__id', flat=True)
        )
        
        followed_fixture_ids = set(
            FanProfile.objects.filter(favorite_fixtures__isnull=False)
            .values_list('favorite_fixtures__id', flat=True)
        ) | set(
            GuestFavorite.objects.filter(favorite_fixtures__isnull=False)
            .values_list('favorite_fixtures__id', flat=True)
        )
        
        # Find live fixtures that have at least one follower connection
        candidates = Fixture.objects.filter(
            status_short__in=live_statuses
        ).filter(
            Q(home_team_id__in=followed_team_ids) |
            Q(away_team_id__in=followed_team_ids) |
            Q(league_id__in=followed_league_ids) |
            Q(id__in=followed_fixture_ids)
        ).only('id', 'updated_at').distinct()
        
        if not candidates:
            return "No live fixtures with followers for events."
        
        # Skip fixtures whose events were polled recently (< 45s ago) via Redis tracking.
        # This decouples event polling from Fixture.updated_at, which is modified every 15s by update_live_fixtures.
        r = get_redis_client()
        candidate_ids = []
        for f in candidates:
            poll_key = f"events_last_poll:{f.id}"
            try:
                if r and r.get(poll_key):
                    continue
            except Exception:
                pass
            candidate_ids.append(f.id)
        
        if not candidate_ids:
            return "All live fixtures events are fresh."
            
        print(f"📢 LIVE EVENTS: Polling events for {len(candidate_ids)} live fixtures with followers.")
        count = 0
        for fid in candidate_ids:
            try:
                if update_fixture_details(fid, type='events'):
                    count += 1
                try:
                    if r:
                        r.set(f"events_last_poll:{fid}", "1", ex=45)
                except Exception:
                    pass
                # Throttle requests to respect the rate limit per minute
                time.sleep(1.0)
            except Exception as e:
                print(f"Error in live events loop for {fid}: {e}")
                continue
        return f"Updated events for {count} fixtures."
    except Exception as e:
        print(f"Failed fetch_live_events: {e}")
        return f"Failed: {e}"
    finally:
        release_lock(lock_id)

@shared_task
def warmup_upcoming_h2h():
    print("🗓️ DAILY TASK: Warming up H2H for upcoming matches...")
    now = timezone.now()
    next_48h = now + timedelta(hours=48)
    upcoming_fixtures = Fixture.objects.filter(date__range=(now, next_48h)).select_related('home_team', 'away_team')
    processed_pairs = set()
    count = 0
    for fixture in upcoming_fixtures:
        t1 = fixture.home_team
        t2 = fixture.away_team
        if t1.id < t2.id: team_a, team_b = t1, t2
        else: team_a, team_b = t2, t1
        pair_key = f"{team_a.id}-{team_b.id}"
        if pair_key in processed_pairs: continue
        try:
            h2h = HeadToHead.objects.get(team_1=team_a, team_2=team_b)
            if h2h.updated_at > now - timedelta(days=7):
                processed_pairs.add(pair_key); continue
        except HeadToHead.DoesNotExist: pass
        try:
            fetch_and_update_h2h_record(team_a, team_b)
            count += 1
            time.sleep(1.0) 
        except Exception as e: print(f"Error warming up H2H for {pair_key}: {e}")
        processed_pairs.add(pair_key)
    return f"Warmed up H2H for {count} pairs."

@shared_task(bind=True, max_retries=6, default_retry_delay=600)
def update_h2h_single_pair(self, team_id_1, team_id_2, fixture_id_to_check=None):
    try:
        t1 = Team.objects.get(id=team_id_1)
        t2 = Team.objects.get(id=team_id_2)
        if t1.id < t2.id: team_a, team_b = t1, t2
        else: team_a, team_b = t2, t1
        print(f"🏁 EVENT TRIGGER: Updating H2H for {team_a.name} vs {team_b.name} (Check ID: {fixture_id_to_check})")
        fetch_and_update_h2h_record(team_a, team_b)
        if fixture_id_to_check:
            try:
                h2h = HeadToHead.objects.get(team_1=team_a, team_2=team_b)
                found = any(item.get('fixture_id') == fixture_id_to_check for item in h2h.history)
                if not found:
                    print(f"⚠️ Match {fixture_id_to_check} missing from API history. Retrying in 10 mins...")
                    raise self.retry()
                else: print(f"✅ H2H successfully updated. Match {fixture_id_to_check} found.")
            except HeadToHead.DoesNotExist: raise self.retry()
    except self.MaxRetriesExceededError: print(f"❌ Max retries reached for {team_a.name} vs {team_b.name}. Giving up.")
    except Exception as e:
        print(f"Failed to update post-match H2H: {e}")
        if isinstance(e, requests.RequestException): raise self.retry()

def fetch_and_update_h2h_record(team_a, team_b):
    url = f"{BASE_URL}/fixtures/headtohead"
    params = {'h2h': f"{team_a.id}-{team_b.id}", 'last': 50, 'timezone': 'UTC'}
    try:
        response = requests.get(url, headers=get_headers(), params=params)
        data = response.json().get('response', [])
    except Exception as e: print(f"API Request failed: {e}"); return
    if not data: return
    data.sort(key=lambda x: x['fixture']['date'], reverse=True)
    t1_wins = 0; t2_wins = 0; draws = 0; history_list = []
    for item in data:
        f = item['fixture']; goals = item['goals']; teams = item['teams']
        home_id = teams['home']['id']; away_id = teams['away']['id']
        home_goals = goals.get('home'); away_goals = goals.get('away')
        winner_id = None
        if home_goals is not None and away_goals is not None:
            if home_goals > away_goals: winner_id = home_id
            elif away_goals > home_goals: winner_id = away_id
            else: draws += 1
            if winner_id == team_a.id: t1_wins += 1
            elif winner_id == team_b.id: t2_wins += 1
        if len(history_list) < 10:
            history_list.append({'fixture_id': f['id'], 'date': f['date'], 'home_name': teams['home']['name'], 'away_name': teams['away']['name'], 'home_logo': teams['home']['logo'], 'away_logo': teams['away']['logo'], 'score': f"{home_goals}-{away_goals}" if home_goals is not None else "vs", 'status': f['status']['short']})
    HeadToHead.objects.update_or_create(team_1=team_a, team_2=team_b, defaults={'team_1_wins': t1_wins, 'team_2_wins': t2_wins, 'draws': draws, 'total_played': len(data), 'history': history_list})

# --- NOTIFICATION TASKS (UNCHANGED) ---
@shared_task
def check_upcoming_matches_and_notify():
    from notifications.models import NotificationLog
    
    now = timezone.now()
    start_range = now - timedelta(minutes=5)
    end_range = now + timedelta(minutes=30)
    
    upcoming = Fixture.objects.filter(date__range=(start_range, end_range), status_short='NS').select_related('home_team', 'away_team', 'league')
    count = 0
    
    from users.models import FanProfile, GuestFavorite

    for match in upcoming:
        # DUPLICATE CHECK: Use match_id in JSON data to ensure we don't spam
        already_notified = NotificationLog.objects.filter(
            data__match_id=str(match.id),
            event_type='MATCH_START',
            created_at__gte=now - timedelta(hours=4)
        ).exists()
        
        if already_notified:
            continue

        home_has_fans = (
            FanProfile.objects.filter(favorite_teams__id=match.home_team.id).exists() or
            GuestFavorite.objects.filter(favorite_teams__id=match.home_team.id).exists()
        )
        away_has_fans = (
            FanProfile.objects.filter(favorite_teams__id=match.away_team.id).exists() or
            GuestFavorite.objects.filter(favorite_teams__id=match.away_team.id).exists()
        )
        league_has_fans = (
            FanProfile.objects.filter(favorite_leagues__id=match.league_id).exists() or
            GuestFavorite.objects.filter(favorite_leagues__id=match.league_id).exists()
        )
        match_has_fans = (
            FanProfile.objects.filter(favorite_fixtures__id=match.id).exists() or
            GuestFavorite.objects.filter(favorite_fixtures__id=match.id).exists()
        )

        if not home_has_fans and not away_has_fans and not league_has_fans and not match_has_fans:
            continue

        # Send Home (only if home team has followers)
        if home_has_fans:
            NotificationService.send_push_to_topic(
                topic=f"team_{match.home_team.id}", 
                title="⏳ Kickoff Soon", 
                body=f"Match starts in 15 mins: {match.home_team.name} vs {match.away_team.name}", 
                data={"type": "MATCH_START", "match_id": str(match.id), "reason": f"Following {match.home_team.name}"}, 
                event_type='MATCH_START'
            )
        
        # Send Away (only if away team has followers)
        if away_has_fans:
            NotificationService.send_push_to_topic(
                topic=f"team_{match.away_team.id}", 
                title="⏳ Kickoff Soon", 
                body=f"Match starts in 15 mins: {match.away_team.name} vs {match.home_team.name}", 
                data={"type": "MATCH_START", "match_id": str(match.id), "reason": f"Following {match.away_team.name}"}, 
                event_type='MATCH_START'
            )

        # Send to Match Bookmarked Followers
        if match_has_fans:
            NotificationService.send_push_to_topic(
                topic=f"match_{match.id}", 
                title="⏳ Kickoff Soon", 
                body=f"Match starts in 15 mins: {match.home_team.name} vs {match.away_team.name}", 
                data={"type": "MATCH_START", "match_id": str(match.id), "reason": "Saved Match"}, 
                event_type='MATCH_START'
            )
        
        if league_has_fans:
            # Send to League Followers
            NotificationService.send_push_to_topic(
                topic=f"league_{match.league_id}", 
                title="⏳ Kickoff Soon", 
                body=f"Match starts in 15 mins: {match.home_team.name} vs {match.away_team.name}", 
                data={"type": "MATCH_START", "match_id": str(match.id), "reason": f"Following {match.league.name}"}, 
                event_type='MATCH_START'
            )
        count += 1
        
    return f"Sent pre-match alerts for {count} fixtures."

@shared_task
def notify_daily_league_schedule():
    from notifications.models import NotificationLog # Local import to avoid circular dependency
    
    tomorrow = timezone.now().date() + timedelta(days=1)
    leagues_playing = Fixture.objects.filter(date__date=tomorrow).values_list('league_id', flat=True).distinct()
    
    count = 0
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for league_id in leagues_playing:
        # DUPLICATE CHECK: Has a schedule alert for this league been sent TODAY?
        topic = f"league_{league_id}"
        already_sent = NotificationLog.objects.filter(
            topic=topic,
            event_type='SCHEDULE', 
            created_at__gte=today_start
        ).exists()

        if already_sent:
            continue

        # [FOLLOWER CHECK] Skip if nobody follows this league
        from users.models import FanProfile, GuestFavorite
        if not FanProfile.objects.filter(favorite_leagues__id=league_id).exists() and not GuestFavorite.objects.filter(favorite_leagues__id=league_id).exists():
            continue

        match_count = Fixture.objects.filter(date__date=tomorrow, league_id=league_id).count()
        if match_count > 0:
            try:
                league = League.objects.get(id=league_id)
                NotificationService.send_league_daily_update(league.name, match_count, league.id)
                count += 1
            except League.DoesNotExist:
                continue
                
    return f"Sent daily schedule alerts for {count} leagues."

@shared_task
def process_scheduled_notifications():
    now = timezone.now()
    pending = ScheduledNotification.objects.filter(is_sent=False, scheduled_time__lte=now)
    count = 0
    for item in pending:
        success = NotificationService.send_push_to_topic(topic="global", title=item.title, body=item.body, data={"type": "CUSTOM"}, event_type=item.event_type)
        if success: item.is_sent = True; item.save(); count += 1
    return f"Processed {count} scheduled notifications."

@shared_task
def cleanup_stale_live_fixtures():
    """
    Finds fixtures stuck in live status for more than 4 hours.
    Fetches their current status from the API and updates the DB.
    If the API call fails or doesn't return the fixture, force updates
    the status to FT (Full Time) to prevent them from showing as live forever.
    """
    print("🧹 START: cleanup_stale_live_fixtures")
    cutoff_time = timezone.now() - timedelta(hours=4)
    live_statuses = Fixture.LIVE_STATUSES
    
    stale_fixtures = Fixture.objects.filter(
        status_short__in=live_statuses,
        date__lt=cutoff_time
    )
    
    if not stale_fixtures.exists():
        print("No stale live fixtures found.")
        return "No stale fixtures."

    stale_ids = list(stale_fixtures.values_list('id', flat=True))
    print(f"Found {len(stale_ids)} stale live fixtures: {stale_ids}")

    url = f"{BASE_URL}/fixtures"
    chunk_size = 20
    updated_count = 0
    
    for i in range(0, len(stale_ids), chunk_size):
        chunk = stale_ids[i:i + chunk_size]
        ids_str = '-'.join(map(str, chunk))
        try:
            response = requests.get(url, headers=get_headers(), params={'ids': ids_str}, timeout=10)
            api_data = response.json().get('response', [])
            api_returned_ids = set()
            
            with transaction.atomic():
                for item in api_data:
                    fixture, _ = save_fixture_from_api(item)
                    if fixture:
                        api_returned_ids.add(fixture.id)
                        updated_count += 1
            
            # For any stale fixture not returned by the API, force update status to FT ONLY if started > 3 hours ago
            not_returned_ids = set(chunk) - api_returned_ids
            if not_returned_ids:
                three_hours_ago = timezone.now() - timedelta(hours=3)
                actually_stale = Fixture.objects.filter(
                    id__in=not_returned_ids,
                    date__lte=three_hours_ago
                )
                if actually_stale.exists():
                    stale_ids_list = list(actually_stale.values_list('id', flat=True))
                    print(f"Force marking {len(stale_ids_list)} unreturned fixtures (>3h) as FT: {stale_ids_list}")
                    with transaction.atomic():
                        actually_stale.update(
                            status_short='FT',
                            status_long='Finished (Force Cleanup)',
                            elapsed=90
                        )
        except Exception as e:
            print(f"❌ Error in stale cleanup for chunk {chunk}: {e}")
            # Fallback: if API fails, force mark fixtures as FT if they started > 12 hours ago
            twelve_hours_ago = timezone.now() - timedelta(hours=12)
            very_stale = Fixture.objects.filter(
                id__in=chunk,
                date__lt=twelve_hours_ago
            )
            if very_stale.exists():
                print(f"Force marking very stale fixtures as FT: {list(very_stale.values_list('id', flat=True))}")
                very_stale.update(
                    status_short='FT',
                    status_long='Finished (Timeout)',
                    elapsed=90
                )
                
    return f"Cleaned up {updated_count} fixtures."


@shared_task
def fetch_and_update_team_squad(team_id):
    """
    Queries API-Football for the squad of a team and updates/creates the TeamSquad record.
    """
    url = f"{BASE_URL}/players/squads"
    params = {'team': team_id}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', [])
            players = []
            if response_data:
                team_data = response_data[0]
                players = team_data.get('players', [])
            squad, created = TeamSquad.objects.update_or_create(
                team_id=team_id,
                defaults={'players': players}
            )
            return players
    except Exception as e:
        print(f"Failed to fetch team squad for team {team_id}: {e}")
    return None


@shared_task
def fetch_and_update_player_profile(player_id, season):
    url = f"{BASE_URL}/players"
    params = {'id': player_id, 'season': season}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', [])
            if response_data:
                player_data = response_data[0]
                profile, created = PlayerProfile.objects.update_or_create(
                    player_id=player_id,
                    season=season,
                    defaults={'data': player_data}
                )
                return player_data
    except Exception as e:
        print(f"Failed to fetch player profile for player {player_id} (season {season}): {e}")
    return None


@shared_task
def fetch_and_update_player_stats(league_id, season_year, stat_type):
    # Mapping stats type to API-Football endpoints
    endpoint_map = {
        'topscorers': 'topscorers',
        'topassists': 'topassists',
        'topyellowcards': 'topyellowcards',
        'topredcards': 'topredcards',
    }
    endpoint = endpoint_map.get(stat_type)
    if not endpoint:
        return None

    url = f"{BASE_URL}/players/{endpoint}"
    params = {'league': league_id, 'season': season_year}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', [])
            PlayerStatList.objects.update_or_create(
                league_id=league_id,
                season_id=season_year,
                stat_type=stat_type,
                defaults={'data': response_data}
            )
            return response_data
    except Exception as e:
        print(f"Failed to fetch {stat_type} for league {league_id} (season {season_year}): {e}")
    return None


@shared_task
def fetch_and_update_team_statistics(team_id, league_id, season_year):
    url = f"{BASE_URL}/teams/statistics"
    params = {'team': team_id, 'league': league_id, 'season': season_year}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', {})
            
            # Ensure ForeignKey dependencies exist
            Season.objects.get_or_create(year=season_year)
            League.objects.get_or_create(id=league_id, defaults={'name': f'League {league_id}', 'season_year': season_year})
            Team.objects.get_or_create(id=team_id, defaults={'name': f'Team {team_id}'})

            TeamStatistic.objects.update_or_create(
                team_id=team_id,
                league_id=league_id,
                season_id=season_year,
                defaults={'data': response_data}
            )
            return response_data
    except Exception as e:
        print(f"Failed to fetch statistics for team {team_id} (league {league_id}, season {season_year}): {e}")
    return None


@shared_task
def fetch_and_update_venue_details(venue_id):
    url = f"{BASE_URL}/venues"
    params = {'id': venue_id}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', [])
            if response_data:
                venue_info = response_data[0]
                venue, created = Venue.objects.update_or_create(
                    id=venue_info.get('id'),
                    defaults={
                        'name': venue_info.get('name'),
                        'city': venue_info.get('city'),
                        'address': venue_info.get('address'),
                        'country': venue_info.get('country'),
                        'capacity': venue_info.get('capacity'),
                        'surface': venue_info.get('surface'),
                        'image': venue_info.get('image'),
                    }
                )
                return venue_info
    except Exception as e:
        print(f"Failed to fetch venue details for venue {venue_id}: {e}")
    return None


@shared_task
def fetch_and_update_team_coaches(team_id):
    url = f"{BASE_URL}/coachs"
    params = {'team': team_id}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            response_data = data.get('response', [])

            # Ensure ForeignKey dependencies exist
            Team.objects.get_or_create(id=team_id, defaults={'name': f'Team {team_id}'})

            TeamCoachList.objects.update_or_create(
                team_id=team_id,
                defaults={'data': response_data}
            )
            return response_data
    except Exception as e:
        print(f"Failed to fetch coach list for team {team_id}: {e}")
    return None


@shared_task
def fetch_and_update_team_fixtures(team_id, season_year):
    """
    Queries API-Football for the fixtures of a specific team and season, and saves them.
    """
    url = f"{BASE_URL}/fixtures"
    params = {'team': team_id, 'season': season_year}
    try:
        response = requests.get(url, headers=get_headers(), params=params, timeout=15)
        if response.status_code == 200:
            data = response.json().get('response', [])
            with transaction.atomic():
                for item in data:
                    save_fixture_from_api(item)
            return len(data)
    except Exception as e:
        print(f"Failed to fetch fixtures for team {team_id} (season {season_year}): {e}")
    return 0