"""애플리케이션 환경 설정 로딩."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    """앱 실행에 필요한 환경 설정."""

    api_key: str
    region: str
    platform: str
    queue_id: int
    db_path: str


def load_config() -> AppConfig:
    """`.env`와 환경 변수에서 앱 설정을 읽는다."""
    load_dotenv()
    return AppConfig(
        api_key=os.environ.get("RIOT_API_KEY", "").strip(),
        region=os.environ.get("RIOT_REGION", "asia").strip() or "asia",
        platform=os.environ.get("RIOT_PLATFORM", "kr").strip() or "kr",
        queue_id=int(os.environ.get("DEFAULT_QUEUE_ID", "420")),
        db_path=os.environ.get("CACHE_DB_PATH", "data/matchup_finder.db"),
    )
