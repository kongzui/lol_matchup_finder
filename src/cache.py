"""SQLite 기반 캐시.

- account_cache: Riot ID → PUUID
- match_cache: matchId → 매치 상세 raw JSON
- match_timeline_cache: matchId → 매치 타임라인 raw JSON
- search_history: 검색 조건 기록 (디버깅/재검색용)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any


# Riot ID는 변경 가능하므로 account_cache는 영구 보관하지 않는다.
ACCOUNT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


class MatchCache:
    """SQLite에 PUUID/매치 상세를 캐시한다."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_parent_dir()
        self._init_schema()

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_cache (
                    riot_id TEXT PRIMARY KEY,
                    puuid TEXT NOT NULL,
                    game_name TEXT,
                    tag_line TEXT,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_cache (
                    match_id TEXT PRIMARY KEY,
                    queue_id INTEGER,
                    game_creation INTEGER,
                    raw_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_timeline_cache (
                    match_id TEXT PRIMARY KEY,
                    raw_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_riot_id TEXT NOT NULL,
                    target_puuid TEXT NOT NULL,
                    queue_id INTEGER NOT NULL,
                    my_champion TEXT NOT NULL,
                    enemy_champion TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER NOT NULL,
                    result_count INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )
            conn.commit()

    @property
    def db_path(self) -> str:
        return self._db_path

    # --- account_cache ---
    def get_account(self, riot_id_key: str) -> dict[str, Any] | None:
        """riot_id_key는 'gameName#tagLine'을 lower-case 정규화한 값이어야 한다."""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT puuid, game_name, tag_line, fetched_at FROM account_cache "
                "WHERE riot_id = ?",
                (riot_id_key,),
            ).fetchone()

        if row is None:
            return None

        if now - row["fetched_at"] > ACCOUNT_CACHE_TTL_SECONDS:
            return None

        return {
            "puuid": row["puuid"],
            "game_name": row["game_name"],
            "tag_line": row["tag_line"],
            "fetched_at": row["fetched_at"],
        }

    def save_account(
        self,
        riot_id_key: str,
        puuid: str,
        game_name: str | None,
        tag_line: str | None,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_cache (riot_id, puuid, game_name, tag_line, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(riot_id) DO UPDATE SET
                    puuid = excluded.puuid,
                    game_name = excluded.game_name,
                    tag_line = excluded.tag_line,
                    fetched_at = excluded.fetched_at
                """,
                (riot_id_key, puuid, game_name, tag_line, now),
            )
            conn.commit()

    # --- match_cache ---
    def get_match(self, match_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM match_cache WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["raw_json"])
        except json.JSONDecodeError:
            return None

    def save_match(self, match_id: str, match: dict[str, Any]) -> None:
        info = match.get("info", {}) if isinstance(match, dict) else {}
        queue_id = info.get("queueId")
        game_creation = info.get("gameCreation")
        raw_json = json.dumps(match, ensure_ascii=False)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO match_cache
                    (match_id, queue_id, game_creation, raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    queue_id = excluded.queue_id,
                    game_creation = excluded.game_creation,
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at
                """,
                (match_id, queue_id, game_creation, raw_json, now),
            )
            conn.commit()

    # --- match_timeline_cache ---
    def get_match_timeline(self, match_id: str) -> dict[str, Any] | None:
        """matchId로 저장된 타임라인 JSON을 가져온다."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM match_timeline_cache WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["raw_json"])
        except json.JSONDecodeError:
            return None

    def save_match_timeline(self, match_id: str, timeline: dict[str, Any]) -> None:
        """matchId별 타임라인 JSON을 캐시한다."""
        raw_json = json.dumps(timeline, ensure_ascii=False)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO match_timeline_cache (match_id, raw_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at
                """,
                (match_id, raw_json, now),
            )
            conn.commit()

    # --- search_history ---
    def record_search(
        self,
        target_riot_id: str,
        target_puuid: str,
        queue_id: int,
        my_champion: str,
        enemy_champion: str,
        lane: str,
        start_time: int,
        end_time: int,
        result_count: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO search_history (
                    target_riot_id, target_puuid, queue_id,
                    my_champion, enemy_champion, lane,
                    start_time, end_time, result_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_riot_id,
                    target_puuid,
                    queue_id,
                    my_champion,
                    enemy_champion,
                    lane,
                    start_time,
                    end_time,
                    result_count,
                    now,
                ),
            )
            conn.commit()

    def get_latest_search_riot_id(self) -> str | None:
        """가장 최근 검색한 Riot ID를 반환한다."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT target_riot_id
                FROM search_history
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return row["target_riot_id"]


def normalize_riot_id_key(game_name: str, tag_line: str) -> str:
    """캐시 키로 쓸 정규화된 Riot ID 문자열을 만든다."""
    return f"{game_name.strip().lower()}#{tag_line.strip().lower()}"
