"""Riot API 호출 클라이언트.

- Account-V1: Riot ID → PUUID
- Match-V5: matchId 목록, matchId → 상세 JSON
- 429 응답 시 Retry-After 헤더에 따라 한 번 대기 후 재시도한다.
- 403/404 등은 의미 있는 예외로 변환한다.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx


DEFAULT_TIMEOUT = 10.0
MAX_RETRY_429 = 2
DEFAULT_BACKOFF_SECONDS = 5.0


class RiotApiError(RuntimeError):
    """Riot API 호출 실패 공통 예외."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RiotApiAuthError(RiotApiError):
    """API Key 만료/오타 (403)."""


class RiotApiNotFound(RiotApiError):
    """존재하지 않는 Riot ID/매치 (404)."""


class RiotApiRateLimited(RiotApiError):
    """Rate Limit 지속 (429 재시도 후에도 실패)."""


class RiotClient:
    """Riot API 호출을 담당한다.

    region: Account-V1, Match-V5에 사용 (예: "asia")
    api_key: Riot Developer Portal에서 발급한 키
    """

    def __init__(
        self,
        api_key: str,
        region: str = "asia",
        timeout: float = DEFAULT_TIMEOUT,
        sleep_func=time.sleep,
    ):
        if not api_key:
            raise RiotApiError("RIOT_API_KEY가 설정되어 있지 않습니다.")
        self._api_key = api_key
        self._region = region
        self._timeout = timeout
        self._sleep = sleep_func
        self._client: httpx.Client | None = None

    # --- 컨텍스트 관리 ---
    def __enter__(self) -> "RiotClient":
        self._client = httpx.Client(
            timeout=self._timeout,
            headers={"X-Riot-Token": self._api_key},
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"X-Riot-Token": self._api_key},
            )
        return self._client

    # --- 공개 API ---
    def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict[str, Any]:
        """Riot ID(gameName, tagLine)로 PUUID/계정 정보를 가져온다."""
        encoded_game = quote(game_name, safe="")
        encoded_tag = quote(tag_line, safe="")
        url = (
            f"https://{self._region}.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{encoded_game}/{encoded_tag}"
        )
        return self._get_json(
            url,
            not_found_message=(
                f"입력한 Riot ID '{game_name}#{tag_line}'를 찾을 수 없습니다. "
                "철자와 태그를 다시 확인해주세요."
            ),
        )

    def get_match_ids(
        self,
        puuid: str,
        queue_id: int,
        start_time: int,
        end_time: int,
        start: int = 0,
        count: int = 100,
    ) -> list[str]:
        """PUUID의 matchId 목록을 가져온다.

        Riot API는 한 번에 최대 100개까지 반환하므로,
        호출 측에서 start를 증가시켜 페이지네이션해야 한다.
        """
        url = (
            f"https://{self._region}.api.riotgames.com"
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
        )
        params = {
            "queue": queue_id,
            "startTime": start_time,
            "endTime": end_time,
            "start": start,
            "count": count,
        }
        result = self._get_json(url, params=params)
        if not isinstance(result, list):
            raise RiotApiError("matchId 목록 응답이 비정상입니다.")
        return result

    def get_match_by_id(self, match_id: str) -> dict[str, Any]:
        """matchId로 매치 상세 데이터를 가져온다."""
        url = (
            f"https://{self._region}.api.riotgames.com"
            f"/lol/match/v5/matches/{quote(match_id, safe='')}"
        )
        return self._get_json(
            url, not_found_message=(f"매치 데이터를 찾을 수 없습니다: {match_id}")
        )

    # --- 내부 ---
    def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        not_found_message: str | None = None,
    ) -> Any:
        client = self._ensure_client()
        attempts = 0

        while True:
            try:
                resp = client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise RiotApiError(f"Riot API 통신 오류: {exc}") from exc

            status = resp.status_code

            if status == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise RiotApiError(
                        "Riot API 응답을 JSON으로 파싱할 수 없습니다."
                    ) from exc

            if status == 401 or status == 403:
                raise RiotApiAuthError(
                    "Riot API Key가 만료되었거나 잘못되었습니다. "
                    "Developer Portal에서 새 키를 발급받아 .env를 갱신해주세요.",
                    status_code=status,
                )

            if status == 404:
                raise RiotApiNotFound(
                    not_found_message or "Riot API 리소스를 찾을 수 없습니다.",
                    status_code=status,
                )

            if status == 429 and attempts < MAX_RETRY_429:
                attempts += 1
                wait_s = self._parse_retry_after(resp.headers.get("Retry-After"))
                self._sleep(wait_s)
                continue

            if status == 429:
                raise RiotApiRateLimited(
                    "Riot API 호출 제한에 도달했습니다. "
                    "잠시 후 다시 시도하거나, 검색 기간을 줄여주세요.",
                    status_code=status,
                )

            if 500 <= status < 600 and attempts < MAX_RETRY_429:
                # 일시적 서버 오류는 짧게 한 번 재시도한다.
                attempts += 1
                self._sleep(DEFAULT_BACKOFF_SECONDS)
                continue

            raise RiotApiError(
                f"Riot API 오류 (status={status}): {resp.text[:200]}",
                status_code=status,
            )

    @staticmethod
    def _parse_retry_after(value: str | None) -> float:
        if not value:
            return DEFAULT_BACKOFF_SECONDS
        try:
            return max(1.0, float(value))
        except ValueError:
            return DEFAULT_BACKOFF_SECONDS
