"""Match-V5 타임라인에서 검색 유저의 빌드 정보를 추출한다."""

from __future__ import annotations

from typing import Any


SKILL_SLOT_LABELS: dict[int, str] = {
    1: "Q",
    2: "W",
    3: "E",
    4: "R",
}


def extract_player_build_timeline(
    match: dict[str, Any],
    timeline: dict[str, Any],
    target_puuid: str,
) -> dict[str, Any] | None:
    """검색 유저의 아이템 구매 순서와 스킬 레벨업 순서를 추출한다."""
    participant_id = _find_participant_id(match, timeline, target_puuid)
    if participant_id is None:
        return None

    item_events: list[dict[str, Any]] = []
    skill_events: list[dict[str, Any]] = []

    for event in _iter_events(timeline):
        if event.get("participantId") != participant_id:
            continue

        event_type = event.get("type")
        timestamp = int(event.get("timestamp") or 0)
        if event_type == "ITEM_PURCHASED":
            item_id = event.get("itemId")
            if item_id:
                item_events.append(
                    {
                        "item_id": int(item_id),
                        "timestamp": timestamp,
                        "minute": timestamp // 60000,
                    }
                )
        elif event_type == "SKILL_LEVEL_UP":
            skill_slot = event.get("skillSlot")
            if skill_slot in SKILL_SLOT_LABELS:
                skill_events.append(
                    {
                        "skill_slot": int(skill_slot),
                        "label": SKILL_SLOT_LABELS[int(skill_slot)],
                        "timestamp": timestamp,
                        "level": len(skill_events) + 1,
                    }
                )

    return {
        "participant_id": participant_id,
        "item_events": item_events,
        "skill_events": skill_events,
    }


def _find_participant_id(
    match: dict[str, Any],
    timeline: dict[str, Any],
    target_puuid: str,
) -> int | None:
    info = match.get("info") or {}
    for participant in info.get("participants") or []:
        if participant.get("puuid") == target_puuid:
            participant_id = participant.get("participantId")
            if isinstance(participant_id, int):
                return participant_id

    timeline_info = timeline.get("info") or {}
    for participant in timeline_info.get("participants") or []:
        if participant.get("puuid") == target_puuid:
            participant_id = participant.get("participantId")
            if isinstance(participant_id, int):
                return participant_id

    return None


def _iter_events(timeline: dict[str, Any]):
    info = timeline.get("info") or {}
    for frame in info.get("frames") or []:
        for event in frame.get("events") or []:
            yield event
