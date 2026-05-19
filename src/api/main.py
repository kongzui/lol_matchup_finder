"""React 프론트엔드를 위한 FastAPI 로컬 API."""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from src.cache import MatchCache
from src.champions import ChampionData, ChampionRepository, champion_icon_url
from src.config import AppConfig, load_config
from src.db_search_service import (
    DB_ENEMY_ALL_LABEL,
    IndexedMatchupSearchPayload,
    IndexedMatchupSearchRequest,
    run_indexed_matchup_search,
)
from src.export import (
    build_indexed_results_csv_bytes,
    build_indexed_results_filename,
    build_results_csv_bytes,
    build_results_filename,
)
from src.matchup_filter import extract_focus_view
from src.multi_search_service import (
    DEFAULT_MULTI_MATCHES_PER_PLAYER,
    DEFAULT_MULTI_SEARCH_DAYS,
    MULTI_MATCHES_PER_PLAYER_OPTIONS,
    MULTI_PERIOD_OPTIONS,
    MultiSearchRequest,
    run_multi_search,
)
from src.riot_client import RiotApiError, RiotClient
from src.search_service import (
    DEFAULT_PERIOD_LABEL,
    PERIOD_CUSTOM_LABEL,
    PERIOD_OPTIONS,
    SearchPayload,
    SearchRequest,
    fetch_ranked_profile,
    fetch_match_timeline,
    run_search,
)
from src.static_data import (
    StaticData,
    StaticDataRepository,
    item_icon_url,
    stat_shard_icon_url,
    stat_shard_name,
)
from src.timeline import extract_player_build_timeline
from src.utils import LANE_LABELS


JobStatus = Literal["queued", "running", "succeeded", "failed"]


class SearchJobBody(BaseModel):
    """개별유저검색 job 생성 요청."""

    model_config = ConfigDict(populate_by_name=True)

    riot_id_raw: str = Field(alias="riotIdRaw")
    period_kind: str = Field(alias="periodKind", default=DEFAULT_PERIOD_LABEL)
    custom_start: date | None = Field(alias="customStart", default=None)
    custom_end: date | None = Field(alias="customEnd", default=None)
    my_champion_korean: str = Field(alias="myChampionKorean")
    enemy_champion_korean: str = Field(alias="enemyChampionKorean")
    lane_label: str = Field(alias="laneLabel")
    max_matches: int = Field(alias="maxMatches", default=100)


class MultiSearchJobBody(BaseModel):
    """멀티서치 job 생성 요청."""

    model_config = ConfigDict(populate_by_name=True)

    riot_ids_raw: str = Field(alias="riotIdsRaw")
    days: int = DEFAULT_MULTI_SEARCH_DAYS
    matches_per_player: int = Field(
        alias="matchesPerPlayer",
        default=DEFAULT_MULTI_MATCHES_PER_PLAYER,
    )


class DbSearchBody(BaseModel):
    """DB조회 요청."""

    model_config = ConfigDict(populate_by_name=True)

    my_champion_korean: str = Field(alias="myChampionKorean")
    enemy_champion_korean: str = Field(alias="enemyChampionKorean")
    lane_label: str = Field(alias="laneLabel")
    period_kind: str = Field(alias="periodKind", default=DEFAULT_PERIOD_LABEL)
    custom_start: date | None = Field(alias="customStart", default=None)
    custom_end: date | None = Field(alias="customEnd", default=None)
    current_patch_only: bool = Field(alias="currentPatchOnly", default=False)
    page: int = 1
    page_size: int = Field(alias="pageSize", default=50)


@dataclass
class JobRecord:
    """백그라운드 작업 상태."""

    job_id: str
    kind: str
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Any = None


class JobStore:
    """서버 생명주기 동안만 유지되는 인메모리 job 저장소."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, runner: Callable[[JobRecord], None]) -> JobRecord:
        job = JobRecord(job_id=uuid.uuid4().hex, kind=kind, status="queued")
        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run, args=(job.job_id, runner), daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _run(self, job_id: str, runner: Callable[[JobRecord], None]) -> None:
        self.update(job_id, status="running", progress=0.0)
        job = self.get(job_id)
        if job is None:
            return
        try:
            runner(job)
        except Exception as exc:  # noqa: BLE001 - API 경계에서 오류 메시지를 job에 보관한다.
            self.update(job_id, status="failed", error=str(exc), message="작업 실패")


app = FastAPI(title="LoL Matchup Finder API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs = JobStore()
_config: AppConfig | None = None
_cache: MatchCache | None = None
_champion_data: ChampionData | None = None
_static_data: StaticData | None = None
_resource_lock = threading.Lock()


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_cache() -> MatchCache:
    global _cache
    if _cache is None:
        _cache = MatchCache(_get_config().db_path)
    return _cache


def _get_champion_data() -> ChampionData:
    global _champion_data
    if _champion_data is None:
        with _resource_lock:
            if _champion_data is None:
                _champion_data = ChampionRepository(_get_config().db_path).load()
    return _champion_data


def _get_static_data() -> StaticData:
    global _static_data
    if _static_data is None:
        with _resource_lock:
            if _static_data is None:
                _static_data = StaticDataRepository(_get_config().db_path).load()
    return _static_data


def _default_dates(
    custom_start: date | None,
    custom_end: date | None,
) -> tuple[date, date]:
    today = date.today()
    return custom_start or today - timedelta(days=90), custom_end or today


def _search_request(body: SearchJobBody) -> SearchRequest:
    custom_start, custom_end = _default_dates(body.custom_start, body.custom_end)
    return SearchRequest(
        riot_id_raw=body.riot_id_raw,
        period_kind=body.period_kind,
        custom_start=custom_start,
        custom_end=custom_end,
        my_champion_korean=body.my_champion_korean,
        enemy_champion_korean=body.enemy_champion_korean,
        lane_label=body.lane_label,
        max_matches=body.max_matches,
    )


def _db_request(body: DbSearchBody) -> IndexedMatchupSearchRequest:
    custom_start, custom_end = _default_dates(body.custom_start, body.custom_end)
    return IndexedMatchupSearchRequest(
        my_champion_korean=body.my_champion_korean,
        enemy_champion_korean=body.enemy_champion_korean,
        lane_label=body.lane_label,
        period_kind=body.period_kind,
        custom_start=custom_start,
        custom_end=custom_end,
        current_patch_only=body.current_patch_only,
    )


def _job_status(job: JobRecord) -> dict[str, Any]:
    return {
        "jobId": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
    }


def _payload_dict(payload: Any) -> dict[str, Any]:
    return (
        asdict(payload) if hasattr(payload, "__dataclass_fields__") else dict(payload)
    )


def _result_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    wins = sum(1 for row in results if row.get("win"))
    losses = total - wins
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / total * 100, 1) if total else None,
    }


def _rune_entry(
    static_data: StaticData, rune_id: int, selected: set[int]
) -> dict[str, Any]:
    return {
        "id": rune_id,
        "name": static_data.rune_name(rune_id),
        "iconUrl": static_data.rune_icon_url(rune_id),
        "selected": rune_id in selected,
    }


def _rune_page(
    static_data: StaticData,
    tree_id: int | None,
    selected_runes: list[int],
) -> dict[str, Any] | None:
    if tree_id is None:
        return None
    selected = {int(rune_id) for rune_id in selected_runes}
    return {
        "treeId": tree_id,
        "treeName": static_data.tree_name(tree_id),
        "treeIconUrl": static_data.tree_icon_url(tree_id),
        "slots": [
            [_rune_entry(static_data, rune_id, selected) for rune_id in slot]
            for slot in static_data.tree_slots(tree_id)
        ],
    }


def _stat_shard_page(player: dict[str, Any]) -> list[list[dict[str, Any]]]:
    selected_by_row = [
        player.get("stat_offense"),
        player.get("stat_flex"),
        player.get("stat_defense"),
    ]
    shard_rows = [
        [5008, 5005, 5007],
        [5008, 5010, 5001],
        [5011, 5013, 5001],
    ]
    return [
        [
            {
                "id": shard_id,
                "name": stat_shard_name(shard_id),
                "iconUrl": stat_shard_icon_url(shard_id),
                "selected": shard_id == selected_by_row[row_idx],
            }
            for shard_id in row
        ]
        for row_idx, row in enumerate(shard_rows)
    ]


def _slice_payload(
    payload: IndexedMatchupSearchPayload,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    raw = _payload_dict(payload)
    results = raw["results"]
    start = (page - 1) * page_size
    raw["results"] = results[start : start + page_size]
    raw["total"] = len(results)
    raw["page"] = page
    raw["pageSize"] = page_size
    raw["stats"] = _result_stats(results)
    return raw


def _participant_with_assets(player: dict[str, Any] | None) -> dict[str, Any] | None:
    if player is None:
        return None
    champion_data = _get_champion_data()
    static_data = _get_static_data()
    version = static_data.version or champion_data.version
    champion_key = player.get("champion_key")
    enriched = dict(player)
    enriched["championNameKo"] = champion_data.to_korean_name(champion_key)
    enriched["championIconUrl"] = champion_icon_url(version, champion_key)
    enriched["summoner1IconUrl"] = static_data.summoner_icon_url(
        player.get("summoner1_id")
    )
    enriched["summoner2IconUrl"] = static_data.summoner_icon_url(
        player.get("summoner2_id")
    )
    enriched["summoner1Name"] = static_data.summoner_name(player.get("summoner1_id"))
    enriched["summoner2Name"] = static_data.summoner_name(player.get("summoner2_id"))
    enriched["itemIconUrls"] = [
        item_icon_url(version, item_id) for item_id in player.get("items", [])
    ]
    enriched["primaryRuneIconUrls"] = [
        static_data.rune_icon_url(rune_id)
        for rune_id in player.get("primary_runes", [])
    ]
    enriched["secondaryRuneIconUrls"] = [
        static_data.rune_icon_url(rune_id)
        for rune_id in player.get("secondary_runes", [])
    ]
    enriched["primaryRuneNames"] = [
        static_data.rune_name(rune_id) for rune_id in player.get("primary_runes", [])
    ]
    enriched["secondaryRuneNames"] = [
        static_data.rune_name(rune_id) for rune_id in player.get("secondary_runes", [])
    ]
    enriched["primaryTreeIconUrl"] = static_data.tree_icon_url(
        player.get("primary_tree_id")
    )
    enriched["secondaryTreeIconUrl"] = static_data.tree_icon_url(
        player.get("secondary_tree_id")
    )
    enriched["statShardIconUrls"] = [
        stat_shard_icon_url(player.get("stat_offense")),
        stat_shard_icon_url(player.get("stat_flex")),
        stat_shard_icon_url(player.get("stat_defense")),
    ]
    enriched["statShardNames"] = [
        stat_shard_name(player.get("stat_offense")),
        stat_shard_name(player.get("stat_flex")),
        stat_shard_name(player.get("stat_defense")),
    ]
    enriched["primaryTreeName"] = static_data.tree_name(player.get("primary_tree_id"))
    enriched["secondaryTreeName"] = static_data.tree_name(
        player.get("secondary_tree_id")
    )
    enriched["primaryRunePage"] = _rune_page(
        static_data,
        player.get("primary_tree_id"),
        player.get("primary_runes", []),
    )
    enriched["secondaryRunePage"] = _rune_page(
        static_data,
        player.get("secondary_tree_id"),
        player.get("secondary_runes", []),
    )
    enriched["statShardPage"] = _stat_shard_page(player)
    return enriched


def _summary_with_assets(player: dict[str, Any]) -> dict[str, Any]:
    return _participant_with_assets(player) or dict(player)


_TIER_LABELS = {
    "IRON": "아이언",
    "BRONZE": "브론즈",
    "SILVER": "실버",
    "GOLD": "골드",
    "PLATINUM": "플래티넘",
    "EMERALD": "에메랄드",
    "DIAMOND": "다이아몬드",
    "MASTER": "마스터",
    "GRANDMASTER": "그랜드마스터",
    "CHALLENGER": "챌린저",
}


def _rank_label(profile: dict[str, Any] | None) -> str:
    if not profile or not profile.get("tier"):
        return "랭크 없음"
    tier_key = str(profile.get("tier") or "").upper()
    tier = _TIER_LABELS.get(tier_key, tier_key.title())
    rank = profile.get("rank") or ""
    lp = profile.get("league_points")
    lp_text = f" {lp}LP" if lp is not None else ""
    return f"{tier} {rank}{lp_text}".strip()


def _attach_rank_profiles(
    players: list[dict[str, Any]],
    *,
    cache: MatchCache,
    config: AppConfig,
) -> None:
    puuids = [player.get("puuid") for player in players if player.get("puuid")]
    if not puuids:
        return

    missing_puuids: list[str] = []
    for player in players:
        puuid = player.get("puuid")
        profile = cache.get_ranked_profile(puuid, config.queue_id) if puuid else None
        if profile is None and puuid:
            missing_puuids.append(puuid)
        player["rankedProfile"] = profile
        player["rankLabel"] = _rank_label(profile)

    if missing_puuids and config.api_key:
        try:
            with RiotClient(
                api_key=config.api_key,
                region=config.region,
                platform=config.platform,
            ) as client:
                for puuid in dict.fromkeys(missing_puuids):
                    try:
                        fetch_ranked_profile(client, cache, puuid, config.queue_id)
                    except RiotApiError:
                        continue
        except RiotApiError:
            return

        for player in players:
            puuid = player.get("puuid")
            profile = (
                cache.get_ranked_profile(puuid, config.queue_id) if puuid else None
            )
            player["rankedProfile"] = profile
            player["rankLabel"] = _rank_label(profile)


@app.get("/api/meta/options")
def get_options() -> dict[str, Any]:
    """프론트 초기 렌더링에 필요한 옵션을 반환한다."""
    config = _get_config()
    champion_data = _get_champion_data()
    static_data = _get_static_data()
    cache = _get_cache()
    champions = [
        {
            "koreanName": korean_name,
            "key": champion_data.to_english_key(korean_name),
            "iconUrl": champion_icon_url(
                champion_data.version,
                champion_data.to_english_key(korean_name) or "",
            ),
        }
        for korean_name in champion_data.korean_names
    ]
    return {
        "champions": champions,
        "laneOptions": LANE_LABELS,
        "periodOptions": PERIOD_OPTIONS,
        "defaultPeriod": DEFAULT_PERIOD_LABEL,
        "customPeriodLabel": PERIOD_CUSTOM_LABEL,
        "multiPeriodOptions": MULTI_PERIOD_OPTIONS,
        "multiMatchesPerPlayerOptions": MULTI_MATCHES_PER_PLAYER_OPTIONS,
        "defaultMultiDays": DEFAULT_MULTI_SEARCH_DAYS,
        "defaultMultiMatchesPerPlayer": DEFAULT_MULTI_MATCHES_PER_PLAYER,
        "dbEnemyAllLabel": DB_ENEMY_ALL_LABEL,
        "dataDragonVersion": champion_data.version,
        "staticDataVersion": static_data.version,
        "apiKeyDetected": bool(config.api_key),
        "region": config.region,
        "platform": config.platform,
        "queueId": config.queue_id,
        "latestSearchRiotId": cache.get_latest_search_riot_id(),
    }


@app.post("/api/jobs/search")
def create_search_job(body: SearchJobBody) -> dict[str, str]:
    """개별유저검색 job을 생성한다."""
    request = _search_request(body)

    def runner(job: JobRecord) -> None:
        def progress_cb(value: float) -> None:
            _jobs.update(job.job_id, progress=min(max(value, 0.0), 1.0))

        def status_cb(message: str) -> None:
            _jobs.update(job.job_id, message=message)

        result = run_search(
            config=_get_config(),
            request=request,
            champion_data=_get_champion_data(),
            cache=_get_cache(),
            progress_cb=progress_cb,
            status_cb=status_cb,
        )
        _jobs.update(
            job.job_id,
            status="succeeded",
            progress=1.0,
            message="검색 완료",
            result=result,
        )

    job = _jobs.create("search", runner)
    return {"jobId": job.job_id}


@app.post("/api/jobs/multi-search")
def create_multi_search_job(body: MultiSearchJobBody) -> dict[str, str]:
    """멀티서치 수집 job을 생성한다."""
    request = MultiSearchRequest(
        riot_ids_raw=body.riot_ids_raw,
        days=body.days,
        matches_per_player=body.matches_per_player,
    )

    def runner(job: JobRecord) -> None:
        def progress_cb(value: float) -> None:
            _jobs.update(job.job_id, progress=min(max(value, 0.0), 1.0))

        def status_cb(message: str) -> None:
            _jobs.update(job.job_id, message=message)

        result = run_multi_search(
            config=_get_config(),
            request=request,
            cache=_get_cache(),
            progress_cb=progress_cb,
            status_cb=status_cb,
        )
        _jobs.update(
            job.job_id,
            status="succeeded",
            progress=1.0,
            message="수집 완료",
            result=result,
        )

    job = _jobs.create("multi-search", runner)
    return {"jobId": job.job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    """job 상태를 조회한다."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job을 찾을 수 없습니다.")
    return _job_status(job)


@app.get("/api/jobs/{job_id}/result")
def get_job_result(job_id: str) -> dict[str, Any]:
    """완료된 job 결과를 조회한다."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job을 찾을 수 없습니다.")
    if job.status == "failed":
        raise HTTPException(status_code=400, detail=job.error or "작업 실패")
    if job.status != "succeeded":
        raise HTTPException(status_code=409, detail="job이 아직 완료되지 않았습니다.")
    return {"kind": job.kind, "payload": _payload_dict(job.result)}


@app.post("/api/db-search")
def db_search(body: DbSearchBody) -> dict[str, Any]:
    """matchup_index 기반 DB조회를 실행한다."""
    try:
        payload = run_indexed_matchup_search(
            request=_db_request(body),
            champion_data=_get_champion_data(),
            cache=_get_cache(),
        )
    except RiotApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _slice_payload(payload, body.page, body.page_size)


@app.get("/api/matches/{match_id}/detail")
def get_match_detail(
    match_id: str,
    player_puuid: str = Query(alias="playerPuuid"),
) -> dict[str, Any]:
    """매치 상세 패널 데이터를 반환한다."""
    cache = _get_cache()
    match = cache.get_match(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="캐시에서 매치를 찾을 수 없습니다.")

    focus = extract_focus_view(match, player_puuid)
    if focus is None:
        raise HTTPException(status_code=404, detail="기준 플레이어를 찾을 수 없습니다.")

    timeline = cache.get_match_timeline(match_id)
    config = _get_config()
    timeline_error = None
    if timeline is None and config.api_key:
        try:
            with RiotClient(
                api_key=config.api_key,
                region=config.region,
                platform=config.platform,
            ) as client:
                timeline = fetch_match_timeline(client, cache, match_id)
        except RiotApiError as exc:
            timeline_error = str(exc)

    build_timeline = (
        extract_player_build_timeline(match, timeline, player_puuid)
        if timeline is not None
        else None
    )
    if build_timeline is not None:
        champion_data = _get_champion_data()
        version = _get_static_data().version or _get_champion_data().version
        me = focus.get("me") or {}
        champion_key = me.get("champion_key")
        build_timeline = dict(build_timeline)
        build_timeline["item_events"] = [
            {
                **event,
                "icon_url": item_icon_url(version, event.get("item_id")),
            }
            for event in build_timeline.get("item_events", [])
        ]
        build_timeline["skill_events"] = [
            {
                **event,
                "skillSlot": event.get("skill_slot"),
                "iconUrl": champion_data.spell_icon_url(
                    champion_key,
                    int(event.get("skill_slot") or 0),
                ),
                "spellName": (
                    champion_data.spell_info(
                        champion_key,
                        int(event.get("skill_slot") or 0),
                    )
                    or {}
                ).get("name", ""),
            }
            for event in build_timeline.get("skill_events", [])
        ]

    me = _participant_with_assets(focus.get("me"))
    enemy_laner = _participant_with_assets(focus.get("enemy_laner"))
    others_ally = [
        _summary_with_assets(player) for player in focus.get("others_ally") or []
    ]
    others_enemy = [
        _summary_with_assets(player) for player in focus.get("others_enemy") or []
    ]
    players_for_rank = [
        player
        for player in [me, enemy_laner, *others_ally, *others_enemy]
        if player is not None
    ]
    _attach_rank_profiles(players_for_rank, cache=cache, config=config)

    return {
        "queueId": focus.get("queue_id"),
        "gameDuration": focus.get("game_duration"),
        "gameVersion": focus.get("game_version"),
        "me": me,
        "enemyLaner": enemy_laner,
        "othersAlly": others_ally,
        "othersEnemy": others_enemy,
        "buildTimeline": build_timeline,
        "timelineError": timeline_error,
    }


@app.get("/api/exports/search/{job_id}.csv")
def export_search_csv(job_id: str) -> Response:
    """완료된 개별유저검색 결과를 CSV로 반환한다."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job을 찾을 수 없습니다.")
    if job.status != "succeeded" or not isinstance(job.result, SearchPayload):
        raise HTTPException(status_code=409, detail="내보낼 검색 결과가 없습니다.")
    return Response(
        content=build_results_csv_bytes(job.result),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{build_results_filename(job.result)}"'
        },
    )


@app.post("/api/exports/db-search.csv")
def export_db_search_csv(body: DbSearchBody) -> Response:
    """DB조회 조건 기준 전체 결과를 CSV로 반환한다."""
    try:
        payload = run_indexed_matchup_search(
            request=_db_request(body),
            champion_data=_get_champion_data(),
            cache=_get_cache(),
        )
    except RiotApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=build_indexed_results_csv_bytes(payload),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{build_indexed_results_filename(payload)}"'
            )
        },
    )
