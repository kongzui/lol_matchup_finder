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


def _participant_detail(p: dict[str, Any]) -> dict[str, Any]:
    """매치 상세 패널에서 라이너 한 명을 그릴 때 필요한 필드만 추려서 반환한다."""
    perks = p.get("perks") or {}
    styles = perks.get("styles") or []
    primary = styles[0] if len(styles) > 0 else {}
    secondary = styles[1] if len(styles) > 1 else {}

    def _selection_ids(style: dict[str, Any]) -> list[int]:
        return [
            int(sel.get("perk"))
            for sel in (style.get("selections") or [])
            if sel.get("perk") is not None
        ]

    stat_perks = perks.get("statPerks") or {}

    return {
        "puuid": p.get("puuid"),
        "team_id": p.get("teamId"),
        "team_position": p.get("teamPosition"),
        "champion_key": p.get("championName"),
        "champion_level": int(p.get("champLevel") or 0),
        "summoner1_id": p.get("summoner1Id"),
        "summoner2_id": p.get("summoner2Id"),
        "items": [int(p.get(f"item{i}") or 0) for i in range(7)],
        "riot_id_game_name": p.get("riotIdGameName"),
        "riot_id_tag_line": p.get("riotIdTagline"),
        "summoner_name": p.get("summonerName"),
        "win": bool(p.get("win")),
        "kills": int(p.get("kills") or 0),
        "deaths": int(p.get("deaths") or 0),
        "assists": int(p.get("assists") or 0),
        "cs": int(
            (p.get("totalMinionsKilled") or 0) + (p.get("neutralMinionsKilled") or 0)
        ),
        "gold": int(p.get("goldEarned") or 0),
        "damage": int(p.get("totalDamageDealtToChampions") or 0),
        "vision": int(p.get("visionScore") or 0),
        "primary_tree_id": primary.get("style"),
        "primary_runes": _selection_ids(primary),
        "secondary_tree_id": secondary.get("style"),
        "secondary_runes": _selection_ids(secondary),
        "stat_offense": stat_perks.get("offense"),
        "stat_flex": stat_perks.get("flex"),
        "stat_defense": stat_perks.get("defense"),
    }


def _participant_summary(p: dict[str, Any]) -> dict[str, Any]:
    """나머지 8명용 요약 (챔피언 + KDA + 닉네임)."""
    return {
        "team_id": p.get("teamId"),
        "team_position": p.get("teamPosition"),
        "champion_key": p.get("championName"),
        "riot_id_game_name": p.get("riotIdGameName"),
        "riot_id_tag_line": p.get("riotIdTagline"),
        "summoner_name": p.get("summonerName"),
        "kills": int(p.get("kills") or 0),
        "deaths": int(p.get("deaths") or 0),
        "assists": int(p.get("assists") or 0),
    }


def extract_focus_view(
    match: dict[str, Any],
    target_puuid: str,
) -> dict[str, Any] | None:
    """매치 상세에서 '나 + 상대 라이너 상세' + '나머지 8명 요약' 을 추출한다.

    Focus 패널 렌더링에 필요한 모든 데이터를 한 번에 묶어서 돌려준다.
    조건에 맞는 me/enemy_laner 를 찾지 못하면 None.
    """
    if not isinstance(match, dict):
        return None

    info = match.get("info") or {}
    participants = info.get("participants") or []

    me = next((p for p in participants if p.get("puuid") == target_puuid), None)
    if me is None:
        return None

    my_lane = me.get("teamPosition")
    my_team = me.get("teamId")
    enemy_laner = next(
        (
            p
            for p in participants
            if p.get("teamId") != my_team and p.get("teamPosition") == my_lane
        ),
        None,
    )

    # 같은 팀에서 나를 제외한 4명 + 적팀에서 enemy_laner 를 제외한 4명.
    me_puuid = me.get("puuid")
    enemy_puuid = enemy_laner.get("puuid") if enemy_laner else None
    others_ally = [
        _participant_summary(p)
        for p in participants
        if p.get("teamId") == my_team and p.get("puuid") != me_puuid
    ]
    others_enemy = [
        _participant_summary(p)
        for p in participants
        if p.get("teamId") != my_team and p.get("puuid") != enemy_puuid
    ]

    return {
        "queue_id": info.get("queueId"),
        "game_duration": int(info.get("gameDuration") or 0),
        "game_version": info.get("gameVersion"),
        "me": _participant_detail(me),
        "enemy_laner": _participant_detail(enemy_laner) if enemy_laner else None,
        "others_ally": others_ally,
        "others_enemy": others_enemy,
    }


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
    me_detail = _participant_detail(me)

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
        "my_champion_level": me_detail["champion_level"],
        "my_summoner1_id": me_detail["summoner1_id"],
        "my_summoner2_id": me_detail["summoner2_id"],
        "my_items": me_detail["items"],
        "my_primary_tree_id": me_detail["primary_tree_id"],
        "my_primary_runes": me_detail["primary_runes"],
        "my_secondary_tree_id": me_detail["secondary_tree_id"],
        "my_secondary_runes": me_detail["secondary_runes"],
    }


def extract_challenger_matchup_results(
    match: dict[str, Any],
    target_players: dict[str, dict[str, Any]],
    my_champion_key: str,
    enemy_champion_key: str | None,
    lane: str,
    patch_prefix: str | None,
) -> list[dict[str, Any]]:
    """챌린저 대상자 중 조건에 맞는 매치업 결과를 반환한다."""
    if not isinstance(match, dict):
        return []

    info = match.get("info") or {}
    metadata = match.get("metadata") or {}
    participants = info.get("participants") or []
    game_version = str(info.get("gameVersion") or "")

    if patch_prefix and not game_version.startswith(f"{patch_prefix}."):
        return []

    results: list[dict[str, Any]] = []
    for me in participants:
        player_puuid = me.get("puuid")
        player_meta = target_players.get(player_puuid)
        if player_meta is None:
            continue

        if me.get("championName") != my_champion_key:
            continue

        if me.get("teamPosition") != lane:
            continue

        enemy_laner = next(
            (
                p
                for p in participants
                if p.get("teamId") != me.get("teamId") and p.get("teamPosition") == lane
            ),
            None,
        )
        if enemy_laner is None:
            continue

        if enemy_champion_key and enemy_laner.get("championName") != enemy_champion_key:
            continue

        enemy_game_name = enemy_laner.get("riotIdGameName")
        enemy_tag_line = enemy_laner.get("riotIdTagline")
        enemy_riot_id = format_riot_id(enemy_game_name, enemy_tag_line)
        if enemy_riot_id is None:
            enemy_riot_id = enemy_laner.get("summonerName") or "Unknown"

        player_game_name = me.get("riotIdGameName") or player_meta.get("game_name")
        player_tag_line = me.get("riotIdTagline") or player_meta.get("tag_line")
        player_riot_id = format_riot_id(player_game_name, player_tag_line)
        if player_riot_id is None:
            player_riot_id = me.get("summonerName") or player_meta.get("fallback_name")
            player_riot_id = player_riot_id or "Unknown"

        cs = (me.get("totalMinionsKilled") or 0) + (me.get("neutralMinionsKilled") or 0)
        me_detail = _participant_detail(me)

        results.append(
            {
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
                "player_riot_id": player_riot_id,
                "player_game_name": player_game_name,
                "player_tag_line": player_tag_line,
                "player_puuid": player_puuid,
                "player_rank": player_meta.get("rank"),
                "player_league_points": player_meta.get("league_points"),
                "win": bool(me.get("win")),
                "kills": int(me.get("kills") or 0),
                "deaths": int(me.get("deaths") or 0),
                "assists": int(me.get("assists") or 0),
                "cs": int(cs),
                "gold_earned": int(me.get("goldEarned") or 0),
                "damage_to_champions": int(me.get("totalDamageDealtToChampions") or 0),
                "game_duration": int(info.get("gameDuration") or 0),
                "game_version": game_version,
                "my_champion_level": me_detail["champion_level"],
                "my_summoner1_id": me_detail["summoner1_id"],
                "my_summoner2_id": me_detail["summoner2_id"],
                "my_items": me_detail["items"],
                "my_primary_tree_id": me_detail["primary_tree_id"],
                "my_primary_runes": me_detail["primary_runes"],
                "my_secondary_tree_id": me_detail["secondary_tree_id"],
                "my_secondary_runes": me_detail["secondary_runes"],
            }
        )

    return results
