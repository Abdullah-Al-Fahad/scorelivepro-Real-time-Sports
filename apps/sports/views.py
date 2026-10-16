import textwrap
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.shortcuts import get_object_or_404
from rest_framework import generics, filters, status
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny

# Swagger / Documentation Imports
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse, extend_schema_view
from drf_spectacular.types import OpenApiTypes

from users.permissions import IsOwnerOrAdmin
from users.utils import log_activity
from .tasks import fetch_and_update_h2h_record
from .models import (HeadToHead, Timezone, Season, Country, League, Team, Venue, Standing, 
                     Fixture, FixtureLineup, FixtureStatistic, FavoriteTeam, FavoriteLeague, TeamSquad, PlayerProfile, PlayerStatList,
                     TeamStatistic, TeamCoachList)
from .serializers import (HeadToHeadSerializer, TimezoneSerializer, SeasonSerializer, CountrySerializer, 
                          LeagueSerializer, TeamSerializer, TeamDetailSerializer, VenueSerializer, 
                          StandingSerializer, FixtureSerializer, 
                          FixtureLineupSerializer, FixtureStatisticSerializer,
                          FavoriteTeamSerializer, FavoriteLeagueSerializer, FavoriteIDSerializer,
                          PlayerProfileSerializer, PlayerStatListSerializer,
                          TeamStatisticSerializer, TeamCoachListSerializer)

User = get_user_model()

# =========================================================
#                    PAGINATION CONFIG
# =========================================================

class StandardPagination(PageNumberPagination):
    """
    Standard pagination for returning 100 items per page.
    """
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000

class FixturePagination(StandardPagination):
    """
    Specialized pagination for fixtures that expands page size to 5000 for live matches
    so all live fixtures fit on Page 1 without eviction, while preserving the standard
    paginated envelope ({"count": ..., "next": ..., "results": [...]}) expected by clients.
    """
    max_page_size = 5000

    def get_page_size(self, request):
        params = getattr(request, 'query_params', getattr(request, 'GET', {}))
        status_param = params.get('status')
        live_param   = params.get('live')
        if status_param == 'live' or live_param in ('true', 'all') or params.get('all') == 'true':
            return 5000
        return super().get_page_size(request)

    def get_paginated_response(self, data):
        return Response({
            'success': True,
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data
        })


# =========================================================
#                    ACTIVITY TRACKING MIXIN
# =========================================================

class ActivityLogMixin:
    """
    Mixin to automatically log when an authenticated user views a specific resource.
    """
    activity_action = "VIEW_RESOURCE"
    
    def get_activity_details(self, request, *args, **kwargs):
        return f"Viewed resource ID: {kwargs.get('pk') or kwargs.get('league_id')}"

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        if request.user and request.user.is_authenticated:
            details = self.get_activity_details(request, *args, **kwargs)
            # Fire and forget logging
            log_activity(request.user, self.activity_action, details, request=request)
        return response


# =========================================================
#                    WEBSOCKET DOCS
# =========================================================

@extend_schema(
    tags=['Fixtures'],
    summary="🔴 WebSocket: Live Score Stream",
    description=textwrap.dedent("""
        **Connect via:** `wss://<base_url>/ws/live/`
        
        Establishes a real-time WebSocket connection for live match updates.
        
        **Protocol Flow:**
        1. **Connect:** Client establishes connection.
        2. **Initial State:** Server immediately sends the full list of currently live matches.
        3. **Updates:** Server pushes updates (JSON) whenever scores, time, or status change.
    """),
    request=None,
    responses={
        101: OpenApiResponse(description="Switching Protocols (Connection Accepted)"),
        426: OpenApiResponse(description="Upgrade Required (Use a WebSocket Client)")
    }
)
class FixtureWebSocketDocsView(APIView):
    permission_classes = [IsOwnerOrAdmin] 

    def get(self, request, *args, **kwargs):
        return Response(
            {"detail": "Please connect via WebSocket (ws://...)"}, 
            status=status.HTTP_426_UPGRADE_REQUIRED
        )


# =========================================================
#                    CORE RESOURCES
# =========================================================

@extend_schema(tags=['Core Resources'], summary="List Timezones")
class TimezoneListView(generics.ListAPIView):
    queryset = Timezone.objects.all()
    serializer_class = TimezoneSerializer
    @method_decorator(cache_page(60 * 60 * 24 * 7))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

@extend_schema(tags=['Core Resources'], summary="List Countries")
class CountryListView(generics.ListAPIView):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer
    search_fields = ['name', 'code']
    @method_decorator(cache_page(60 * 60 * 24))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

@extend_schema(tags=['Core Resources'], summary="List Available Seasons")
class SeasonListView(generics.ListAPIView):
    queryset = Season.objects.all()
    serializer_class = SeasonSerializer
    ordering = ['-year']
    @method_decorator(cache_page(60 * 60 * 24))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

@extend_schema(
    tags=['Core Resources'],
    summary="List or Get Venue Details",
    parameters=[
        OpenApiParameter(name='id', description='Venue ID to get details for', required=False, type=int)
    ]
)
class VenueListView(generics.ListAPIView):
    queryset = Venue.objects.all()
    serializer_class = VenueSerializer
    search_fields = ['name', 'city']

    def dispatch(self, request, *args, **kwargs):
        if 'id' in request.GET:
            # Skip cache_page for single venue requests to handle DB-based TTL checking dynamically
            return super().dispatch(request, *args, **kwargs)
        return cache_page(60 * 60 * 24)(super().dispatch)(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        venue_id = request.query_params.get('id')
        if venue_id:
            try:
                venue_id = int(venue_id)
            except ValueError:
                return Response(
                    {"error": "Invalid id parameter. Must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            from datetime import timedelta
            from django.utils import timezone
            need_fetch = False
            try:
                venue = Venue.objects.get(id=venue_id)
                # If fields like address or country are null, it might be a placeholder, so update/fetch the full info!
                if not venue.address or venue.updated_at < timezone.now() - timedelta(days=1):
                    need_fetch = True
            except Venue.DoesNotExist:
                need_fetch = True
                venue = None

            if need_fetch:
                from .tasks import fetch_and_update_venue_details
                data = fetch_and_update_venue_details(venue_id)
                if data is None and venue is None:
                    return Response(
                        {"error": "Venue details not found or failed to retrieve."},
                        status=status.HTTP_404_NOT_FOUND
                    )
                try:
                    venue = Venue.objects.get(id=venue_id)
                except Venue.DoesNotExist:
                    return Response(
                        {"error": "Venue details not found or failed to retrieve."},
                        status=status.HTTP_404_NOT_FOUND
                    )

            serializer = self.get_serializer(venue)
            return Response(serializer.data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)


# =========================================================
#                    LEAGUES & TEAMS
# =========================================================

@extend_schema(tags=['Leagues'], summary="List Leagues", description="Searchable list of leagues.")
class LeagueListView(generics.ListAPIView):
    serializer_class = LeagueSerializer
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['country__name', 'type', 'has_standings']

    def get_queryset(self):
        from django.db.models import Case, When, Value, IntegerField, Q
        
        user_country = self.request.query_params.get('user_country')
        country_param = self.request.query_params.get('country')
        search_param = self.request.query_params.get('search')
        
        queryset = League.objects.select_related('country').all()
        
        if country_param:
            queryset = queryset.filter(country__name__iexact=country_param)
            
        if search_param:
            search_clean = search_param.strip()
            queryset = queryset.filter(
                Q(name__unaccent__icontains=search_clean) | Q(country__name__unaccent__icontains=search_clean)
            )

        when_clauses = []
        if user_country:
            when_clauses.append(When(country__name__iexact=user_country, then=Value(4000)))
            
        # UCL (ID 2), World Cup (ID 1), Euro (ID 4) (Tier 2)
        when_clauses.append(When(
            Q(id__in=[2, 1, 4]) | Q(name__iexact="UEFA Champions League") | Q(name__iexact="World Cup") | Q(name__iexact="UEFA Euro"),
            then=Value(2000)
        ))
        
        # UEL, Conference League, Nations League, and other continental cups (Tier 3)
        when_clauses.append(When(
            Q(id__in=[3, 848, 5, 12, 16, 17, 18, 13]) |
            Q(name__in=[
                "Europa League", "UEFA Europa League",
                "Conference League", "UEFA Europa Conference League",
                "Nations League", "UEFA Nations League",
                "CAF Champions League", "AFC Champions League Elite",
                "CONCACAF Champions Cup", "CONCACAF Champions League",
                "Copa Libertadores"
            ]),
            then=Value(1800)
        ))
        
        # Top 5 European Leagues (Tier 4)
        when_clauses.append(When(
            (Q(name__icontains="Premier League") & Q(country__name__iexact="England")) |
            (Q(name__icontains="La Liga") & Q(country__name__iexact="Spain")) |
            (Q(name__icontains="Primera División") & Q(country__name__iexact="Spain")) |
            (Q(name__icontains="Serie A") & Q(country__name__iexact="Italy")) |
            (Q(name__icontains="Bundesliga") & Q(country__name__iexact="Germany")) |
            (Q(name__icontains="Ligue 1") & Q(country__name__iexact="France")),
            then=Value(1500)
        ))
        
        # Secondary Major Leagues (Tier 5)
        when_clauses.append(When(
            (Q(name__icontains="Primeira Liga") & Q(country__name__iexact="Portugal")) |
            (Q(name__icontains="Eredivisie") & Q(country__name__iexact="Netherlands")) |
            (Q(name__icontains="Süper Lig") & Q(country__name__iexact="Turkey")) |
            (Q(name__icontains="Serie A") & Q(country__name__iexact="Brazil")) |
            (Q(name__icontains="Série A") & Q(country__name__iexact="Brazil")) |
            (Q(name__icontains="Major League Soccer") & Q(country__name__iexact="USA")) |
            (Q(name__icontains="MLS") & Q(country__name__iexact="USA")) |
            Q(name__icontains="Libertadores"),
            then=Value(1000)
        ))
        
        queryset = queryset.annotate(
            priority_score=Case(
                *when_clauses,
                default=Value(100),
                output_field=IntegerField()
            )
        )
        
        return queryset.order_by('-priority_score', 'country__name', 'name')
    
    def dispatch(self, request, *args, **kwargs):
        # Only use cache if no search, filtering, pagination or custom parameters are provided
        has_params = any(p in request.GET for p in ['search', 'user_country', 'country', 'page', 'page_size', 'country__name', 'type', 'has_standings'])
        if has_params:
            return super().dispatch(request, *args, **kwargs)
        return cache_page(60 * 60)(super().dispatch)(request, *args, **kwargs)

@extend_schema(tags=['Leagues'], summary="Get League Details")
class LeagueDetailView(generics.RetrieveAPIView):
    queryset = League.objects.select_related('country').all()
    serializer_class = LeagueSerializer
    @method_decorator(cache_page(60 * 60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

import django_filters

class TeamFilter(django_filters.FilterSet):
    country = django_filters.CharFilter(field_name='country', lookup_expr='iexact')

    class Meta:
        model = Team
        fields = ['country', 'leagues', 'is_popular']

@extend_schema(
    tags=['Teams'], 
    summary="List Teams", 
    description="Search teams or filter by country and league. Example: `?leagues=39` (Premier League)",
    parameters=[
        OpenApiParameter(name='leagues', description='Filter by League ID', required=False, type=int),
    ]
)
class TeamListView(generics.ListAPIView):
    serializer_class = TeamSerializer
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = TeamFilter
    ordering_fields = ['name', 'country']
    ordering = ['name']
    
    def get_queryset(self):
        from django.db.models import Q
        queryset = Team.objects.all()
        search_query = self.request.query_params.get('search')
        if search_query:
            search_clean = search_query.strip()
            queryset = queryset.filter(
                Q(name__unaccent__icontains=search_clean) | Q(country__unaccent__icontains=search_clean)
            )
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Build country map to prevent N+1 queries in serializer country lookup
        countries_map = {c.name.lower(): c for c in Country.objects.all()}
        context['countries_map'] = countries_map
        return context

    def dispatch(self, request, *args, **kwargs):
        # Only use cache if no search, filtering, or custom parameters are provided
        has_params = any(p in request.GET for p in ['search', 'country', 'leagues', 'is_popular', 'page', 'page_size'])
        if has_params:
            return super().dispatch(request, *args, **kwargs)
        return cache_page(60 * 60)(super().dispatch)(request, *args, **kwargs)

@extend_schema(
    tags=['Teams'],
    summary="List Teams Grouped by Country",
    description="Returns all teams in the system grouped by country."
)
class TeamCountryGroupedView(APIView):
    def get(self, request, *args, **kwargs):
        from collections import defaultdict
        from django.db.models import Q
        
        search_query = request.query_params.get('search')
        country_query = request.query_params.get('country')
        
        if country_query == 'All' or country_query == '':
            country_query = None
            
        use_cache = not search_query and not country_query
        
        if use_cache:
            from django.core.cache import cache
            cached_data = cache.get('team_grouped_all')
            if cached_data:
                return Response(cached_data, status=status.HTTP_200_OK)

        # Fetch countries for code and flag mapping
        country_map = {c.name.lower(): c for c in Country.objects.all()}
        
        teams = Team.objects.all().only('id', 'name', 'logo', 'country')
        if search_query:
            teams = teams.filter(Q(name__icontains=search_query) | Q(country__icontains=search_query))
        if country_query:
            teams = teams.filter(country__iexact=country_query)
            
        teams = teams.order_by('country', 'name')
        
        grouped = defaultdict(list)
        for team in teams:
            country_name = team.country or "International"
            c_obj = country_map.get(country_name.lower())
            
            grouped[country_name].append({
                "id": team.id,
                "name": team.name,
                "logo": team.logo,
                "country": {
                    "name": country_name,
                    "code": c_obj.code if c_obj else "",
                    "flag": c_obj.flag if c_obj else ""
                }
            })
            
        response_data = []
        for country_name in sorted(grouped.keys()):
            c_obj = country_map.get(country_name.lower())
            response_data.append({
                "country": country_name,
                "flag": c_obj.flag if c_obj else None,
                "teams": grouped[country_name]
            })
            
        if use_cache:
            from django.core.cache import cache
            cache.set('team_grouped_all', response_data, 60 * 60 * 24)
            
        return Response(response_data, status=status.HTTP_200_OK)

@extend_schema(tags=['Teams'], summary="Get Team Details")
class TeamDetailView(generics.RetrieveAPIView):
    queryset = Team.objects.all()
    serializer_class = TeamDetailSerializer
    @method_decorator(cache_page(60 * 60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

    def get_object(self):
        obj = super().get_object()
        from django.utils import timezone
        from datetime import timedelta
        
        need_fetch = False
        try:
            squad = obj.squad
            if squad.updated_at < timezone.now() - timedelta(hours=24):
                need_fetch = True
        except TeamSquad.DoesNotExist:
            need_fetch = True
            
        if need_fetch:
            from .tasks import fetch_and_update_team_squad
            fetch_and_update_team_squad(obj.id)
            obj.refresh_from_db()
            
        return obj


# =========================================================
#                    STANDINGS
# =========================================================

@extend_schema(tags=['Standings'], summary="List Standings")
class StandingListView(generics.ListAPIView):
    serializer_class = StandingSerializer
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['league', 'season']
    search_fields = ['league__name', 'league__country__name']
    ordering_fields = ['season', 'league__name']
    ordering = ['-season', 'league__name'] 

    def get_queryset(self): 
        return Standing.objects.select_related('league', 'season').all()

    @method_decorator(cache_page(60 * 60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

@extend_schema(tags=['Standings'], summary="Get Standing Details")
class StandingDetailView(generics.RetrieveAPIView):
    serializer_class = StandingSerializer
    def get_object(self):
        return get_object_or_404(
            Standing.objects.select_related('league', 'season'),
            league_id=self.kwargs.get('league_id'),
            season_id=self.kwargs.get('season_year')
        )
    @method_decorator(cache_page(60 * 60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)


# =========================================================
#                    FIXTURES
# =========================================================
from django.utils import timezone
from datetime import timedelta
@extend_schema(
    tags=['Fixtures'], 
    summary="List Fixtures",
    description="Retrieve fixtures filtered by status, league, and date. Pagination enabled (100 per page).",
    parameters=[
        OpenApiParameter(
            name='status', 
            description='Filter by match status group: "live", "finished", "upcoming"', 
            required=False, 
            type=str,
            enum=['live', 'finished', 'upcoming']
        ),
        OpenApiParameter(
            name='date',
            description='Filter fixtures by date (format: YYYY-MM-DD, e.g. "2026-08-30")',
            required=False,
            type=str,
        ),
    ]
)
class FixtureListView(generics.ListAPIView):
    serializer_class = FixtureSerializer
    pagination_class = FixturePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
 
    filterset_fields = ['league', 'season']
    search_fields = ['home_team__name', 'away_team__name', 'league__name']
 
    LIVE_STATUSES      = Fixture.LIVE_STATUSES
    FINISHED_STATUSES  = Fixture.FINISHED_STATUSES
    UPCOMING_STATUSES  = Fixture.UPCOMING_STATUSES

    def paginate_queryset(self, queryset):
        # Support optional unpaginated flat list only when explicitly requested
        if self.request.query_params.get('flat') == 'true' or self.request.query_params.get('raw') == 'true':
            return None
        return super().paginate_queryset(queryset)
 
    def get_queryset(self):
        from django.db.models import Case, When, Value, IntegerField, Q
        from django.core.cache import cache
        
        queryset = Fixture.objects.select_related(
            'league', 'league__country', 'season', 'home_team', 'away_team', 'venue', 'lineup', 'statistic'
        ).all()

        team_param = self.request.query_params.get('team')
        year_param = self.request.query_params.get('year')

        if team_param:
            target_year = year_param or 2026
            local_exists = Fixture.objects.filter(
                Q(home_team_id=team_param) | Q(away_team_id=team_param),
                season_id=target_year
            ).exists()
            if not local_exists:
                sync_team_key = f"sync_team_fixtures:{team_param}:{target_year}"
                if not cache.get(sync_team_key):
                    cache.set(sync_team_key, True, timeout=120)
                    from .tasks import fetch_and_update_team_fixtures
                    try:
                        fetch_and_update_team_fixtures.delay(team_param, target_year)
                    except Exception:
                        pass

            queryset = queryset.filter(Q(home_team_id=team_param) | Q(away_team_id=team_param))

        if year_param:
            queryset = queryset.filter(season_id=year_param)

        # Date-wise filtering (e.g. ?date=2026-08-30)
        date_param = self.request.query_params.get('date')
        parsed_date = None
        if date_param:
            try:
                clean_date = date_param.strip()
                from datetime import datetime as dt, time, timezone as dt_timezone
                if 'T' in clean_date:
                    clean_date = clean_date.split('T')[0]

                parsed_date = dt.strptime(clean_date, "%Y-%m-%d").date()
                date_str = parsed_date.strftime("%Y-%m-%d")

                # Support client timezone offset (via query param ?tz= or header X-Timezone-Offset)
                tz_offset_hours = 0
                tz_param = self.request.query_params.get('timezone_offset') or self.request.query_params.get('tz') or self.request.headers.get('X-Timezone-Offset')
                if tz_param:
                    try:
                        clean_tz = str(tz_param).replace('+', '').split(':')[0].strip()
                        tz_offset_hours = int(clean_tz)
                    except (ValueError, TypeError):
                        pass

                start_dt = dt.combine(parsed_date, time.min).replace(tzinfo=dt_timezone.utc) - timedelta(hours=tz_offset_hours)
                end_dt = dt.combine(parsed_date, time.max).replace(tzinfo=dt_timezone.utc) - timedelta(hours=tz_offset_hours)

                # Check if fixtures for this date exist locally; if not, trigger async sync without blocking HTTP response
                local_fixtures = Fixture.objects.filter(date__gte=start_dt, date__lte=end_dt)
                need_sync = False
                if not local_fixtures.exists():
                    need_sync = True
                elif parsed_date <= timezone.now().date():
                    stuck_count = local_fixtures.filter(status_short='NS', date__lte=timezone.now()).count()
                    if stuck_count > 0:
                        need_sync = True

                if need_sync:
                    sync_date_key = f"sync_fixtures_date:{date_str}"
                    if not cache.get(sync_date_key):
                        cache.set(sync_date_key, True, timeout=120)
                        from .tasks import fetch_fixtures_for_date
                        try:
                            fetch_fixtures_for_date.delay(date_str)
                        except Exception:
                            pass

                queryset = queryset.filter(date__gte=start_dt, date__lte=end_dt)
            except (ValueError, TypeError):
                pass  # Silently ignore malformed date strings

        status_param = self.request.query_params.get('status')
        live_param   = self.request.query_params.get('live')
        
        # Priority-aware Sorting Logic
        user_country = self.request.query_params.get('user_country')
        country_code = self.request.query_params.get('country_code')
        
        when_clauses = []
        # Tier 0 (Score: 10000): Active LIVE matches
        when_clauses.append(When(status_short__in=self.LIVE_STATUSES, then=Value(10000)))
        
        # Tier 1 (Score: 4000): Matches where league.country == user_country
        if user_country:
            when_clauses.append(When(league__country__name__iexact=user_country, then=Value(4000)))
        if country_code:
            when_clauses.append(When(league__country__code__iexact=country_code, then=Value(4000)))
            
        # Tier 2 (Score: 2000): UCL (ID 2), FIFA World Cup (ID 1), UEFA Euro (ID 4)
        when_clauses.append(When(
            Q(league_id__in=[2, 1, 4]) | Q(league__name__iexact="UEFA Champions League") | Q(league__name__iexact="World Cup") | Q(league__name__iexact="UEFA Euro"),
            then=Value(2000)
        ))
        
        # Tier 3 (Score: 1800): UEL, Conference League, Nations League, and other continental tournaments
        when_clauses.append(When(
            Q(league_id__in=[3, 848, 5, 12, 16, 17, 18, 13]) |
            Q(league__name__in=[
                "Europa League", "UEFA Europa League",
                "Conference League", "UEFA Europa Conference League",
                "Nations League", "UEFA Nations League",
                "CAF Champions League", "AFC Champions League Elite",
                "CONCACAF Champions Cup", "CONCACAF Champions League",
                "Copa Libertadores"
            ]),
            then=Value(1800)
        ))
        
        # Tier 4 (Score: 1500): Top 5 European Leagues
        when_clauses.append(When(
            (Q(league__name__icontains="Premier League") & Q(league__country__name__iexact="England")) |
            (Q(league__name__icontains="La Liga") & Q(league__country__name__iexact="Spain")) |
            (Q(league__name__icontains="Primera División") & Q(league__country__name__iexact="Spain")) |
            (Q(league__name__icontains="Serie A") & Q(league__country__name__iexact="Italy")) |
            (Q(league__name__icontains="Bundesliga") & Q(league__country__name__iexact="Germany")) |
            (Q(league__name__icontains="Ligue 1") & Q(league__country__name__iexact="France")),
            then=Value(1500)
        ))
        
        # Tier 5 (Score: 1000): Secondary Major Leagues
        when_clauses.append(When(
            (Q(league__name__icontains="Primeira Liga") & Q(league__country__name__iexact="Portugal")) |
            (Q(league__name__icontains="Eredivisie") & Q(league__country__name__iexact="Netherlands")) |
            (Q(league__name__icontains="Süper Lig") & Q(league__country__name__iexact="Turkey")) |
            (Q(league__name__icontains="Serie A") & Q(league__country__name__iexact="Brazil")) |
            (Q(league__name__icontains="Série A") & Q(league__country__name__iexact="Brazil")) |
            (Q(league__name__icontains="Major League Soccer") & Q(league__country__name__iexact="USA")) |
            (Q(league__name__icontains="MLS") & Q(league__country__name__iexact="USA")),
            then=Value(1000)
        ))
        
        queryset = queryset.annotate(
            priority_score=Case(
                *when_clauses,
                default=Value(100),
                output_field=IntegerField()
            )
        )
 
        if status_param == 'live' or live_param == 'true':
            queryset = queryset.filter(
                status_short__in=self.LIVE_STATUSES
            ).order_by('-priority_score', 'league__country__name', 'date')
 
        elif status_param == 'finished':
            queryset = queryset.filter(
                status_short__in=self.FINISHED_STATUSES
            ).order_by('-priority_score', 'league__country__name', '-date')
 
        elif status_param == 'upcoming':
            if parsed_date:
                # Date param already applied above, just filter by upcoming statuses
                queryset = queryset.filter(
                    status_short__in=self.UPCOMING_STATUSES,
                ).order_by('-priority_score', 'league__country__name', 'date')
            else:
                now = timezone.now()
                days_param = self.request.query_params.get('days')
                try:
                    days_ahead = int(days_param) if days_param else 1
                except (ValueError, TypeError):
                    days_ahead = 1

                # Default: Cap to current 24-hour window (or days parameter) to prevent multi-week upcoming fixture leakage
                max_upcoming = now + timedelta(days=days_ahead)
                queryset = queryset.filter(
                    status_short__in=self.UPCOMING_STATUSES,
                    date__gte=now - timedelta(hours=2),
                    date__lte=max_upcoming,
                ).order_by('-priority_score', 'league__country__name', 'date')
 
        else:
            queryset = queryset.order_by('-priority_score', 'league__country__name', 'date')
 
        return queryset

    def list(self, request, *args, **kwargs):
        if request.query_params.get('grouped') == 'true':
            return FixtureGroupedListView().get_grouped_response(request, queryset=self.get_queryset())
        return super().list(request, *args, **kwargs)


@extend_schema(
    tags=['Fixtures'],
    summary="List Fixtures Grouped by League",
    description="Returns fixtures pre-grouped strictly by canonical league_id.",
    parameters=[
        OpenApiParameter(name='date', description='Date in YYYY-MM-DD format (e.g. 2026-09-10)', required=False, type=str),
        OpenApiParameter(name='status', description='Filter by match status group: "live", "finished", "upcoming"', required=False, type=str),
        OpenApiParameter(name='live', description='Set to "true" to filter currently live matches', required=False, type=str),
        OpenApiParameter(name='user_country', description='User local country for dynamic priority ranking', required=False, type=str),
        OpenApiParameter(name='tz', description='Client timezone offset in hours', required=False, type=str),
    ]
)
class FixtureGroupedListView(APIView):
    permission_classes = [AllowAny]

    def get_grouped_response(self, request, queryset=None):
        from django.core.cache import cache
        from collections import OrderedDict
        from .serializers import LeagueSerializer, FixtureSerializer
        from .models import Country
        from django.utils import timezone
        from datetime import datetime as dt, time, timezone as dt_timezone

        # Build a distinct cache key based on query parameters
        date_param = str(request.query_params.get('date', '')).strip()
        status_param = str(request.query_params.get('status', '')).strip()
        live_param = str(request.query_params.get('live', '')).strip()
        team_param = str(request.query_params.get('team', '')).strip()
        league_param = str(request.query_params.get('league', '')).strip()
        user_country = str(request.query_params.get('user_country', '')).strip()
        tz_param = str(request.query_params.get('tz') or request.query_params.get('timezone_offset') or '').strip()

        cache_key = f"grouped_fixtures:{date_param}:{status_param}:{live_param}:{team_param}:{league_param}:{user_country}:{tz_param}"

        if queryset is None:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                return Response(cached_data, status=status.HTTP_200_OK)

            view = FixtureListView()
            view.request = request
            view.format_kwarg = None
            queryset = view.get_queryset()

            # Prevent unbounded multi-year table scan if no date or specific filter was provided
            if not date_param and not status_param and live_param != 'true' and not team_param and not league_param:
                today = timezone.now().date()
                start_dt = dt.combine(today, time.min).replace(tzinfo=dt_timezone.utc)
                end_dt = dt.combine(today, time.max).replace(tzinfo=dt_timezone.utc)
                queryset = queryset.filter(date__gte=start_dt, date__lte=end_dt)

        countries_map = {c.name.lower(): c for c in Country.objects.all()}
        context = {'request': request, 'countries_map': countries_map}
        grouped_dict = OrderedDict()

        for fixture in queryset:
            lid = fixture.league_id
            if lid not in grouped_dict:
                grouped_dict[lid] = {
                    'league': LeagueSerializer(fixture.league, context=context).data,
                    'count': 0,
                    'fixtures': []
                }
            grouped_dict[lid]['count'] += 1
            grouped_dict[lid]['fixtures'].append(FixtureSerializer(fixture, context=context).data)

        results = list(grouped_dict.values())
        total_fixtures = sum(g['count'] for g in results)

        response_data = {
            "success": True,
            "count": len(results),
            "total_fixtures": total_fixtures,
            "results": results
        }

        # Cache for 20 seconds to absorb spikes from 10,000 mobile app users while keeping live scores fresh
        cache.set(cache_key, response_data, timeout=20)

        return Response(response_data, status=status.HTTP_200_OK)

    def get(self, request, *args, **kwargs):
        return self.get_grouped_response(request)

@extend_schema(tags=['Fixtures'], summary="Get Fixture Details")
class FixtureDetailView(ActivityLogMixin, generics.RetrieveAPIView):
    queryset = Fixture.objects.select_related(
        'league', 'league__country', 'season', 'home_team', 'away_team', 'venue'
    ).all()
    serializer_class = FixtureSerializer
    activity_action = "VIEW_FIXTURE_DETAILS"
    def get_activity_details(self, request, *args, **kwargs):
        return f"Viewed match overview for Fixture {kwargs.get('pk')}"

    def get_object(self):
        fixture = super().get_object()
        if fixture.status_short not in ['NS', 'TBD'] and not fixture.events:
            from django.utils import timezone
            from datetime import timedelta
            if timezone.now() - fixture.updated_at > timedelta(minutes=5):
                from .tasks import update_fixture_details
                success = update_fixture_details(fixture.id, type='events')
                if success:
                    fixture.refresh_from_db()
        return fixture

@extend_schema(tags=['Fixture Details'], summary="Get Lineups")
class FixtureLineupsView(ActivityLogMixin, generics.RetrieveAPIView):
    serializer_class = FixtureLineupSerializer
    activity_action = "VIEW_FIXTURE_LINEUPS"
    def get_activity_details(self, request, *args, **kwargs):
        return f"Viewed lineups for Fixture {kwargs.get('pk')}"
    
    @method_decorator(cache_page(30))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

    def get_object(self):
        obj, created = FixtureLineup.objects.get_or_create(fixture_id=self.kwargs['pk'])
        if not obj.home or not obj.away:
            from django.utils import timezone
            from datetime import timedelta
            if created or (timezone.now() - obj.updated_at > timedelta(minutes=2)):
                from .tasks import update_fixture_details
                success = update_fixture_details(self.kwargs['pk'], type='lineups')
                if success:
                    obj.refresh_from_db()
        return obj

@extend_schema(tags=['Fixture Details'], summary="Get Statistics")
class FixtureStatisticsView(ActivityLogMixin, generics.RetrieveAPIView):
    serializer_class = FixtureStatisticSerializer
    activity_action = "VIEW_FIXTURE_STATS"
    def get_activity_details(self, request, *args, **kwargs):
        return f"Viewed statistics for Fixture {kwargs.get('pk')}"

    @method_decorator(cache_page(60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)

    def get_object(self):
        obj, created = FixtureStatistic.objects.get_or_create(fixture_id=self.kwargs['pk'])
        if not obj.data:
            from django.utils import timezone
            from datetime import timedelta
            if created or (timezone.now() - obj.updated_at > timedelta(minutes=5)):
                from .tasks import update_fixture_details
                success = update_fixture_details(self.kwargs['pk'], type='statistics')
                if success:
                    obj.refresh_from_db()
        return obj

@extend_schema(tags=['Fixture Details'], summary="Get Head-to-Head")
class FixtureHeadToHeadView(ActivityLogMixin, generics.RetrieveAPIView):
    serializer_class = HeadToHeadSerializer
    activity_action = "VIEW_FIXTURE_H2H"
    def get_activity_details(self, request, *args, **kwargs):
        return f"Viewed Head-to-Head stats for Fixture {kwargs.get('pk')}"

    @method_decorator(cache_page(60))
    def dispatch(self, *args, **kwargs): return super().dispatch(*args, **kwargs)
    
    def get_object(self):
        fixture_id = self.kwargs['pk']
        fixture = get_object_or_404(Fixture, id=fixture_id)
        t1 = fixture.home_team
        t2 = fixture.away_team
        
        if t1.id < t2.id:
            team_1, team_2 = t1, t2
        else:
            team_1, team_2 = t2, t1

        try:
            return HeadToHead.objects.get(team_1=team_1, team_2=team_2)
        except HeadToHead.DoesNotExist:
            print(f"H2H missing for {team_1.name} vs {team_2.name}. Fetching now...")
            fetch_and_update_h2h_record(team_1, team_2)
            return get_object_or_404(HeadToHead, team_1=team_1, team_2=team_2)


# =========================================================
#                    USER FAVORITES
# =========================================================

@extend_schema_view(
    get=extend_schema(
        tags=['User Favorites'],
        summary="List User Favorites",
        description="Get a list of favorite 'teams' or 'leagues' for a specific user.",
        parameters=[
            OpenApiParameter(
                name='type', 
                location=OpenApiParameter.PATH, 
                description="Type of favorite to retrieve ('teams' or 'leagues')", 
                required=True, 
                type=str, 
                enum=['teams', 'leagues']
            )
        ],
        responses={
            200: OpenApiResponse(
                description="List of favorites (Format depends on 'type' parameter)",
                examples=[
                    OpenApiExample("Teams Example", value=[{"id": 1, "team_details": {"name": "Arsenal"}}]),
                    OpenApiExample("Leagues Example", value=[{"id": 1, "league_details": {"name": "Premier League"}}])
                ]
            )
        }
    ),
    post=extend_schema(
        tags=['User Favorites'],
        summary="Add to Favorites",
        description="Add a specific Team ID or League ID to the user's favorites.",
        request=FavoriteIDSerializer,
        parameters=[
            OpenApiParameter(
                name='type', 
                location=OpenApiParameter.PATH, 
                enum=['teams', 'leagues']
            )
        ],
        responses={
            201: OpenApiResponse(description="Successfully added"),
            400: OpenApiResponse(description="Invalid ID or Type")
        }
    ),
    delete=extend_schema(
        tags=['User Favorites'],
        summary="Remove from Favorites",
        description="Delete a favorite item by the Item ID (Team ID or League ID), NOT the favorite record ID.",
        parameters=[
            OpenApiParameter(name='type', location=OpenApiParameter.PATH, enum=['teams', 'leagues']),
            OpenApiParameter(name='item_id', location=OpenApiParameter.PATH, type=int, description="The ID of the Team or League to remove")
        ],
        responses={
            204: OpenApiResponse(description="Successfully deleted"),
            404: OpenApiResponse(description="Favorite not found")
        }
    )
)
class ManageUserFavoritesView(APIView):
    permission_classes = [IsOwnerOrAdmin]

    def _get_type_config(self, type_name):
        if type_name == 'teams':
            return {
                'model': FavoriteTeam,
                'target_model': Team,
                'serializer': FavoriteTeamSerializer,
                'field': 'team' 
            }
        elif type_name == 'leagues':
            return {
                'model': FavoriteLeague,
                'target_model': League,
                'serializer': FavoriteLeagueSerializer,
                'field': 'league'
            }
        return None

    def get(self, request, user_id, type):
        config = self._get_type_config(type)
        if not config:
            return Response({"error": "Invalid type. Use 'teams' or 'leagues'."}, status=400)

        target_user = get_object_or_404(User, pk=user_id)
        queryset = config['model'].objects.filter(user=target_user)
        serializer = config['serializer'](queryset, many=True)
        return Response(serializer.data)

    def post(self, request, user_id, type):
        config = self._get_type_config(type)
        if not config:
            return Response({"error": "Invalid type"}, status=400)
        
        target_user = get_object_or_404(User, pk=user_id)

        serializer = FavoriteIDSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        
        obj_id = serializer.validated_data['id']
        target_obj = get_object_or_404(config['target_model'], pk=obj_id)

        create_kwargs = {'user': target_user, config['field']: target_obj}
        obj, created = config['model'].objects.get_or_create(**create_kwargs)

        if created:
            # Log this action to the request user
            log_activity(request.user, "ADD_FAVORITE", f"Added {type[:-1].capitalize()} ID {obj_id} to favorites", request)
            return Response({"status": "Added", "id": obj_id}, status=status.HTTP_200_OK)
        return Response({"status": "Already exists", "id": obj_id}, status=status.HTTP_200_OK)

    def delete(self, request, user_id, type, item_id):
        config = self._get_type_config(type)
        if not config:
            return Response({"error": "Invalid type"}, status=400)
        
        target_user = get_object_or_404(User, pk=user_id)

        filter_kwargs = {'user': target_user, f"{config['field']}_id": item_id}
        deleted_count, _ = config['model'].objects.filter(**filter_kwargs).delete()

        if deleted_count > 0:
            # Log this action to the request user
            log_activity(request.user, "REMOVE_FAVORITE", f"Removed {type[:-1].capitalize()} ID {item_id} from favorites", request)
            return Response({"status": "Removed", "id": item_id}, status=status.HTTP_200_OK)
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(
    tags=['Players'],
    summary="Get Player Details",
    parameters=[
        OpenApiParameter(
            name='season',
            description='Season year (e.g. 2026)',
            required=False,
            type=int
        )
    ]
)
class PlayerDetailView(APIView):
    def get(self, request, pk):
        from django.utils import timezone
        from datetime import timedelta
        
        # Default to current active season/year
        season = request.query_params.get('season')
        if not season:
            current_year = timezone.now().year
            latest_season = Season.objects.filter(year__lte=current_year).order_by('-year').first()
            if not latest_season:
                latest_season = Season.objects.order_by('-year').first()
            season = latest_season.year if latest_season else current_year
        else:
            try:
                season = int(season)
            except ValueError:
                return Response(
                    {"error": "Invalid season parameter. Must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        player_id = pk
        need_fetch = False
        try:
            profile = PlayerProfile.objects.get(player_id=player_id, season=season)
            if profile.updated_at < timezone.now() - timedelta(hours=24):
                need_fetch = True
        except PlayerProfile.DoesNotExist:
            need_fetch = True
            profile = None

        if need_fetch:
            from .tasks import fetch_and_update_player_profile
            data = fetch_and_update_player_profile(player_id, season)
            if data:
                profile = PlayerProfile.objects.get(player_id=player_id, season=season)
            elif not profile:
                return Response(
                    {"error": "Player details not found or failed to retrieve from external provider."},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = PlayerProfileSerializer(profile)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BasePlayerStatListView(APIView):
    stat_type = None  # To be overridden by subclasses

    def get(self, request):
        from django.utils import timezone
        from datetime import timedelta

        if not self.stat_type:
            return Response(
                {"error": "Developer configuration error: stat_type is not defined."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        league_id = request.query_params.get('league')
        if not league_id:
            return Response(
                {"error": "league parameter is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            league_id = int(league_id)
        except ValueError:
            return Response(
                {"error": "Invalid league parameter. Must be an integer."},
                status=status.HTTP_400_BAD_REQUEST
            )

        season_param = request.query_params.get('season')
        if not season_param:
            current_year = timezone.now().year
            latest_season = Season.objects.filter(year__lte=current_year).order_by('-year').first()
            if not latest_season:
                latest_season = Season.objects.order_by('-year').first()
            season_year = latest_season.year if latest_season else current_year
        else:
            try:
                season_year = int(season_param)
            except ValueError:
                return Response(
                    {"error": "Invalid season parameter. Must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        need_fetch = False
        try:
            record = PlayerStatList.objects.get(
                league_id=league_id,
                season_id=season_year,
                stat_type=self.stat_type
            )
            if record.updated_at < timezone.now() - timedelta(hours=2):
                need_fetch = True
        except PlayerStatList.DoesNotExist:
            need_fetch = True
            record = None

        if need_fetch:
            from .tasks import fetch_and_update_player_stats
            data = fetch_and_update_player_stats(league_id, season_year, self.stat_type)
            if data is not None:
                record = PlayerStatList.objects.get(
                    league_id=league_id,
                    season_id=season_year,
                    stat_type=self.stat_type
                )
            elif not record:
                return Response(
                    {"error": f"{self.stat_type.replace('top', 'top ')} details not found or failed to retrieve."},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = PlayerStatListSerializer(record)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Players'],
    summary="Get Top Scorers",
    parameters=[
        OpenApiParameter(name='league', description='League ID', required=True, type=int),
        OpenApiParameter(name='season', description='Season Year', required=False, type=int)
    ]
)
class TopScorersView(BasePlayerStatListView):
    stat_type = 'topscorers'


@extend_schema(
    tags=['Players'],
    summary="Get Top Assists",
    parameters=[
        OpenApiParameter(name='league', description='League ID', required=True, type=int),
        OpenApiParameter(name='season', description='Season Year', required=False, type=int)
    ]
)
class TopAssistsView(BasePlayerStatListView):
    stat_type = 'topassists'


@extend_schema(
    tags=['Players'],
    summary="Get Top Yellow Cards",
    parameters=[
        OpenApiParameter(name='league', description='League ID', required=True, type=int),
        OpenApiParameter(name='season', description='Season Year', required=False, type=int)
    ]
)
class TopYellowCardsView(BasePlayerStatListView):
    stat_type = 'topyellowcards'


@extend_schema(
    tags=['Players'],
    summary="Get Top Red Cards",
    parameters=[
        OpenApiParameter(name='league', description='League ID', required=True, type=int),
        OpenApiParameter(name='season', description='Season Year', required=False, type=int)
    ]
)
class TopRedCardsView(BasePlayerStatListView):
    stat_type = 'topredcards'


@extend_schema(
    tags=['Teams'],
    summary="Get Team Statistics",
    parameters=[
        OpenApiParameter(name='team', description='Team ID', required=True, type=int),
        OpenApiParameter(name='league', description='League ID', required=True, type=int),
        OpenApiParameter(name='season', description='Season Year', required=False, type=int)
    ]
)
class TeamStatisticsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        team_id = request.query_params.get('team')
        league_id = request.query_params.get('league')
        
        if not team_id or not league_id:
            return Response(
                {"error": "Both team and league parameters are required."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            team_id = int(team_id)
            league_id = int(league_id)
        except ValueError:
            return Response(
                {"error": "Invalid team or league parameters. Must be integers."},
                status=status.HTTP_400_BAD_REQUEST
            )

        season_param = request.query_params.get('season')
        from django.utils import timezone
        if not season_param:
            current_year = timezone.now().year
            latest_season = Season.objects.filter(year__lte=current_year).order_by('-year').first()
            if not latest_season:
                latest_season = Season.objects.order_by('-year').first()
            season_year = latest_season.year if latest_season else current_year
        else:
            try:
                season_year = int(season_param)
            except ValueError:
                return Response(
                    {"error": "Invalid season parameter. Must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        from datetime import timedelta
        need_fetch = False
        try:
            record = TeamStatistic.objects.get(
                team_id=team_id,
                league_id=league_id,
                season_id=season_year
            )
            if record.updated_at < timezone.now() - timedelta(hours=2):
                need_fetch = True
        except TeamStatistic.DoesNotExist:
            need_fetch = True
            record = None

        if need_fetch:
            from .tasks import fetch_and_update_team_statistics
            data = fetch_and_update_team_statistics(team_id, league_id, season_year)
            if data is not None:
                try:
                    record = TeamStatistic.objects.get(
                        team_id=team_id,
                        league_id=league_id,
                        season_id=season_year
                    )
                except TeamStatistic.DoesNotExist:
                    record = None
            elif not record:
                return Response(
                    {"error": "Team statistics details not found or failed to retrieve."},
                    status=status.HTTP_404_NOT_FOUND
                )

        if not record:
            return Response(
                {"error": "Team statistics details not found or failed to retrieve."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = TeamStatisticSerializer(record)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Teams'],
    summary="Get Team Coaches List",
    parameters=[
        OpenApiParameter(name='team', description='Team ID', required=True, type=int)
    ]
)
class CoachsListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        team_id = request.query_params.get('team')
        if not team_id:
            return Response(
                {"error": "team parameter is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            team_id = int(team_id)
        except ValueError:
            return Response(
                {"error": "Invalid team parameter. Must be an integer."},
                status=status.HTTP_400_BAD_REQUEST
            )

        from datetime import timedelta
        from django.utils import timezone
        need_fetch = False
        try:
            record = TeamCoachList.objects.get(team_id=team_id)
            if record.updated_at < timezone.now() - timedelta(days=1):
                need_fetch = True
        except TeamCoachList.DoesNotExist:
            need_fetch = True
            record = None

        if need_fetch:
            from .tasks import fetch_and_update_team_coaches
            data = fetch_and_update_team_coaches(team_id)
            if data is not None:
                try:
                    record = TeamCoachList.objects.get(team_id=team_id)
                except TeamCoachList.DoesNotExist:
                    record = None
            elif not record:
                return Response(
                    {"error": "Coaches details not found or failed to retrieve."},
                    status=status.HTTP_404_NOT_FOUND
                )

        if not record:
            return Response(
                {"error": "Coaches details not found or failed to retrieve."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = TeamCoachListSerializer(record)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Fixtures'],
    summary="Get Match Events",
    description="Returns list of events (goals, cards, substitutions) for a specific fixture."
)
class FixtureEventsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            fixture = Fixture.objects.get(id=pk)
        except Fixture.DoesNotExist:
            return Response({"error": "Fixture not found"}, status=status.HTTP_404_NOT_FOUND)

        # Update events from API if empty or match is currently live
        need_fetch = False
        if not fixture.events:
            need_fetch = True
        elif fixture.status_short in Fixture.LIVE_STATUSES:
            need_fetch = True

        if need_fetch:
            from .tasks import update_fixture_details
            success = update_fixture_details(pk, type='events')
            if success:
                fixture.refresh_from_db()

        return Response({
            "count": len(fixture.events or []),
            "results": fixture.events or []
        }, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Players'],
    summary="Get Team Squad",
    description="Returns squad players list for a specific team."
)
class TeamSquadsListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from django.utils import timezone
        from datetime import timedelta
        
        team_id = request.query_params.get('team')
        if not team_id:
            return Response({"error": "team parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            team_id = int(team_id)
        except ValueError:
            return Response({"error": "Invalid team parameter. Must be an integer."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            team = Team.objects.get(id=team_id)
        except Team.DoesNotExist:
            return Response({"error": "Team not found"}, status=status.HTTP_404_NOT_FOUND)

        need_fetch = False
        try:
            squad = TeamSquad.objects.get(team=team)
            if squad.updated_at < timezone.now() - timedelta(hours=24):
                need_fetch = True
        except TeamSquad.DoesNotExist:
            need_fetch = True
            squad = None

        if need_fetch:
            from .tasks import fetch_and_update_team_squad
            players = fetch_and_update_team_squad(team_id)
            if players is not None:
                try:
                    squad = TeamSquad.objects.get(team=team)
                except TeamSquad.DoesNotExist:
                    squad = None
            elif not squad:
                return Response({"error": "Squad details not found or failed to retrieve."}, status=status.HTTP_404_NOT_FOUND)

        if not squad:
            return Response({"error": "Squad details not found or failed to retrieve."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "team": {
                "id": team.id,
                "name": team.name,
                "logo": team.logo
            },
            "players": squad.players or []
        }, status=status.HTTP_200_OK)