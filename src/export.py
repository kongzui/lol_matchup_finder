"""검색 결과 내보내기."""

from __future__ import annotations

import io

import pandas as pd

from .db_search_service import IndexedMatchupSearchPayload
from .search_service import SearchPayload


def build_results_csv_bytes(payload: SearchPayload) -> bytes:
    """검색 결과를 Excel에서 열기 쉬운 UTF-8 BOM CSV 바이트로 만든다."""
    csv_rows = [
        {
            "game_date": row["game_date"],
            "my_champion": row["my_champion_key"],
            "enemy_champion": row["enemy_champion_key"],
            "enemy_riot_id": row["enemy_riot_id"],
            "result": "win" if row["win"] else "loss",
            "match_id": row["match_id"] or "",
        }
        for row in payload.results
    ]
    csv_df = pd.DataFrame(csv_rows)
    csv_buffer = io.StringIO()
    csv_df.to_csv(csv_buffer, index=False, encoding="utf-8")
    return csv_buffer.getvalue().encode("utf-8-sig")


def build_results_filename(payload: SearchPayload) -> str:
    """검색 조건 기반 CSV 파일명을 만든다."""
    return f"matchup_{payload.my_champion_key}_vs_{payload.enemy_champion_key}.csv"


def build_indexed_results_csv_bytes(payload: IndexedMatchupSearchPayload) -> bytes:
    """DB조회 결과를 Excel에서 열기 쉬운 UTF-8 BOM CSV 바이트로 만든다."""
    csv_rows = [
        {
            "game_date": row["game_date"],
            "player_riot_id": row.get("player_riot_id") or "",
            "player_rank": row.get("player_rank") or "",
            "player_league_points": row.get("player_league_points") or "",
            "my_champion": row["my_champion_key"],
            "enemy_champion": row["enemy_champion_key"],
            "enemy_riot_id": row["enemy_riot_id"],
            "result": "win" if row["win"] else "loss",
            "match_id": row["match_id"] or "",
        }
        for row in payload.results
    ]
    csv_df = pd.DataFrame(csv_rows)
    csv_buffer = io.StringIO()
    csv_df.to_csv(csv_buffer, index=False, encoding="utf-8")
    return csv_buffer.getvalue().encode("utf-8-sig")


def build_indexed_results_filename(payload: IndexedMatchupSearchPayload) -> str:
    """DB조회 조건 기반 CSV 파일명을 만든다."""
    enemy = payload.enemy_champion_key or "All"
    return f"indexed_matchup_{payload.my_champion_key}_vs_{enemy}.csv"
