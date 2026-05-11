"""Data Dragon 에서 소환사 주문 / 룬 메타데이터를 받아 SQLite 에 캐시한다.

매치 상세 패널에 필요한 정보:
- 소환사 주문 숫자 ID → 영문 키 (예: 4 → "SummonerFlash") + 한글 이름
- 룬 ID → 이름 + 아이콘 경로 + 소속 트리 ID
- 룬 트리 ID → 이름 + 아이콘 경로

챔피언 메타데이터와 동일한 패턴이며, 갱신 TTL 도 7일로 맞춘다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
SUMMONER_URL_TEMPLATE = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/summoner.json"
)
RUNES_URL_TEMPLATE = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/runesReforged.json"
)

# 7일이 지나면 패치 버전을 다시 확인한다.
VERSION_CHECK_TTL_SECONDS = 7 * 24 * 60 * 60

# 스탯 샤드 (Adaptive Force, Attack Speed 등) 아이콘은 Data Dragon 에 없다.
# CommunityDragon 의 안정 URL 을 사용한다.
_STAT_SHARD_BASE = "https://raw.communitydragon.org/latest/game/assets/perks/statmods/"
STAT_SHARD_ICONS: dict[int, str] = {
    5001: _STAT_SHARD_BASE + "statmodshealthscalingicon.png",  # 체력 (성장)
    5002: _STAT_SHARD_BASE + "statmodsarmoricon.png",  # 방어력
    5003: _STAT_SHARD_BASE + "statmodsmagicresicon.png",  # 마법 저항력
    5005: _STAT_SHARD_BASE + "statmodsattackspeedicon.png",  # 공격 속도
    5007: _STAT_SHARD_BASE + "statmodscdrscalingicon.png",  # 능력 가속
    5008: _STAT_SHARD_BASE + "statmodsadaptiveforceicon.png",  # 적응형 능력치
}
STAT_SHARD_NAMES: dict[int, str] = {
    5001: "체력 (성장)",
    5002: "방어력",
    5003: "마법 저항력",
    5005: "공격 속도",
    5007: "능력 가속",
    5008: "적응형 능력치",
}


@dataclass(frozen=True)
class StaticData:
    """매치 상세 패널에 필요한 정적 메타데이터."""

    version: str
    # 소환사 주문: "4" (str key) → {"id": "SummonerFlash", "name": "점멸"}
    summoner_by_key: dict[str, dict[str, str]] = field(default_factory=dict)
    # 룬: rune_id → {"name": str, "icon": "perk-images/...", "tree_id": int}
    rune_by_id: dict[int, dict[str, Any]] = field(default_factory=dict)
    # 룬 트리: tree_id → {"name": str, "icon": "perk-images/..."}
    tree_by_id: dict[int, dict[str, str]] = field(default_factory=dict)

    def summoner_icon_url(self, spell_id: int | str | None) -> str | None:
        """매치 데이터의 summoner1Id/summoner2Id (숫자) → 아이콘 URL."""
        if spell_id is None:
            return None
        entry = self.summoner_by_key.get(str(spell_id))
        if not entry:
            return None
        return (
            f"https://ddragon.leagueoflegends.com/cdn/{self.version}"
            f"/img/spell/{entry['id']}.png"
        )

    def summoner_name(self, spell_id: int | str | None) -> str:
        if spell_id is None:
            return ""
        entry = self.summoner_by_key.get(str(spell_id))
        return entry["name"] if entry else ""

    def rune_icon_url(self, rune_id: int | None) -> str | None:
        if rune_id is None:
            return None
        entry = self.rune_by_id.get(int(rune_id))
        if not entry:
            return None
        return f"https://ddragon.leagueoflegends.com/cdn/img/{entry['icon']}"

    def rune_name(self, rune_id: int | None) -> str:
        if rune_id is None:
            return ""
        entry = self.rune_by_id.get(int(rune_id))
        return entry["name"] if entry else ""

    def tree_icon_url(self, tree_id: int | None) -> str | None:
        if tree_id is None:
            return None
        entry = self.tree_by_id.get(int(tree_id))
        if not entry:
            return None
        return f"https://ddragon.leagueoflegends.com/cdn/img/{entry['icon']}"

    def tree_name(self, tree_id: int | None) -> str:
        if tree_id is None:
            return ""
        entry = self.tree_by_id.get(int(tree_id))
        return entry["name"] if entry else ""


def item_icon_url(version: str, item_id: int | None) -> str | None:
    """아이템 아이콘 URL. item_id 가 0 또는 None 이면 빈 칸이므로 None 을 반환한다."""
    if not item_id:
        return None
    return (
        f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{int(item_id)}.png"
    )


def stat_shard_icon_url(shard_id: int | None) -> str | None:
    if shard_id is None:
        return None
    return STAT_SHARD_ICONS.get(int(shard_id))


def stat_shard_name(shard_id: int | None) -> str:
    if shard_id is None:
        return ""
    return STAT_SHARD_NAMES.get(int(shard_id), "")


class StaticDataRepository:
    """소환사 주문·룬 메타데이터를 조회하고 SQLite 에 캐시한다."""

    _KIND_SUMMONER = "summoner"
    _KIND_RUNES = "runes"

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
                CREATE TABLE IF NOT EXISTS static_data_cache (
                    kind TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    # --- 외부 API ---
    def load(self, force_refresh: bool = False) -> StaticData:
        """캐시가 유효하면 캐시를, 아니면 Data Dragon 에서 새로 받아 반환한다."""
        cached_summoner = self._read_cache(self._KIND_SUMMONER)
        cached_runes = self._read_cache(self._KIND_RUNES)
        now = int(time.time())

        # 두 종류 모두 캐시되어 있고 TTL 안이라면 그대로 사용한다.
        if (
            not force_refresh
            and cached_summoner is not None
            and cached_runes is not None
            and now - cached_summoner["fetched_at"] < VERSION_CHECK_TTL_SECONDS
            and now - cached_runes["fetched_at"] < VERSION_CHECK_TTL_SECONDS
            and cached_summoner["version"] == cached_runes["version"]
        ):
            return self._build_data(
                version=cached_summoner["version"],
                summoner_payload=cached_summoner["payload_json"],
                runes_payload=cached_runes["payload_json"],
            )

        try:
            latest_version = self._fetch_latest_version()
        except httpx.HTTPError:
            # 네트워크 실패 시 캐시가 있다면 캐시로 폴백한다.
            if cached_summoner is not None and cached_runes is not None:
                return self._build_data(
                    version=cached_summoner["version"],
                    summoner_payload=cached_summoner["payload_json"],
                    runes_payload=cached_runes["payload_json"],
                )
            raise

        # 종류별로 버전 일치 여부를 확인해 필요한 것만 새로 받는다.
        summoner_payload = self._reuse_or_fetch(
            cached_summoner,
            latest_version,
            SUMMONER_URL_TEMPLATE.format(version=latest_version),
            self._KIND_SUMMONER,
            force_refresh,
        )
        runes_payload = self._reuse_or_fetch(
            cached_runes,
            latest_version,
            RUNES_URL_TEMPLATE.format(version=latest_version),
            self._KIND_RUNES,
            force_refresh,
        )

        return self._build_data(
            version=latest_version,
            summoner_payload=summoner_payload,
            runes_payload=runes_payload,
        )

    # --- 내부 ---
    def _reuse_or_fetch(
        self,
        cached: dict[str, Any] | None,
        latest_version: str,
        url: str,
        kind: str,
        force_refresh: bool,
    ) -> str:
        if (
            not force_refresh
            and cached is not None
            and cached["version"] == latest_version
        ):
            # 버전이 같으면 페이로드를 재사용하고 fetched_at 만 갱신한다.
            self._write_cache(kind, latest_version, cached["payload_json"])
            return cached["payload_json"]

        payload_json = self._fetch_text(url)
        self._write_cache(kind, latest_version, payload_json)
        return payload_json

    def _read_cache(self, kind: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT version, payload_json, fetched_at "
                "FROM static_data_cache WHERE kind = ?",
                (kind,),
            ).fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "payload_json": row["payload_json"],
            "fetched_at": row["fetched_at"],
        }

    def _write_cache(self, kind: str, version: str, payload_json: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO static_data_cache (kind, version, payload_json, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind) DO UPDATE SET
                    version = excluded.version,
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (kind, version, payload_json, now),
            )
            conn.commit()

    def _fetch_latest_version(self) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(VERSIONS_URL)
            resp.raise_for_status()
            versions = resp.json()
        if not isinstance(versions, list) or not versions:
            raise RuntimeError("Data Dragon 버전 목록이 비어 있습니다.")
        return versions[0]

    def _fetch_text(self, url: str) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text

    def _build_data(
        self,
        version: str,
        summoner_payload: str,
        runes_payload: str,
    ) -> StaticData:
        # --- 소환사 주문 파싱 ---
        summoner_data = json.loads(summoner_payload).get("data", {})
        summoner_by_key: dict[str, dict[str, str]] = {}
        for entry in summoner_data.values():
            key = entry.get("key")
            spell_id = entry.get("id")
            name = entry.get("name")
            if not key or not spell_id:
                continue
            summoner_by_key[str(key)] = {"id": spell_id, "name": name or spell_id}

        # --- 룬 파싱 ---
        runes_payload_parsed = json.loads(runes_payload)
        rune_by_id: dict[int, dict[str, Any]] = {}
        tree_by_id: dict[int, dict[str, str]] = {}
        if isinstance(runes_payload_parsed, list):
            for tree in runes_payload_parsed:
                tree_id = tree.get("id")
                if not isinstance(tree_id, int):
                    continue
                tree_by_id[tree_id] = {
                    "name": tree.get("name") or "",
                    "icon": tree.get("icon") or "",
                }
                for slot in tree.get("slots", []):
                    for rune in slot.get("runes", []):
                        rune_id = rune.get("id")
                        if not isinstance(rune_id, int):
                            continue
                        rune_by_id[rune_id] = {
                            "name": rune.get("name") or "",
                            "icon": rune.get("icon") or "",
                            "tree_id": tree_id,
                        }

        return StaticData(
            version=version,
            summoner_by_key=summoner_by_key,
            rune_by_id=rune_by_id,
            tree_by_id=tree_by_id,
        )
