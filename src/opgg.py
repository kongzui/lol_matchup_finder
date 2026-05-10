"""OP.GG 검색 링크 생성."""

from __future__ import annotations

from urllib.parse import quote


def build_opgg_url(game_name: str, tag_line: str, region: str = "kr") -> str:
    """Riot ID(gameName, tagLine)로 OP.GG 검색 URL을 만든다.

    예: build_opgg_url("Hide on bush", "KR1") →
        https://op.gg/ko/lol/summoners/kr/Hide%20on%20bush-KR1
    """
    encoded_name = quote(game_name or "", safe="")
    encoded_tag = quote(tag_line or "", safe="")
    return f"https://op.gg/ko/lol/summoners/{region}/{encoded_name}-{encoded_tag}"
