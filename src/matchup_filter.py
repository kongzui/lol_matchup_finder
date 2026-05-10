"""매치 상세 데이터에서 조건에 맞는 매치업을 추출한다.

조건:
- 내 PUUID == target_puuid
- 내 championName == my_champion_key (영문 키)
- 내 teamPosition == lane (예: "MIDDLE")
- 상대팀 같은 라인의 챔피언 == enemy_champion_key
"""

from __future__ import annotations

from typing import Any

from .utils import format_riot_id, unix_to_kst_date_str


def extract_matchup_result(
    match: dict[str, Any],
    target_puuid: str,
    my_champion_key: str,
    enemy_champion_key: str,
    lane: str,
) -> dict[str, Any] | None:
    """조건에 맞으면 결과 row를, 아니면 None을 반환한다."""
    if not isinstance(match, dict):
        return None

    info = match.get("info") or {}
    metadata = match.get("metadata") or {}
    participants = info.get("participants") or []

    me = next(
        (p for p in participants if p.get("puuid") == target_puuid),
        None,
    )
    if me is None:
        return None

    if me.get("championName") != my_champion_key:
        return None

    if me.get("teamPosition") != lane:
        return None

    enemy_laner = next(
        (
            p
            for p in participants
            if p.get("teamId") != me.get("teamId") and p.get("teamPosition") == lane
        ),
        None,
    )
    if enemy_laner is None:
        return None

    if enemy_laner.get("championName") != enemy_champion_key:
        return None

    enemy_game_name = enemy_laner.get("riotIdGameName")
    enemy_tag_line = enemy_laner.get("riotIdTagline")
    enemy_riot_id = format_riot_id(enemy_game_name, enemy_tag_line)
    if enemy_riot_id is None:
        # Riot ID가 비어 있으면 summonerName을 fallback으로 사용한다.
        enemy_riot_id = enemy_laner.get("summonerName") or "Unknown"

    cs = (me.get("totalMinionsKilled") or 0) + (me.get("neutralMinionsKilled") or 0)

    return {
        "match_id": metadata.get("matchId"),
        "game_creation": info.get("gameCreation"),
        "game_date": (
            unix_to_kst_date_str(info.get("gameCreation"))
            if info.get("gameCreation")
            else ""
        ),
        "queue_id": info.get("queueId"),
        "my_champion_key": me.get("championName"),
        "enemy_champion_key": enemy_laner.get("championName"),
        "enemy_riot_id": enemy_riot_id,
        "enemy_game_name": enemy_game_name,
        "enemy_tag_line": enemy_tag_line,
        "win": bool(me.get("win")),
        "kills": int(me.get("kills") or 0),
        "deaths": int(me.get("deaths") or 0),
        "assists": int(me.get("assists") or 0),
        "cs": int(cs),
        "gold_earned": int(me.get("goldEarned") or 0),
        "damage_to_champions": int(me.get("totalDamageDealtToChampions") or 0),
        "game_duration": int(info.get("gameDuration") or 0),
        "game_version": info.get("gameVersion"),
    }
