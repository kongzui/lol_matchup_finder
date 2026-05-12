"""챌린저 랭킹 기반 매치업 검색 서비스."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .cache import MatchCache
from .champions import ChampionData
from .config import AppConfig
from .matchup_filter import extract_challenger_matchup_results
from .riot_client import RiotApiError, RiotApiNotFound, RiotClient
from .search_service import fetch_all_match_ids, fetch_match_detail
from .utils import LANE_LABEL_TO_TEAM_POSITION, days_ago_to_unix_range, format_riot_id


DEFAULT_CHALLENGER_TOP_N = 300
DEFAULT_CHALLENGER_DAYS = 7
DEFAULT_MATCHES_PER_PLAYER = 50
CHALLENGER_QUEUE = "RANKED_SOLO_5x5"

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class ChallengerSearchRequest:
    """챌린저 매치업 검색 조건."""

    my_champion_korean: str
    enemy_champion_korean: str
    lane_label: str
    top_n: int
    days: int
    matches_per_player: int
    current_patch_only: bool


@dataclass(frozen=True)
class ChallengerSearchPayload:
    """챌린저 검색 결과와 렌더링에 필요한 부가 정보."""

    results: list[dict[str, Any]]
    scanned_players: int
    scanned_matches: int
    cache_hits: int
    api_calls: int
    my_champion_key: str
    enemy_champion_key: str | None
    lane_label: str
    period_label: str
    top_n: int
    matches_per_player: int
    patch_prefix: str | None
    start_ts: int
    end_ts: int


def _patch_prefix(version: str) -> str | None:
    """Data Dragon 버전에서 major.minor 패치 prefix를 추출한다."""
    parts = version.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:2])


def _sort_challenger_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """챌린저 엔트리를 래더 표시용 순서로 정렬한다."""
    return sorted(
        entries,
        key=lambda row: (
            -int(row.get("leaguePoints") or 0),
            -int(row.get("wins") or 0),
            int(row.get("losses") or 0),
        ),
    )


def _resolve_summoner_puuid(
    client: RiotClient,
    cache: MatchCache,
    summoner_id: str,
) -> tuple[str | None, bool]:
    """summonerId로 PUUID를 조회하고 캐시 히트 여부를 함께 반환한다."""
    cached = cache.get_summoner_puuid(summoner_id)
    if cached:
        return cached, True

    try:
        summoner = client.get_summoner_by_id(summoner_id)
    except RiotApiNotFound:
        return None, False

    puuid = summoner.get("puuid")
    if not puuid:
        return None, False

    cache.save_summoner_puuid(summoner_id, puuid)
    return puuid, False


def _fill_player_riot_ids(
    client: RiotClient,
    rows: list[dict[str, Any]],
) -> int:
    """결과 row의 플레이어 Riot ID가 비어 있으면 PUUID로 보강한다."""
    api_calls = 0
    resolved: dict[str, dict[str, str]] = {}

    for row in rows:
        player_riot_id = str(row.get("player_riot_id") or "")
        if "#" in player_riot_id:
            continue

        puuid = row.get("player_puuid")
        if not puuid:
            continue

        account = resolved.get(puuid)
        if account is None:
            try:
                account = client.get_account_by_puuid(puuid)
            except RiotApiError:
                continue
            resolved[puuid] = account
            api_calls += 1
            time.sleep(0.05)

        game_name = account.get("gameName")
        tag_line = account.get("tagLine")
        riot_id = format_riot_id(game_name, tag_line)
        if riot_id:
            row["player_game_name"] = game_name
            row["player_tag_line"] = tag_line
            row["player_riot_id"] = riot_id

    return api_calls


def run_challenger_search(
    *,
    config: AppConfig,
    request: ChallengerSearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
    progress_cb: ProgressCallback,
    status_cb: StatusCallback,
) -> ChallengerSearchPayload:
    """챌린저 상위권의 최근 경기에서 조건에 맞는 매치업을 찾는다."""
    if not config.api_key:
        raise RiotApiError(
            "RIOT_API_KEY가 설정되어 있지 않습니다. .env 파일에 Riot API Key를 넣어주세요."
        )

    my_champion_key = champion_data.to_english_key(request.my_champion_korean)
    if not my_champion_key:
        raise RiotApiError("선택한 내 챔피언을 인식하지 못했습니다.")

    enemy_champion_key = None
    if request.enemy_champion_korean != "전체":
        enemy_champion_key = champion_data.to_english_key(request.enemy_champion_korean)
        if not enemy_champion_key:
            raise RiotApiError("선택한 상대 챔피언을 인식하지 못했습니다.")

    lane_value = LANE_LABEL_TO_TEAM_POSITION[request.lane_label]
    start_ts, end_ts = days_ago_to_unix_range(request.days)
    patch_prefix = (
        _patch_prefix(champion_data.version) if request.current_patch_only else None
    )

    cache_hits = 0
    api_calls = 0

    status_cb("챌린저 랭킹을 불러오는 중...")
    with RiotClient(
        api_key=config.api_key,
        region=config.region,
        platform=config.platform,
    ) as client:
        league = client.get_challenger_league(CHALLENGER_QUEUE)
        api_calls += 1
        entries = league.get("entries") or []
        if not isinstance(entries, list):
            raise RiotApiError("챌린저 랭킹 응답이 비정상입니다.")

        ranked_entries = _sort_challenger_entries(entries)[: request.top_n]
        target_players: dict[str, dict[str, Any]] = {}
        match_ids: list[str] = []
        seen_match_ids: set[str] = set()

        total_players = len(ranked_entries)
        for idx, entry in enumerate(ranked_entries, start=1):
            rank_progress_base = (idx - 1) / max(total_players, 1)
            progress_cb(rank_progress_base * 0.45)
            status_cb(f"챌린저 PUUID/최근 경기 수집 중... ({idx}/{total_players})")

            puuid = entry.get("puuid")
            if not puuid:
                summoner_id = entry.get("summonerId")
                if not summoner_id:
                    continue

                puuid, hit = _resolve_summoner_puuid(client, cache, summoner_id)
                if hit:
                    cache_hits += 1
                else:
                    api_calls += 1
                    time.sleep(0.05)

            if not puuid:
                continue

            target_players[puuid] = {
                "rank": idx,
                "league_points": int(entry.get("leaguePoints") or 0),
                "fallback_name": entry.get("summonerName"),
            }

            ids = fetch_all_match_ids(
                client=client,
                puuid=puuid,
                queue_id=config.queue_id,
                start_ts=start_ts,
                end_ts=end_ts,
                max_total=request.matches_per_player,
            )
            api_calls += 1
            time.sleep(0.05)

            for match_id in ids:
                if match_id in seen_match_ids:
                    continue
                seen_match_ids.add(match_id)
                match_ids.append(match_id)

        results: list[dict[str, Any]] = []
        total_matches = len(match_ids)
        for idx, match_id in enumerate(match_ids, start=1):
            progress_cb(0.45 + (idx / max(total_matches, 1)) * 0.5)
            status_cb(f"중복 제거된 매치 분석 중... ({idx}/{total_matches})")

            cached_before = cache.get_match(match_id) is not None
            match = fetch_match_detail(client, cache, match_id)
            if match is None:
                continue

            if cached_before:
                cache_hits += 1
            else:
                api_calls += 1
                time.sleep(0.05)

            results.extend(
                extract_challenger_matchup_results(
                    match=match,
                    target_players=target_players,
                    my_champion_key=my_champion_key,
                    enemy_champion_key=enemy_champion_key,
                    lane=lane_value,
                    patch_prefix=patch_prefix,
                )
            )

        if results:
            status_cb("결과 Riot ID를 정리하는 중...")
            api_calls += _fill_player_riot_ids(client, results)

    results.sort(key=lambda row: row.get("game_creation") or 0, reverse=True)
    progress_cb(1.0)

    return ChallengerSearchPayload(
        results=results,
        scanned_players=len(target_players),
        scanned_matches=len(match_ids),
        cache_hits=cache_hits,
        api_calls=api_calls,
        my_champion_key=my_champion_key,
        enemy_champion_key=enemy_champion_key,
        lane_label=request.lane_label,
        period_label=f"최근 {request.days}일",
        top_n=request.top_n,
        matches_per_player=request.matches_per_player,
        patch_prefix=patch_prefix,
        start_ts=start_ts,
        end_ts=end_ts,
    )
