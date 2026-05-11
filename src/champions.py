"""Data Dragon에서 한글 챔피언 목록을 가져와 캐시한다.

- 영문 키(`Ahri`)와 한글 이름(`아리`)의 양방향 매핑을 제공한다.
- Match-V5의 `championName`과 비교할 때는 영문 키를 그대로 사용한다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import httpx


VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
CHAMPION_URL_TEMPLATE = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/champion.json"
)

# 7일이 지나면 패치 버전을 다시 확인한다.
VERSION_CHECK_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ChampionData:
    """챔피언 목록 + 양방향 매핑."""

    version: str
    # 한글 이름 정렬 리스트 (UI 드롭다운 표시용)
    korean_names: list[str]
    # 한글 이름 → 영문 키 (예: "아리" → "Ahri")
    ko_to_en: dict[str, str]
    # 영문 키 → 한글 이름 (예: "Ahri" → "아리")
    en_to_ko: dict[str, str]

    def to_english_key(self, korean_name: str) -> str | None:
        return self.ko_to_en.get(korean_name)

    def to_korean_name(self, english_key: str) -> str:
        return self.en_to_ko.get(english_key, english_key)


def champion_icon_url(version: str, champion_key: str) -> str:
    """Data Dragon 챔피언 정사각형 아이콘 URL."""
    return (
        f"https://ddragon.leagueoflegends.com/cdn/{version}"
        f"/img/champion/{champion_key}.png"
    )


class ChampionRepository:
    """Data Dragon 챔피언 목록을 조회하고 SQLite에 캐시한다."""

    def __init__(self, db_path: str, http_timeout: float = 10.0):
        self._db_path = db_path
        self._timeout = http_timeout
        self._ensure_parent_dir()
        self._ensure_table()

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS champion_cache (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    # --- 외부 API ---
    def load(self, force_refresh: bool = False) -> ChampionData:
        """캐시가 유효하면 캐시를, 아니면 Data Dragon에서 새로 받아 반환한다."""
        cached = self._read_cache()
        now = int(time.time())

        if not force_refresh and cached is not None:
            cache_age = now - cached["fetched_at"]
            if cache_age < VERSION_CHECK_TTL_SECONDS:
                return self._build_data(cached["version"], cached["payload_json"])

        try:
            latest_version = self._fetch_latest_version()
        except httpx.HTTPError:
            # 네트워크 실패 시 캐시가 있다면 캐시로 폴백한다.
            if cached is not None:
                return self._build_data(cached["version"], cached["payload_json"])
            raise

        if (
            not force_refresh
            and cached is not None
            and cached["version"] == latest_version
        ):
            # 버전은 같지만 TTL만 지난 경우: 페이로드 재사용 후 fetched_at만 갱신한다.
            self._touch_cache(latest_version, cached["payload_json"])
            return self._build_data(latest_version, cached["payload_json"])

        payload_json = self._fetch_champion_payload(latest_version)
        self._write_cache(latest_version, payload_json)
        return self._build_data(latest_version, payload_json)

    # --- 내부 ---
    def _read_cache(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version, payload_json, fetched_at FROM champion_cache WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "payload_json": row["payload_json"],
            "fetched_at": row["fetched_at"],
        }

    def _write_cache(self, version: str, payload_json: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO champion_cache (id, version, payload_json, fetched_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    version = excluded.version,
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (version, payload_json, now),
            )
            conn.commit()

    def _touch_cache(self, version: str, payload_json: str) -> None:
        self._write_cache(version, payload_json)

    def _fetch_latest_version(self) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(VERSIONS_URL)
            resp.raise_for_status()
            versions = resp.json()
        if not isinstance(versions, list) or not versions:
            raise RuntimeError("Data Dragon 버전 목록이 비어 있습니다.")
        return versions[0]

    def _fetch_champion_payload(self, version: str) -> str:
        url = CHAMPION_URL_TEMPLATE.format(version=version)
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text

    def _build_data(self, version: str, payload_json: str) -> ChampionData:
        payload = json.loads(payload_json)
        data = payload.get("data", {})

        ko_to_en: dict[str, str] = {}
        en_to_ko: dict[str, str] = {}
        for entry in data.values():
            english_key = entry.get("id")
            korean_name = entry.get("name")
            if not english_key or not korean_name:
                continue
            ko_to_en[korean_name] = english_key
            en_to_ko[english_key] = korean_name

        korean_names = sorted(ko_to_en.keys())
        return ChampionData(
            version=version,
            korean_names=korean_names,
            ko_to_en=ko_to_en,
            en_to_ko=en_to_ko,
        )
