"""Riot API와 Data Dragon 연결을 점검하는 독립 스크립트.

사용 예 (PowerShell):
    python scripts/check_riot_api.py "Hide on bush#KR1"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.cache import MatchCache  # noqa: E402
from src.champions import ChampionRepository  # noqa: E402
from src.riot_client import RiotClient  # noqa: E402
from src.utils import days_ago_to_unix_range, parse_riot_id  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/check_riot_api.py <gameName#tagLine>")
        return 1

    riot_id_raw = sys.argv[1]
    load_dotenv()
    api_key = os.environ.get("RIOT_API_KEY", "").strip()
    region = os.environ.get("RIOT_REGION", "asia").strip() or "asia"
    queue_id = int(os.environ.get("DEFAULT_QUEUE_ID", "420"))
    db_path = os.environ.get("CACHE_DB_PATH", "data/matchup_finder.db")

    if not api_key:
        print("[오류] RIOT_API_KEY가 .env에 없습니다.")
        return 1

    game_name, tag_line = parse_riot_id(riot_id_raw)
    print(f"[입력] gameName='{game_name}', tagLine='{tag_line}'")

    # Data Dragon 점검
    repo = ChampionRepository(db_path)
    champion_data = repo.load()
    print(
        f"[Data Dragon] 버전={champion_data.version}, 챔피언 수={len(champion_data.korean_names)}"
    )

    # Riot API 점검
    cache = MatchCache(db_path)
    with RiotClient(api_key=api_key, region=region) as client:
        account = client.get_account_by_riot_id(game_name, tag_line)
        puuid = account["puuid"]
        print(f"[Account-V1] puuid={puuid[:12]}... gameName={account.get('gameName')}")

        start_ts, end_ts = days_ago_to_unix_range(30)
        ids = client.get_match_ids(
            puuid=puuid,
            queue_id=queue_id,
            start_time=start_ts,
            end_time=end_ts,
            start=0,
            count=5,
        )
        print(f"[Match-V5/ids] 최근 30일 솔로 랭크 매치 {len(ids)}개")

        if ids:
            match = client.get_match_by_id(ids[0])
            cache.save_match(ids[0], match)
            participants = match.get("info", {}).get("participants", [])
            print(
                f"[Match-V5/detail] matchId={ids[0]} participants={len(participants)}"
            )

    print("[완료] 연결 점검 성공")
    return 0


if __name__ == "__main__":
    sys.exit(main())
