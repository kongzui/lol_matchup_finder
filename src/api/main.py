"""React 프론트엔드를 위한 FastAPI 로컬 API."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from math import ceil
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from src.cache import MatchCache, normalize_riot_id_key
from src.champions import ChampionData, ChampionRepository, champion_icon_url
from src.config import AppConfig, load_config
from src.db_search_service import (
    DB_DEFAULT_PERIOD_LABEL,
    DB_ENEMY_ALL_LABEL,
    DB_PERIOD_OPTIONS,
    IndexedMatchupSearchPayload,
    IndexedMatchupSearchRequest,
    find_indexed_enemy_riot_ids,
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
    MultiSearchTarget,
    parse_multi_riot_ids,
    run_multi_search,
)
from src.riot_client import RiotApiError, RiotClient
from src.search_service import (
    DEFAULT_PERIOD_LABEL,
    PERIOD_CUSTOM_LABEL,
    PERIOD_OPTIONS,
    SEARCH_ALL_LABEL,
    SearchPayload,
    SearchRequest,
    fetch_ranked_profile,
    fetch_match_timeline,
    index_memo_player_matches,
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
from src.utils import RiotIdParseError, parse_riot_id


JobStatus = Literal["queued", "running", "succeeded", "partial", "failed"]


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
    max_matches: int = Field(alias="maxMatches", default=100, ge=1, le=300)


class MultiSearchJobBody(BaseModel):
    """멀티서치 job 생성 요청."""

    model_config = ConfigDict(populate_by_name=True)

    riot_ids_raw: str = Field(alias="riotIdsRaw")
    days: int = Field(default=DEFAULT_MULTI_SEARCH_DAYS, ge=1, le=180)
    matches_per_player: int = Field(
        alias="matchesPerPlayer",
        default=DEFAULT_MULTI_MATCHES_PER_PLAYER,
        ge=1,
        le=300,
    )


class DbSearchSnapshotBody(BaseModel):
    """페이지 이동과 CSV가 함께 쓰는 확정 조회 기준."""

    model_config = ConfigDict(populate_by_name=True)

    target_puuids: list[str] = Field(alias="targetPuuids")
    start_ts: int = Field(alias="startTs", ge=0)
    end_ts: int = Field(alias="endTs", ge=0)
    patch_prefix: str | None = Field(alias="patchPrefix", default=None)
    max_index_id: int = Field(alias="maxIndexId", ge=0)
    target_label: str = Field(alias="targetLabel")


class DbSearchBody(BaseModel):
    """DB조회 요청."""

    model_config = ConfigDict(populate_by_name=True)

    my_champion_korean: str = Field(alias="myChampionKorean")
    enemy_champion_korean: str = Field(alias="enemyChampionKorean")
    lane_label: str = Field(alias="laneLabel")
    period_kind: str = Field(alias="periodKind", default=DB_DEFAULT_PERIOD_LABEL)
    custom_start: date | None = Field(alias="customStart", default=None)
    custom_end: date | None = Field(alias="customEnd", default=None)
    current_patch_only: bool = Field(alias="currentPatchOnly", default=False)
    riot_ids_raw: str = Field(alias="riotIdsRaw", default="")
    target_puuids: list[str] = Field(alias="targetPuuids", default_factory=list)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(alias="pageSize", default=50, ge=1, le=200)
    snapshot: DbSearchSnapshotBody | None = None


class PlayerMemoBody(BaseModel):
    """관심 유저 메모 저장 요청."""

    memo: str


class PlayerMemoAutoRefreshBody(BaseModel):
    """관심 유저 자동갱신 설정 요청."""

    enabled: bool


class PlayerMatchCollectionBody(BaseModel):
    """메모 유저 매치 수집 요청."""

    confirmed: bool = False


class ResolveRiotIdBody(BaseModel):
    """Riot ID를 PUUID로 변환하는 요청."""

    model_config = ConfigDict(populate_by_name=True)

    riot_id_raw: str = Field(alias="riotIdRaw")


@dataclass
class JobRecord:
    """백그라운드 작업 상태."""

    job_id: str
    kind: str
    status: JobStatus
    origin: str = "unknown"
    title: str = "작업"
    target_summary: str = ""
    dedupe_key: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Any = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    started_at: int | None = None
    updated_at: int = field(default_factory=lambda: int(time.time()))
    finished_at: int | None = None


class JobStore:
    """서버 생명주기 동안만 유지되는 인메모리 job 저장소."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self.server_instance_id = uuid.uuid4().hex
        self.server_started_at = int(time.time())

    def create(
        self,
        kind: str,
        runner: Callable[[JobRecord], None],
        *,
        origin: str = "unknown",
        title: str = "작업",
        target_summary: str = "",
        dedupe_key: str | None = None,
    ) -> JobRecord:
        now = int(time.time())
        with self._lock:
            if dedupe_key:
                existing = next(
                    (
                        job
                        for job in self._jobs.values()
                        if job.dedupe_key == dedupe_key
                        and job.status in {"queued", "running"}
                    ),
                    None,
                )
                if existing is not None:
                    return replace(existing)

            job = JobRecord(
                job_id=uuid.uuid4().hex,
                kind=kind,
                status="queued",
                origin=origin,
                title=title,
                target_summary=target_summary,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run, args=(job.job_id, runner), daemon=True
        )
        thread.start()
        return replace(job)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return replace(job) if job is not None else None

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            now = int(time.time())
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = now
            if changes.get("status") == "running" and job.started_at is None:
                job.started_at = now
            if changes.get("status") in {"succeeded", "partial", "failed"}:
                job.finished_at = now
            self._prune_completed_locked()

    def list_jobs(
        self,
        *,
        recent_limit: int,
    ) -> tuple[list[JobRecord], list[JobRecord]]:
        """진행 중 작업 전체와 최근 종료 작업의 스냅샷을 반환한다."""
        with self._lock:
            active = [
                replace(job)
                for job in self._jobs.values()
                if job.status in {"queued", "running"}
            ]
            recent = [
                replace(job)
                for job in self._jobs.values()
                if job.status in {"succeeded", "partial", "failed"}
            ]
        active.sort(key=lambda job: job.created_at, reverse=True)
        recent.sort(
            key=lambda job: job.finished_at or job.updated_at,
            reverse=True,
        )
        return active, recent[:recent_limit]

    def _prune_completed_locked(self) -> None:
        completed = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in {"succeeded", "partial", "failed"}
            ),
            key=lambda job: job.finished_at or job.updated_at,
            reverse=True,
        )
        for job in completed[100:]:
            self._jobs.pop(job.job_id, None)

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
_data_dragon_checked = False
_resource_lock = threading.RLock()
_cache_lock = threading.Lock()
_auto_refresh_lock = threading.RLock()
_auto_refresh_checked = False
_auto_refresh_status: dict[str, Any] = {
    "status": "not_checked",
    "message": "앱 시작 시 매치 수집을 아직 확인하지 않았습니다.",
    "jobId": None,
    "targetCount": 0,
    "plannedTargetCount": 0,
    "days": None,
    "matchesPerPlayer": None,
    "lastCollectedAt": None,
    "requiresConfirmation": False,
    "successCount": None,
    "failureCount": None,
    "failures": [],
}

AUTO_REFRESH_SKIP_DAYS = 2
AUTO_REFRESH_MAX_DAYS = 60
AUTO_REFRESH_MATCHES_PER_DAY = 5
AUTO_REFRESH_MAX_MATCHES_PER_PLAYER = 300
STALE_COLLECTION_DAYS = 90


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_cache() -> MatchCache:
    global _cache
    if _cache is None:
        with _cache_lock:
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


def _refresh_data_dragon_resources(
    *,
    force_refresh: bool,
    check_latest: bool,
) -> tuple[ChampionData, StaticData]:
    global _champion_data, _static_data, _data_dragon_checked
    with _resource_lock:
        config = _get_config()
        champion_data = ChampionRepository(config.db_path).load(
            force_refresh=force_refresh,
            check_latest=check_latest,
        )
        static_data = StaticDataRepository(config.db_path).load(
            force_refresh=force_refresh,
            check_latest=check_latest,
            latest_version=champion_data.version,
        )
        _champion_data = champion_data
        _static_data = static_data
        _data_dragon_checked = True
    return champion_data, static_data


def _ensure_data_dragon_checked() -> None:
    with _resource_lock:
        if _data_dragon_checked:
            return
        _refresh_data_dragon_resources(force_refresh=False, check_latest=True)


def _require_application_started() -> None:
    """숨은 게임 정보 통신 대신 명시적인 시작 준비를 요구한다."""
    if not _data_dragon_checked:
        raise HTTPException(
            status_code=409,
            detail="앱 시작 준비가 아직 끝나지 않았어요. 시작 준비를 다시 실행해 주세요.",
        )


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


def _resolve_db_target_users(
    body: DbSearchBody,
    *,
    cache: MatchCache,
) -> tuple[tuple[str, ...], int, int]:
    riot_ids = parse_multi_riot_ids(body.riot_ids_raw)
    if not riot_ids:
        return (), 0, 0

    accounts: list[dict[str, str]] = []
    cache_hits = 0

    for raw_riot_id in riot_ids:
        try:
            game_name, tag_line = parse_riot_id(raw_riot_id)
        except RiotIdParseError as exc:
            raise RiotApiError(str(exc)) from exc

        cached = cache.get_account(normalize_riot_id_key(game_name, tag_line))
        if cached is not None:
            cache_hits += 1
            accounts.append(
                {
                    "puuid": cached["puuid"],
                    "game_name": cached["game_name"] or game_name,
                    "tag_line": cached["tag_line"] or tag_line,
                }
            )
            continue

        registered_candidates = cache.find_players_by_riot_id(game_name, tag_line)
        if len(registered_candidates) > 1:
            raise RiotApiError(
                f"같은 Riot ID가 서로 다른 저장 유저 {len(registered_candidates)}명에게 "
                f"남아 있어요: {game_name}#{tag_line}. '새 데이터 가져오기'에서 "
                "현재 계정을 먼저 확인해 주세요."
            )
        registered = registered_candidates[0] if registered_candidates else None
        if registered is None:
            raise RiotApiError(
                f"내 컴퓨터에 저장된 유저가 아니에요: {game_name}#{tag_line}. "
                "'Riot에서 최신 경기 가져오기' 또는 '새 데이터 가져오기'로 "
                "이 유저의 경기를 먼저 모아 주세요."
            )
        cache_hits += 1
        accounts.append(
            {
                "puuid": registered["puuid"],
                "game_name": registered["riot_id_game_name"] or game_name,
                "tag_line": registered["riot_id_tag_line"] or tag_line,
            }
        )

    unique_puuids: list[str] = []
    seen_puuids: set[str] = set()
    for account in accounts:
        puuid = account["puuid"]
        if puuid in seen_puuids:
            continue
        seen_puuids.add(puuid)
        unique_puuids.append(puuid)

    return tuple(unique_puuids), cache_hits, 0


def _resolve_player_memo_target(body: ResolveRiotIdBody) -> dict[str, Any]:
    cache = _get_cache()
    config = _get_config()
    try:
        game_name, tag_line = parse_riot_id(body.riot_id_raw)
    except RiotIdParseError as exc:
        raise RiotApiError(str(exc)) from exc

    cached = cache.get_account(normalize_riot_id_key(game_name, tag_line))
    if cached is not None:
        cache.save_player_identity(
            puuid=cached["puuid"],
            game_name=cached["game_name"] or game_name,
            tag_line=cached["tag_line"] or tag_line,
        )
        return {
            "puuid": cached["puuid"],
            "gameName": cached["game_name"] or game_name,
            "tagLine": cached["tag_line"] or tag_line,
        }

    registered_candidates = cache.find_players_by_riot_id(game_name, tag_line)
    registered = registered_candidates[0] if len(registered_candidates) == 1 else None
    if registered is not None:
        return {
            "puuid": registered["puuid"],
            "gameName": registered["riot_id_game_name"] or game_name,
            "tagLine": registered["riot_id_tag_line"] or tag_line,
        }

    if len(registered_candidates) > 1 and not config.api_key:
        raise RiotApiError(
            "같은 Riot ID가 여러 저장 유저에게 남아 있어 현재 계정을 구분할 수 "
            "없어요. RIOT_API_KEY를 설정한 뒤 다시 확인해 주세요."
        )

    if not config.api_key:
        raise RiotApiError("Riot ID를 새로 확인하려면 RIOT_API_KEY가 필요합니다.")

    with RiotClient(
        api_key=config.api_key,
        region=config.region,
        platform=config.platform,
    ) as client:
        account = client.get_account_by_riot_id(game_name, tag_line)

    puuid = account.get("puuid")
    if not puuid:
        raise RiotApiError("Account API 응답에 puuid가 없습니다.")

    resolved_game_name = account.get("gameName") or game_name
    resolved_tag_line = account.get("tagLine") or tag_line
    cache.save_account(
        normalize_riot_id_key(game_name, tag_line),
        puuid=puuid,
        game_name=resolved_game_name,
        tag_line=resolved_tag_line,
    )
    cache.save_player_identity(
        puuid=puuid,
        game_name=resolved_game_name,
        tag_line=resolved_tag_line,
    )
    return {
        "puuid": puuid,
        "gameName": resolved_game_name,
        "tagLine": resolved_tag_line,
    }


def _db_target_label(
    selected_puuids: tuple[str, ...],
    typed_count: int,
    *,
    cache: MatchCache,
) -> str:
    """고른 사람과 직접 입력 수로 조회 대상 이름을 만든다."""
    parts: list[str] = []
    if selected_puuids:
        memos = cache.get_player_memos(list(selected_puuids))
        people = {memos.get(puuid, "").strip() for puuid in selected_puuids}
        if len(people) > 1:
            parts.append(f"관심 유저 {len(people)}명")
        else:
            name = next(iter(people)) or "메모 없음"
            count = len(selected_puuids)
            parts.append(f"{name} · 계정 {count}개" if count > 1 else name)
    if typed_count:
        parts.append(f"입력 유저 {typed_count}명")
    return " + ".join(parts)


def _db_request(
    body: DbSearchBody,
    *,
    cache: MatchCache,
    paginate: bool = True,
) -> IndexedMatchupSearchRequest:
    custom_start, custom_end = _default_dates(body.custom_start, body.custom_end)
    if body.snapshot is not None:
        target_puuids = tuple(dict.fromkeys(body.snapshot.target_puuids))
        cache_hits = 0
        api_calls = 0
        target_label = body.snapshot.target_label
    else:
        target_puuids, cache_hits, api_calls = _resolve_db_target_users(
            body,
            cache=cache,
        )
        selected_puuids = tuple(dict.fromkeys(body.target_puuids))
        if body.riot_ids_raw.strip() or selected_puuids:
            target_label = _db_target_label(
                selected_puuids,
                len(target_puuids),
                cache=cache,
            )
            target_puuids = tuple(dict.fromkeys((*selected_puuids, *target_puuids)))
        else:
            target_puuids = tuple(
                dict.fromkeys(item["puuid"] for item in cache.list_player_memos())
            )
            target_label = f"관심 유저 {len(target_puuids)}명"
    return IndexedMatchupSearchRequest(
        my_champion_korean=body.my_champion_korean,
        enemy_champion_korean=body.enemy_champion_korean,
        lane_label=body.lane_label,
        period_kind=body.period_kind,
        custom_start=custom_start,
        custom_end=custom_end,
        current_patch_only=body.current_patch_only,
        target_puuids=target_puuids,
        resolve_cache_hits=cache_hits,
        resolve_api_calls=api_calls,
        result_limit=body.page_size if paginate else None,
        result_offset=(body.page - 1) * body.page_size if paginate else 0,
        fixed_start_ts=body.snapshot.start_ts if body.snapshot else None,
        fixed_end_ts=body.snapshot.end_ts if body.snapshot else None,
        fixed_patch_prefix=body.snapshot.patch_prefix if body.snapshot else None,
        snapshot_max_index_id=(body.snapshot.max_index_id if body.snapshot else None),
        target_label=target_label,
    )


def _create_multi_search_job(
    *,
    request: MultiSearchRequest,
    kind: str = "multi-search",
    origin: str = "manual-multi",
    title: str = "여러 명 경기 수집",
    target_summary: str = "",
    success_message: str = "수집 완료",
) -> JobRecord:
    """멀티서치 job을 생성한다."""

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
        has_saved_progress = any(
            getattr(result, field_name, 0)
            for field_name in (
                "success_count",
                "new_match_details",
                "indexed_rows",
                "continuation_advanced_count",
            )
        )
        if result.failure_count == 0:
            final_status: JobStatus = "succeeded"
        elif has_saved_progress:
            final_status = "partial"
        else:
            final_status = "failed"
        result_message = (
            f"{result.success_count}명 완료 · " f"{result.failure_count}명 확인 필요"
        )
        _jobs.update(
            job.job_id,
            status=final_status,
            progress=1.0,
            message=success_message if final_status == "succeeded" else result_message,
            error=result_message if final_status == "failed" else None,
            result=result,
        )

    collection_scope: set[tuple[str, str, int, int]] = {
        (
            "target",
            target.puuid,
            target.days,
            target.matches_per_player,
        )
        for target in request.known_targets
    }
    for riot_id_raw in parse_multi_riot_ids(request.riot_ids_raw):
        try:
            game_name, tag_line = parse_riot_id(riot_id_raw)
            key = normalize_riot_id_key(game_name, tag_line)
            cached = _get_cache().get_account(key)
            puuid = cached["puuid"] if cached else None
        except RiotIdParseError:
            key = riot_id_raw.strip().lower()
            puuid = None
        collection_scope.add(
            (
                "target" if puuid else "riot-id",
                str(puuid or key),
                request.days_by_riot_id.get(key, request.days),
                request.matches_per_player_by_riot_id.get(
                    key,
                    request.matches_per_player,
                ),
            )
        )
    dedupe_key = f"collection:{sorted(collection_scope)!r}"
    return _jobs.create(
        kind,
        runner,
        origin=origin,
        title=title,
        target_summary=target_summary,
        dedupe_key=dedupe_key,
    )


def _set_auto_refresh_status(**changes: Any) -> None:
    with _auto_refresh_lock:
        _auto_refresh_status.update(changes)


def _collection_targets() -> list[dict[str, Any]]:
    return _get_cache().list_auto_refresh_player_memos()


def _latest_target_collection_at(targets: list[dict[str, Any]]) -> int | None:
    collected_at = [
        target["last_collected_at"] for target in targets if target["last_collected_at"]
    ]
    return max(collected_at, default=None)


def _days_since_collection(
    last_collected_at: int | None,
    *,
    now: float | None = None,
) -> int | None:
    """한 유저를 마지막으로 끝까지 수집한 뒤 지난 날짜 수를 반환한다."""
    if last_collected_at is None:
        return None
    current_time = time.time() if now is None else now
    return max(ceil((current_time - last_collected_at) / 86_400), 0)


def _collection_plan(days_since_collection: int | None) -> tuple[int, int, bool]:
    if days_since_collection is None:
        return DEFAULT_MULTI_SEARCH_DAYS, DEFAULT_MULTI_MATCHES_PER_PLAYER, False
    if days_since_collection >= AUTO_REFRESH_MAX_DAYS:
        return STALE_COLLECTION_DAYS, AUTO_REFRESH_MAX_MATCHES_PER_PLAYER, True

    days = max(days_since_collection, 1)
    matches_per_player = min(
        days * AUTO_REFRESH_MATCHES_PER_DAY,
        AUTO_REFRESH_MAX_MATCHES_PER_PLAYER,
    )
    return days, matches_per_player, False


def _plan_collection_targets(
    targets: list[dict[str, Any]],
    *,
    include_recent: bool,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """각 유저의 마지막 완료 시각으로 수집 범위를 따로 계산한다."""
    planned_targets: list[dict[str, Any]] = []
    for target in targets:
        pending_start = target.get("pending_collection_start_at")
        pending_end = target.get("pending_collection_end_at")
        has_pending_collection = pending_start is not None and pending_end is not None
        days_since_collection = _days_since_collection(
            target.get("last_collected_at"),
            now=now,
        )
        if (
            not include_recent
            and not has_pending_collection
            and days_since_collection is not None
            and days_since_collection <= AUTO_REFRESH_SKIP_DAYS
        ):
            continue

        if has_pending_collection:
            days = max(ceil((int(pending_end) - int(pending_start)) / 86_400), 1)
            matches_per_player = int(
                target.get("pending_collection_matches_per_player")
                or DEFAULT_MULTI_MATCHES_PER_PLAYER
            )
            requires_confirmation = False
        else:
            days, matches_per_player, requires_confirmation = _collection_plan(
                days_since_collection
            )
        planned_targets.append(
            {
                **target,
                "collection_days": days,
                "collection_matches_per_player": matches_per_player,
                "requires_confirmation": requires_confirmation,
            }
        )
    return planned_targets


def _collection_plan_summary(
    targets: list[dict[str, Any]],
) -> tuple[int, int, bool]:
    """사람별 계획을 기존 요약 필드에 표시할 최댓값으로 묶는다."""
    return (
        max(target["collection_days"] for target in targets),
        max(target["collection_matches_per_player"] for target in targets),
        any(target["requires_confirmation"] for target in targets),
    )


def _auto_refresh_payload(*, api_key_available: bool | None = None) -> dict[str, Any]:
    with _auto_refresh_lock:
        status = dict(_auto_refresh_status)

    targets = _collection_targets()
    status["targetCount"] = len(targets)
    status.setdefault("plannedTargetCount", 0)
    status["lastCollectedAt"] = _latest_target_collection_at(targets)

    job_id = status.get("jobId")
    job_is_active = False
    if job_id:
        job = _jobs.get(job_id)
        if job is not None:
            if job.status == "running" or job.status == "queued":
                job_is_active = True
                status["status"] = "running"
                status["message"] = job.message or status["message"]
            elif job.status in {"succeeded", "partial"}:
                status["status"] = job.status
                if job.result is not None:
                    failures = [asdict(failure) for failure in job.result.failures]
                    status["successCount"] = job.result.success_count
                    status["failureCount"] = job.result.failure_count
                    status["failures"] = failures
                    status["message"] = (
                        f"{job.result.success_count}명 완료 · "
                        f"{job.result.failure_count}명 확인 필요"
                    )
                else:
                    status["message"] = job.message or "매치 수집 완료"
            elif job.status == "failed":
                status["status"] = "failed"
                if job.result is not None:
                    status["successCount"] = job.result.success_count
                    status["failureCount"] = job.result.failure_count
                    status["failures"] = [
                        asdict(failure) for failure in job.result.failures
                    ]
                status["message"] = job.error or "매치 수집 실패"

    if not job_is_active:
        needed_targets = _plan_collection_targets(targets, include_recent=False)
        terminal_result_visible = status.get("status") in {
            "succeeded",
            "partial",
            "failed",
        }
        if needed_targets:
            days, matches_per_player, requires_confirmation = _collection_plan_summary(
                needed_targets
            )
            status["plannedTargetCount"] = len(needed_targets)
            status["days"] = days
            status["matchesPerPlayer"] = matches_per_player
            status["requiresConfirmation"] = (
                requires_confirmation and api_key_available is not False
            )
            if (
                requires_confirmation
                and api_key_available is not False
                and not terminal_result_visible
            ):
                status["status"] = "stale"
                status["message"] = (
                    f"오래 확인하지 않은 대상이 있어 {len(needed_targets)}명 수집 전 "
                    "확인이 필요합니다."
                )
            elif api_key_available is not False and status.get("status") in {
                "not_checked",
                "checking",
                "skipped",
                "stale",
            }:
                status["status"] = "ready"
                status["message"] = f"{len(needed_targets)}명의 경기 수집이 필요합니다."
        else:
            status["plannedTargetCount"] = 0
            status["requiresConfirmation"] = False
            if status.get("status") in {"not_checked", "checking", "ready", "stale"}:
                status["status"] = "skipped"
                status["message"] = (
                    "모든 자동 수집 대상이 최근 "
                    f"{AUTO_REFRESH_SKIP_DAYS}일 이내에 수집되었습니다."
                )

    return status


def _start_player_match_collection(
    *,
    targets: list[dict[str, Any]],
    days: int,
    matches_per_player: int,
    origin: str,
) -> JobRecord:
    request = MultiSearchRequest(
        riot_ids_raw="",
        days=days,
        matches_per_player=matches_per_player,
        known_targets=tuple(
            MultiSearchTarget(
                puuid=target["puuid"],
                game_name=target["riot_id_game_name"],
                tag_line=target["riot_id_tag_line"],
                days=target["collection_days"],
                matches_per_player=target["collection_matches_per_player"],
            )
            for target in targets
        ),
    )
    job = _create_multi_search_job(
        request=request,
        kind="player-match-collection",
        origin=origin,
        title=(
            "앱 시작 자동 수집" if origin == "startup-auto" else "관심 유저 지금 수집"
        ),
        target_summary=f"관심 유저 {len(targets)}명",
        success_message="매치 수집 완료",
    )
    _set_auto_refresh_status(
        status="running",
        message="매치 수집 중입니다.",
        jobId=job.job_id,
        plannedTargetCount=len(targets),
        days=days,
        matchesPerPlayer=matches_per_player,
        lastCollectedAt=_latest_target_collection_at(targets),
        requiresConfirmation=False,
        successCount=None,
        failureCount=None,
        failures=[],
    )
    return job


def _maybe_start_auto_refresh() -> None:
    """서버 프로세스 기준 1회만 메모 유저 자동갱신을 시작한다."""
    global _auto_refresh_checked

    with _auto_refresh_lock:
        if _auto_refresh_checked:
            return
        _auto_refresh_checked = True
        _auto_refresh_status.update(
            {
                "status": "checking",
                "message": "앱 시작 시 매치 수집 조건을 확인 중입니다.",
                "jobId": None,
                "targetCount": 0,
                "plannedTargetCount": 0,
                "days": None,
                "matchesPerPlayer": None,
                "lastCollectedAt": None,
                "requiresConfirmation": False,
                "successCount": None,
                "failureCount": None,
                "failures": [],
            }
        )

        try:
            _check_and_start_auto_refresh()
        except Exception:
            _auto_refresh_checked = False
            _auto_refresh_status.update(
                {
                    "status": "not_checked",
                    "message": "자동 수집 조건 확인에 실패했어요. 시작 준비를 다시 실행해 주세요.",
                }
            )
            raise


def _check_and_start_auto_refresh() -> None:
    """잠금 안에서 자동 수집 대상과 시작 조건을 끝까지 확인한다."""

    targets = _collection_targets()
    last_collected_at = _latest_target_collection_at(targets)

    if not targets:
        days, matches_per_player, _ = _collection_plan(None)
        _set_auto_refresh_status(
            status="skipped",
            message="앱 시작 시 수집이 켜진 메모 유저가 없습니다.",
            days=days,
            matchesPerPlayer=matches_per_player,
            targetCount=0,
            plannedTargetCount=0,
            lastCollectedAt=None,
        )
        return

    planned_targets = _plan_collection_targets(targets, include_recent=False)
    if not planned_targets:
        _set_auto_refresh_status(
            status="skipped",
            message=(
                "모든 자동 수집 대상이 최근 "
                f"{AUTO_REFRESH_SKIP_DAYS}일 이내에 수집되었습니다."
            ),
            days=None,
            matchesPerPlayer=None,
            targetCount=len(targets),
            plannedTargetCount=0,
            lastCollectedAt=last_collected_at,
        )
        return

    days, matches_per_player, requires_confirmation = _collection_plan_summary(
        planned_targets
    )

    if not _get_config().api_key:
        _set_auto_refresh_status(
            status="skipped",
            message="RIOT_API_KEY가 없어 시작 시 수집을 건너뜁니다.",
            days=days,
            matchesPerPlayer=matches_per_player,
            targetCount=len(planned_targets),
            plannedTargetCount=len(planned_targets),
            lastCollectedAt=last_collected_at,
        )
        return

    if requires_confirmation:
        _set_auto_refresh_status(
            status="stale",
            message="오랫동안 수집하지 않아 확인이 필요합니다.",
            days=days,
            matchesPerPlayer=matches_per_player,
            targetCount=len(planned_targets),
            plannedTargetCount=len(planned_targets),
            lastCollectedAt=last_collected_at,
            requiresConfirmation=True,
        )
        return

    _start_player_match_collection(
        targets=planned_targets,
        days=days,
        matches_per_player=matches_per_player,
        origin="startup-auto",
    )


def _job_status(job: JobRecord) -> dict[str, Any]:
    return {
        "jobId": job.job_id,
        "kind": job.kind,
        "origin": job.origin,
        "title": job.title,
        "targetSummary": job.target_summary,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "updatedAt": job.updated_at,
        "finishedAt": job.finished_at,
        "resultAvailable": (
            job.status in {"succeeded", "partial", "failed"} and job.result is not None
        ),
    }


def _payload_dict(payload: Any) -> dict[str, Any]:
    return (
        asdict(payload) if hasattr(payload, "__dataclass_fields__") else dict(payload)
    )


def _result_stat_shard_ids(row: dict[str, Any]) -> list[int | None]:
    shard_ids = [
        row.get("my_stat_offense"),
        row.get("my_stat_flex"),
        row.get("my_stat_defense"),
    ]
    if any(shard_id is not None for shard_id in shard_ids):
        return shard_ids

    match_id = row.get("match_id")
    player_puuid = row.get("player_puuid")
    if not match_id or not player_puuid:
        return shard_ids

    match = _get_cache().get_match(match_id)
    info = (match or {}).get("info") or {}
    participant = next(
        (
            player
            for player in info.get("participants") or []
            if player.get("puuid") == player_puuid
        ),
        None,
    )
    stat_perks = ((participant or {}).get("perks") or {}).get("statPerks") or {}
    return [
        stat_perks.get("offense"),
        stat_perks.get("flex"),
        stat_perks.get("defense"),
    ]


def _result_row_with_assets(row: dict[str, Any]) -> dict[str, Any]:
    static_data = _get_static_data()
    enriched = dict(row)
    stat_shard_ids = _result_stat_shard_ids(row)
    enriched["mySummoner1IconUrl"] = static_data.summoner_icon_url(
        row.get("my_summoner1_id")
    )
    enriched["mySummoner2IconUrl"] = static_data.summoner_icon_url(
        row.get("my_summoner2_id")
    )
    enriched["myPrimaryTreeIconUrl"] = static_data.tree_icon_url(
        row.get("my_primary_tree_id")
    )
    enriched["mySecondaryTreeIconUrl"] = static_data.tree_icon_url(
        row.get("my_secondary_tree_id")
    )
    enriched["myPrimaryRuneIconUrls"] = [
        static_data.rune_icon_url(rune_id)
        for rune_id in row.get("my_primary_runes", [])
    ]
    enriched["mySecondaryRuneIconUrls"] = [
        static_data.rune_icon_url(rune_id)
        for rune_id in row.get("my_secondary_runes", [])
    ]
    enriched["myStatShardIconUrls"] = [
        stat_shard_icon_url(shard_id) for shard_id in stat_shard_ids
    ]
    enriched["myStatShardNames"] = [
        stat_shard_name(shard_id) for shard_id in stat_shard_ids
    ]
    return enriched


def _payload_with_result_assets(payload: Any) -> dict[str, Any]:
    raw = _payload_dict(payload)
    if "results" in raw:
        raw["results"] = [_result_row_with_assets(row) for row in raw["results"]]
    return raw


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
    raw["results"] = [_result_row_with_assets(row) for row in raw["results"]]
    raw["page"] = page
    raw["pageSize"] = page_size
    raw["stats"] = {
        "total": raw["total"],
        "wins": raw["wins"],
        "losses": raw["losses"],
        "winRate": (
            round(raw["wins"] / raw["total"] * 100, 1) if raw["total"] else None
        ),
    }
    raw["snapshot"] = {
        "targetPuuids": raw.pop("target_puuids"),
        "startTs": raw["start_ts"],
        "endTs": raw["end_ts"],
        "patchPrefix": raw["patch_prefix"],
        "maxIndexId": raw.pop("snapshot_max_index_id"),
        "targetLabel": raw["target_label"],
    }
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
) -> dict[str, Any]:
    puuids = [player.get("puuid") for player in players if player.get("puuid")]
    if not puuids:
        return {"cache_hits": 0, "api_calls": 0, "error": None}

    missing_puuids: list[str] = []
    cache_hits = 0
    for player in players:
        puuid = player.get("puuid")
        profile = cache.get_ranked_profile(puuid, config.queue_id) if puuid else None
        if profile is None and puuid:
            missing_puuids.append(puuid)
        elif profile is not None:
            cache_hits += 1
        player["rankedProfile"] = profile
        player["rankLabel"] = (
            _rank_label(profile) if profile is not None else "확인하지 못함"
        )

    api_calls = 0
    load_error = (
        "RIOT_API_KEY가 없어 저장되지 않은 랭크는 확인하지 못했어요."
        if missing_puuids and not config.api_key
        else None
    )
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
                    except RiotApiError as exc:
                        load_error = str(exc)
                        break
                api_calls = client.request_count
        except RiotApiError as exc:
            load_error = str(exc)

        for player in players:
            puuid = player.get("puuid")
            profile = (
                cache.get_ranked_profile(puuid, config.queue_id) if puuid else None
            )
            player["rankedProfile"] = profile
            player["rankLabel"] = (
                _rank_label(profile) if profile is not None else "확인하지 못함"
            )
    return {
        "cache_hits": cache_hits,
        "api_calls": api_calls,
        "error": load_error,
    }


def _build_meta_options() -> dict[str, Any]:
    """프론트 초기 렌더링에 필요한 옵션을 반환한다."""
    config = _get_config()
    champion_data = _get_champion_data()
    static_data = _get_static_data()
    cache = _get_cache()
    match_cache_stats = cache.get_match_cache_stats()
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
        "dbPeriodOptions": DB_PERIOD_OPTIONS,
        "defaultDbPeriod": DB_DEFAULT_PERIOD_LABEL,
        "multiPeriodOptions": MULTI_PERIOD_OPTIONS,
        "multiMatchesPerPlayerOptions": MULTI_MATCHES_PER_PLAYER_OPTIONS,
        "defaultMultiDays": DEFAULT_MULTI_SEARCH_DAYS,
        "defaultMultiMatchesPerPlayer": DEFAULT_MULTI_MATCHES_PER_PLAYER,
        "allLabel": SEARCH_ALL_LABEL,
        "dbEnemyAllLabel": DB_ENEMY_ALL_LABEL,
        "dataDragonVersion": champion_data.version,
        "staticDataVersion": static_data.version,
        "apiKeyDetected": bool(config.api_key),
        "region": config.region,
        "platform": config.platform,
        "queueId": config.queue_id,
        "latestSearchRiotId": cache.get_latest_search_riot_id(),
        "dbMatchCount": match_cache_stats["match_count"],
        "dbLastMatchCreation": match_cache_stats["last_match_creation"],
        "dbIndexedMatchCount": match_cache_stats["indexed_match_count"],
        "dbIndexRowCount": match_cache_stats["indexed_row_count"],
        "dbIndexMaxId": match_cache_stats["max_index_id"],
        "dbLastIndexedMatchCreation": match_cache_stats["last_indexed_match_creation"],
        "autoRefresh": _auto_refresh_payload(
            api_key_available=bool(config.api_key),
        ),
    }


@app.post("/api/startup")
def start_application() -> dict[str, Any]:
    """앱 시작 준비와 자동 수집 조건 확인을 명시적으로 실행한다."""
    _ensure_data_dragon_checked()
    index_memo_player_matches(_get_cache(), _get_config().queue_id)
    _maybe_start_auto_refresh()
    return _build_meta_options()


@app.get("/api/meta/options")
def get_options() -> dict[str, Any]:
    """현재 화면 옵션과 로컬 저장 현황을 읽는다."""
    _require_application_started()
    return _build_meta_options()


@app.post("/api/meta/refresh")
def refresh_meta() -> dict[str, Any]:
    """Data Dragon 정적 데이터를 즉시 갱신하고 메타 옵션을 반환한다."""
    _refresh_data_dragon_resources(force_refresh=True, check_latest=True)
    return _build_meta_options()


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

    job = _jobs.create(
        "search",
        runner,
        origin="individual-search",
        title="한 명 빠른 검색",
        target_summary=body.riot_id_raw.strip(),
        dedupe_key=f"search:{body.model_dump_json(by_alias=True)}",
    )
    return {"jobId": job.job_id}


@app.post("/api/jobs/multi-search")
def create_multi_search_job(body: MultiSearchJobBody) -> dict[str, str]:
    """멀티서치 수집 job을 생성한다."""
    request = MultiSearchRequest(
        riot_ids_raw=body.riot_ids_raw,
        days=body.days,
        matches_per_player=body.matches_per_player,
    )
    target_count = len(parse_multi_riot_ids(body.riot_ids_raw))
    job = _create_multi_search_job(
        request=request,
        target_summary=f"직접 입력 {target_count}명",
    )
    return {"jobId": job.job_id}


@app.post("/api/jobs/player-match-collection")
def create_player_match_collection_job(
    body: PlayerMatchCollectionBody,
) -> dict[str, str]:
    """앱 시작 시 수집 대상의 매치를 사용자가 즉시 수집한다."""
    with _auto_refresh_lock:
        if _auto_refresh_status.get("status") == "checking":
            raise HTTPException(
                status_code=409,
                detail="앱 시작 수집 조건을 확인 중입니다. 잠시 후 다시 눌러주세요.",
            )
        active_job_id = _auto_refresh_status.get("jobId")
        if active_job_id:
            active_job = _jobs.get(active_job_id)
            if active_job is not None and active_job.status in {"queued", "running"}:
                raise HTTPException(
                    status_code=409, detail="매치 수집이 이미 진행 중입니다."
                )

        if not _get_config().api_key:
            raise HTTPException(status_code=400, detail="RIOT_API_KEY가 필요합니다.")

        targets = _collection_targets()
        if not targets:
            raise HTTPException(
                status_code=400,
                detail="앱 시작 시 수집이 켜진 메모 유저가 없습니다.",
            )

        refresh_targets = _plan_collection_targets(
            targets,
            include_recent=False,
        )
        refresh_requires_confirmation = any(
            target["requires_confirmation"] for target in refresh_targets
        )
        planned_targets = (
            refresh_targets
            if refresh_requires_confirmation
            else _plan_collection_targets(targets, include_recent=True)
        )
        days, matches_per_player, requires_confirmation = _collection_plan_summary(
            planned_targets
        )
        if requires_confirmation and not body.confirmed:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"이번에 수집이 필요한 {len(planned_targets)}명은 "
                    f"대상별 최근 최대 {days}일, "
                    f"최대 {matches_per_player}판씩 수집할까요?"
                ),
            )

        job = _start_player_match_collection(
            targets=planned_targets,
            days=days,
            matches_per_player=matches_per_player,
            origin="manual-auto",
        )
    return {"jobId": job.job_id}


@app.get("/api/jobs")
def list_jobs(
    recent_limit: int = Query(default=20, alias="recentLimit", ge=1, le=100),
) -> dict[str, Any]:
    """같은 서버에서 다시 연결할 진행 중·최근 작업을 반환한다."""
    active_jobs, recent_jobs = _jobs.list_jobs(recent_limit=recent_limit)
    return {
        "serverInstanceId": _jobs.server_instance_id,
        "serverStartedAt": _jobs.server_started_at,
        "activeJobs": [_job_status(job) for job in active_jobs],
        "recentJobs": [_job_status(job) for job in recent_jobs],
    }


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
    if job.status == "failed" and job.result is None:
        raise HTTPException(status_code=400, detail=job.error or "작업 실패")
    if job.status not in {"succeeded", "partial", "failed"}:
        raise HTTPException(status_code=409, detail="job이 아직 완료되지 않았습니다.")
    return {"kind": job.kind, "payload": _payload_with_result_assets(job.result)}


@app.post("/api/db-search")
def db_search(body: DbSearchBody) -> dict[str, Any]:
    """matchup_index 기반 DB조회를 실행한다."""
    _require_application_started()
    try:
        payload = run_indexed_matchup_search(
            request=_db_request(body, cache=_get_cache()),
            champion_data=_get_champion_data(),
            cache=_get_cache(),
        )
    except RiotApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _slice_payload(payload, body.page, body.page_size)


@app.post("/api/db-search/enemy-riot-ids")
def db_search_enemy_riot_ids(body: DbSearchBody) -> dict[str, list[str]]:
    """마지막 적용 스냅샷 전체의 상대 Riot ID를 중복 없이 반환한다."""
    _require_application_started()
    if body.snapshot is None:
        raise HTTPException(
            status_code=409,
            detail="먼저 저장된 경기를 조회한 뒤 전체 복사를 실행해 주세요.",
        )
    try:
        values = find_indexed_enemy_riot_ids(
            request=_db_request(body, cache=_get_cache(), paginate=False),
            champion_data=_get_champion_data(),
            cache=_get_cache(),
        )
    except RiotApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"items": values}


@app.get("/api/player-memos")
def list_player_memos() -> dict[str, Any]:
    """등록된 관심 유저 메모 목록을 반환한다."""
    items = _get_cache().list_player_memos()
    for item in items:
        if item.get("pending_collection_target_at") is not None:
            item["collection_status"] = "continuation_needed"
        elif item.get("last_collected_at") is None:
            item["collection_status"] = "not_collected"
        elif (
            _days_since_collection(item["last_collected_at"]) <= AUTO_REFRESH_SKIP_DAYS
        ):
            item["collection_status"] = "current"
        else:
            item["collection_status"] = "needs_collection"
    return {"items": items}


@app.post("/api/player-memos/resolve-riot-id")
def resolve_player_memo_riot_id(body: ResolveRiotIdBody) -> dict[str, Any]:
    """Riot ID를 메모 저장용 PUUID로 변환한다."""
    try:
        return _resolve_player_memo_target(body)
    except RiotApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/player-memos/{puuid}")
def upsert_player_memo(puuid: str, body: PlayerMemoBody) -> dict[str, bool]:
    """관심 유저 메모를 저장한다."""
    _get_cache().upsert_player_memo(puuid, body.memo)
    index_memo_player_matches(_get_cache(), _get_config().queue_id, puuid)
    return {"ok": True}


@app.patch("/api/player-memos/{puuid}/auto-refresh")
def update_player_memo_auto_refresh(
    puuid: str,
    body: PlayerMemoAutoRefreshBody,
) -> dict[str, bool]:
    """관심 유저 자동갱신 여부를 저장한다."""
    _get_cache().set_player_memo_auto_refresh(puuid, body.enabled)
    return {"ok": True}


@app.delete("/api/player-memos/{puuid}")
def delete_player_memo(puuid: str) -> dict[str, bool]:
    """관심 유저 메모를 삭제한다."""
    _get_cache().delete_player_memo(puuid)
    return {"ok": True}


@app.get("/api/matches/{match_id}/detail")
def get_match_detail(
    match_id: str,
    player_puuid: str = Query(alias="playerPuuid"),
) -> dict[str, Any]:
    """외부 호출 없이 저장된 매치 상세의 기본 정보를 반환한다."""
    _require_application_started()
    _cache, _match, focus = _load_match_focus(match_id, player_puuid)
    me, enemy_laner, others_ally, others_enemy = _focus_players_with_assets(focus)
    return {
        "queueId": focus.get("queue_id"),
        "gameDuration": focus.get("game_duration"),
        "gameVersion": focus.get("game_version"),
        "me": me,
        "enemyLaner": enemy_laner,
        "othersAlly": others_ally,
        "othersEnemy": others_enemy,
        "buildTimeline": None,
        "timelineError": None,
        "detailLoad": {
            "timelineSource": "not_requested",
            "rankCacheHits": 0,
            "externalApiCalls": 0,
            "rankError": None,
        },
    }


def _load_match_focus(
    match_id: str,
    player_puuid: str,
) -> tuple[MatchCache, dict[str, Any], dict[str, Any]]:
    """저장된 경기와 기준 플레이어 관점을 함께 확인한다."""
    cache = _get_cache()
    match = cache.get_match(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="캐시에서 매치를 찾을 수 없습니다.")

    focus = extract_focus_view(match, player_puuid)
    if focus is None:
        raise HTTPException(status_code=404, detail="기준 플레이어를 찾을 수 없습니다.")
    return cache, match, focus


def _focus_players_with_assets(
    focus: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """상세 화면 참가자에게 로컬 정적 이미지 정보를 붙인다."""
    me = _participant_with_assets(focus.get("me"))
    if me is None:
        raise HTTPException(status_code=404, detail="기준 플레이어를 찾을 수 없습니다.")
    enemy_laner = _participant_with_assets(focus.get("enemy_laner"))
    others_ally = [
        _summary_with_assets(player) for player in focus.get("others_ally") or []
    ]
    others_enemy = [
        _summary_with_assets(player) for player in focus.get("others_enemy") or []
    ]
    return me, enemy_laner, others_ally, others_enemy


@app.get("/api/matches/{match_id}/detail/build")
def get_match_build_detail(
    match_id: str,
    player_puuid: str = Query(alias="playerPuuid"),
) -> dict[str, Any]:
    """빌드 탭을 열었을 때만 타임라인을 확인한다."""
    _require_application_started()
    cache, match, focus = _load_match_focus(match_id, player_puuid)

    timeline = cache.get_match_timeline(match_id)
    timeline_source = "stored" if timeline is not None else "unavailable"
    timeline_api_calls = 0
    config = _get_config()
    timeline_error = (
        None
        if timeline is not None or config.api_key
        else "RIOT_API_KEY가 없어 저장되지 않은 타임라인은 가져올 수 없어요."
    )
    if timeline is None and config.api_key:
        try:
            with RiotClient(
                api_key=config.api_key,
                region=config.region,
                platform=config.platform,
            ) as client:
                try:
                    timeline = fetch_match_timeline(client, cache, match_id)
                finally:
                    timeline_api_calls = client.request_count
                if timeline is not None:
                    timeline_source = "downloaded"
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

    return {
        "buildTimeline": build_timeline,
        "timelineError": timeline_error,
        "detailLoad": {
            "timelineSource": timeline_source,
            "externalApiCalls": timeline_api_calls,
        },
    }


@app.get("/api/matches/{match_id}/detail/ranks")
def get_match_rank_detail(
    match_id: str,
    player_puuid: str = Query(alias="playerPuuid"),
) -> dict[str, Any]:
    """종합 탭을 열었을 때만 참가자 랭크를 확인한다."""
    _require_application_started()
    cache, _match, focus = _load_match_focus(match_id, player_puuid)
    me, enemy_laner, others_ally, others_enemy = _focus_players_with_assets(focus)
    players_for_rank = [
        player
        for player in [me, enemy_laner, *others_ally, *others_enemy]
        if player is not None
    ]
    rank_load = _attach_rank_profiles(
        players_for_rank,
        cache=cache,
        config=_get_config(),
    )

    return {
        "me": me,
        "enemyLaner": enemy_laner,
        "othersAlly": others_ally,
        "othersEnemy": others_enemy,
        "detailLoad": {
            "rankCacheHits": rank_load["cache_hits"],
            "externalApiCalls": rank_load["api_calls"],
            "rankError": rank_load["error"],
        },
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
    _require_application_started()
    if body.snapshot is None:
        raise HTTPException(
            status_code=409,
            detail="먼저 저장된 경기를 조회한 뒤 CSV를 내려받아 주세요.",
        )
    try:
        payload = run_indexed_matchup_search(
            request=_db_request(body, cache=_get_cache(), paginate=False),
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
