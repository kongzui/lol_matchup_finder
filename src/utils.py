"""공통 유틸리티: Riot ID 파싱, 기간 변환, 라인 매핑."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


# 한글 라인명 → Match-V5 teamPosition 값
LANE_LABEL_TO_TEAM_POSITION: dict[str, str] = {
    "탑": "TOP",
    "정글": "JUNGLE",
    "미드": "MIDDLE",
    "원딜": "BOTTOM",
    "서폿": "UTILITY",
}

# UI 드롭다운 표시 순서
LANE_LABELS: list[str] = ["탑", "정글", "미드", "원딜", "서폿"]


class RiotIdParseError(ValueError):
    """Riot ID 형식 오류."""


def parse_riot_id(raw: str) -> tuple[str, str]:
    """'gameName#tagLine' 문자열을 분리한다.

    공백을 트리밍하고, '#'이 정확히 한 번 등장하지 않으면 오류를 발생시킨다.
    """
    if raw is None:
        raise RiotIdParseError("Riot ID가 비어 있습니다.")

    text = raw.strip()
    if not text:
        raise RiotIdParseError("Riot ID가 비어 있습니다.")

    if text.count("#") != 1:
        raise RiotIdParseError(
            "Riot ID는 '게임이름#태그' 형식이어야 합니다. 예: Hide on bush#KR1"
        )

    game_name, tag_line = text.split("#", 1)
    game_name = game_name.strip()
    tag_line = tag_line.strip()

    if not game_name or not tag_line:
        raise RiotIdParseError(
            "Riot ID의 게임 이름과 태그가 모두 필요합니다. 예: Hide on bush#KR1"
        )

    return game_name, tag_line


def format_riot_id(game_name: str | None, tag_line: str | None) -> str | None:
    """게임 이름과 태그를 'gameName#tagLine' 형식으로 합친다.

    값 중 하나라도 비어 있으면 None을 반환한다.
    """
    if not game_name or not tag_line:
        return None
    return f"{game_name}#{tag_line}"


def days_ago_to_unix_range(days: int, now: float | None = None) -> tuple[int, int]:
    """현재 시각으로부터 N일 전 ~ 현재까지의 unix 초를 반환한다."""
    end_ts = int(now if now is not None else time.time())
    start_ts = end_ts - days * 24 * 60 * 60
    return start_ts, end_ts


def date_range_to_unix(start_date: datetime, end_date: datetime) -> tuple[int, int]:
    """date(또는 datetime) 범위를 unix 초 [시작, 끝)으로 변환한다.

    - 시작일 00:00:00 KST부터 종료일+1 00:00:00 KST 직전까지 포함한다.
    - tz 정보가 없는 입력은 KST(UTC+9)로 간주한다.
    """
    kst = timezone(timedelta(hours=9))

    if start_date.tzinfo is None:
        start_dt = datetime(
            start_date.year, start_date.month, start_date.day, tzinfo=kst
        )
    else:
        start_dt = start_date.astimezone(kst)
        start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=kst)

    if end_date.tzinfo is None:
        end_base = datetime(end_date.year, end_date.month, end_date.day, tzinfo=kst)
    else:
        end_base = end_date.astimezone(kst)
        end_base = datetime(end_base.year, end_base.month, end_base.day, tzinfo=kst)

    end_dt = end_base + timedelta(days=1)

    return int(start_dt.timestamp()), int(end_dt.timestamp())


def unix_to_kst_date_str(unix_ms: int) -> str:
    """Match-V5의 gameCreation(밀리초)를 KST 'YYYY-MM-DD'로 변환한다."""
    kst = timezone(timedelta(hours=9))
    dt = datetime.fromtimestamp(unix_ms / 1000, tz=kst)
    return dt.strftime("%Y-%m-%d")


def unix_to_kst_datetime_str(unix_ms: int) -> str:
    """Match-V5의 gameCreation(밀리초)를 KST 'YYYY-MM-DD HH:MM'로 변환한다."""
    kst = timezone(timedelta(hours=9))
    dt = datetime.fromtimestamp(unix_ms / 1000, tz=kst)
    return dt.strftime("%Y-%m-%d %H:%M")
