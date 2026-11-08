"""매치 상세 데이터에서 조건에 맞는 매치업을 추출한다.

조건:
- 내 PUUID == target_puuid
- my_champion_key가 있으면 내 championName == my_champion_key (영문 키)
- 내 teamPosition == lane (예: "MIDDLE")
- enemy_champion_key가 있으면 상대팀 같은 라인의 챔피언 == enemy_champion_key
"""

from __future__ import annotations

from typing import Any


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
    """나머지 팀원도 종합 표에서 쓸 수 있도록 상세 필드를 반환한다."""
    return _participant_detail(p)


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
