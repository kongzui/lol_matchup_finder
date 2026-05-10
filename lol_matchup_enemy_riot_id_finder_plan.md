# LoL 매치업 상대 닉네임 추출기 구현 계획서

## 1. 프로젝트 개요

이 프로젝트는 기존 전적검색 사이트에서 직접 제공하지 않는 조건 검색을 보조하기 위한 개인용 도구이다.

목표는 OP.GG 같은 전체 전적 사이트를 새로 만드는 것이 아니라, 사용자가 입력한 조건에 맞는 경기만 Riot API로 필터링한 뒤, 해당 경기의 상대 라이너 Riot ID만 깔끔하게 출력하는 것이다.

예시 목표 기능:

> 내 계정이 솔로 랭크에서 아리 미드를 플레이했고, 상대 미드가 사일러스였던 경기만 찾아서 상대 사일러스 유저의 Riot ID 목록을 출력한다.

사용자는 출력된 상대 Riot ID를 OP.GG, DEEPLOL, Your.GG, LoLalytics 등 기존 전적 사이트에 붙여넣어 더 상세한 전적을 확인할 수 있다.

---

## 구현 확정 사항

본 계획서의 결정해야 할 항목은 아래와 같이 확정한다. 이후 본문은 이 결정값을 기준으로 한다.

- **API Key**: Riot Developer Portal에서 발급 완료 상태로 가정한다. `.env`의 `RIOT_API_KEY`에 발급받은 키를 넣어서 사용하며, 발급/갱신 절차 자체는 본 구현 범위에서 다루지 않는다.
- **서버 고정**: 한국 서버만 지원한다. `.env`에 `RIOT_REGION=asia`, `RIOT_PLATFORM=kr`로 고정하고, 다른 서버 선택 UI나 분기는 두지 않는다.
- **챔피언 목록**: Riot Data Dragon에서 전체 챔피언을 자동으로 가져온다. 코드 내에 챔피언 이름을 하드코딩하지 않으며, 신규 챔피언이 추가되면 캐시 갱신만으로 자동 반영된다.
- **표시 언어**: UI 라벨과 챔피언 이름은 한글로 표시한다. Data Dragon은 `ko_KR` 로케일을 사용하고, 내부 비교에는 영문 키(`Ahri`, `Sylas` 등)를 그대로 사용한다.
- **UI 스택**: Streamlit 단일 페이지 앱으로 구현한다. FastAPI 백엔드나 React 프론트엔드는 사용하지 않는다.
- **프로젝트 폴더**: 본 저장소를 새 프로젝트 루트로 그대로 사용한다. 같은 저장소에 있는 `CLAUDE.md`(BitGame Web 기준)는 본 프로젝트와 무관하므로, 본 구현은 해당 가이드의 폴더 구조나 빠른 시작 명령어를 따르지 않는다.

---

## 2. 문제 배경

현재 많은 전적검색 사이트는 다음과 같은 필터는 제공한다.

- 특정 유저의 최근 전적
- 특정 챔피언으로 플레이한 경기
- 큐 타입별 전적
- 시즌/기간별 전적
- 챔피언별 승률

하지만 대부분의 사이트는 아래와 같은 조합 필터를 직접 제공하지 않는다.

- 내가 아리를 했을 때
- 라인은 미드였고
- 상대 미드 챔피언이 사일러스였던 판만
- 상대 유저 닉네임만 모아서 보기

기존 사이트에서 “내 챔피언 = 아리”까지는 고정할 수 있어도, “상대 맞라이너 챔피언 = 사일러스”까지 고정하는 기능은 일반적으로 부족하다.

이 프로젝트는 이 틈새를 보완한다.

---

## 3. 핵심 아이디어

전적 사이트 전체를 만들 필요 없이, 아래의 작은 기능만 구현한다.

1. 내 Riot ID를 입력한다.
2. 검색 큐를 솔로 랭크로 제한한다.
3. 검색 기간을 선택한다.
4. 내 챔피언을 선택한다. 예: 아리
5. 상대 챔피언을 선택한다. 예: 사일러스
6. Riot API로 내 매치 목록을 가져온다.
7. 각 매치 상세 데이터를 확인한다.
8. 내가 아리 미드였고, 상대 미드가 사일러스인 경기만 필터링한다.
9. 해당 경기의 상대 유저 Riot ID를 출력한다.
10. 필요하면 OP.GG 검색 링크를 같이 제공한다.

즉, 이 도구는 “전적 분석 사이트”가 아니라 “매치업 조건 검색 도구”이다.

---

## 4. 예상 사용자 흐름

### 4.1 입력 화면

필수 입력값:

```text
내 Riot ID: 예) Hide on bush#KR1
큐 타입: 솔로 랭크
검색 기간: 최근 30일 / 최근 90일 / 직접 지정
내 챔피언: 아리
상대 챔피언: 사일러스
라인: 미드
```

검색 버튼:

```text
[검색]
```

### 4.2 출력 화면

기본 출력 예시:

```text
검색 조건:
- 내 챔피언: 아리
- 상대 챔피언: 사일러스
- 라인: 미드
- 큐: 솔로 랭크
- 기간: 최근 90일

총 6판 발견

1. 상대닉네임#KR1
   날짜: 2026-05-08
   결과: 승리
   내 KDA: 7 / 2 / 9
   OP.GG 열기

2. 상대닉네임2#KR1
   날짜: 2026-05-02
   결과: 패배
   내 KDA: 2 / 5 / 4
   OP.GG 열기
```

복사용 목록:

```text
상대닉네임#KR1
상대닉네임2#KR1
상대닉네임3#KR1
```

CSV 다운로드 예시:

```csv
game_date,my_champion,enemy_champion,enemy_riot_id,result,match_id
2026-05-08,Ahri,Sylas,상대닉네임#KR1,win,KR_1234567890
2026-05-02,Ahri,Sylas,상대닉네임2#KR1,loss,KR_1234567891
```

---

## 5. Riot API 구현 맥락

### 5.1 Riot ID와 PUUID

요즘 Riot 계정 검색은 소환사명 단독보다 Riot ID 기준으로 처리하는 것이 적합하다.

Riot ID는 보통 다음 형태이다.

```text
gameName#tagLine
```

예:

```text
Hide on bush#KR1
```

API 호출 순서는 다음과 같다.

```text
Riot ID
→ Account API
→ PUUID
→ Match API
→ Match ID 목록
→ Match 상세 데이터
```

### 5.2 필요한 주요 API

#### Account API

목적:

- 사용자가 입력한 Riot ID로 PUUID를 얻는다.

개념적 엔드포인트:

```text
/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
```

반환 데이터 예시:

```json
{
  "puuid": "...",
  "gameName": "Hide on bush",
  "tagLine": "KR1"
}
```

#### Match API - 매치 ID 목록 조회

목적:

- 특정 PUUID의 매치 ID 목록을 가져온다.

개념적 엔드포인트:

```text
/lol/match/v5/matches/by-puuid/{puuid}/ids
```

사용할 주요 파라미터:

```text
queue=420
startTime=검색 시작 시각 Unix timestamp
endTime=검색 종료 시각 Unix timestamp
start=0
count=100
```

큐 ID:

```text
420 = Ranked Solo/Duo
```

이 단계에서 가능한 필터:

- 솔로 랭크 여부
- 검색 기간
- 가져올 매치 수

이 단계에서 불가능한 필터:

- 내 챔피언이 아리인지
- 상대 챔피언이 사일러스인지
- 상대 라인이 미드인지

챔피언/라인/상대 라이너 조건은 매치 상세 데이터를 받아온 뒤 직접 후처리해야 한다.

#### Match API - 매치 상세 조회

목적:

- 각 matchId에 포함된 10명의 플레이어 정보를 확인한다.

개념적 엔드포인트:

```text
/lol/match/v5/matches/{matchId}
```

확인할 주요 필드:

```text
metadata.matchId
metadata.participants
info.gameCreation
info.queueId
info.participants
```

participant에서 확인할 필드:

```text
puuid
riotIdGameName
riotIdTagline
summonerName
championId
championName
teamId
teamPosition
individualPosition
win
kills
deaths
assists
totalMinionsKilled
neutralMinionsKilled
goldEarned
totalDamageDealtToChampions
```

---

## 6. 필터링 로직

### 6.1 기본 조건

검색 조건:

```text
내 PUUID == target_puuid
내 championName == Ahri
내 teamPosition == MIDDLE
상대 teamId != 내 teamId
상대 teamPosition == MIDDLE
상대 championName == Sylas
```

Python 의사코드:

```python
def find_matchup(match, target_puuid, my_champion, enemy_champion, lane="MIDDLE"):
    participants = match["info"]["participants"]

    me = next(
        (p for p in participants if p["puuid"] == target_puuid),
        None,
    )

    if me is None:
        return None

    if me.get("championName") != my_champion:
        return None

    if me.get("teamPosition") != lane:
        return None

    enemy_laner = next(
        (
            p for p in participants
            if p.get("teamId") != me.get("teamId")
            and p.get("teamPosition") == lane
        ),
        None,
    )

    if enemy_laner is None:
        return None

    if enemy_laner.get("championName") != enemy_champion:
        return None

    return {
        "match_id": match["metadata"]["matchId"],
        "game_creation": match["info"]["gameCreation"],
        "my_champion": me["championName"],
        "enemy_champion": enemy_laner["championName"],
        "enemy_riot_id": format_riot_id(enemy_laner),
        "win": me["win"],
        "kills": me["kills"],
        "deaths": me["deaths"],
        "assists": me["assists"],
    }
```

Riot ID 포맷 함수:

```python
def format_riot_id(participant):
    game_name = participant.get("riotIdGameName")
    tag_line = participant.get("riotIdTagline")

    if game_name and tag_line:
        return f"{game_name}#{tag_line}"

    return participant.get("summonerName", "Unknown")
```

---

## 7. 라인 판정 정확도

### 7.1 MVP 기준

MVP에서는 `teamPosition`을 기준으로 맞라이너를 판정한다.

장점:

- 구현이 쉽다.
- 속도가 빠르다.
- 솔로 랭크에서는 대체로 충분히 정확하다.
- OP.GG에서 다시 확인할 목적이라면 실사용성이 좋다.

단점:

- 라인 스왑이 있었던 판은 실제 맞라이너와 다를 수 있다.
- 비정상 조합, 포지션 꼬임, 리메이크, 초반 스왑 상황에서는 부정확할 수 있다.
- 일부 데이터에서 `teamPosition` 또는 `individualPosition`이 비어 있거나 예상과 다를 수 있다.

### 7.2 추후 정확 모드

추후에는 timeline API를 사용해 정확도를 높일 수 있다.

분석 아이디어:

- 0~14분 위치 좌표
- CS 증가 시점
- 경험치/골드 흐름
- 챔피언 간 근접 시간
- 초반 교전 위치
- 포탑 근처 체류 시간

정확 모드 예시:

```text
빠른 모드:
- match 상세의 teamPosition만 사용

정확 모드:
- timeline까지 조회
- 0~14분 동안 실제 라인 체류 위치 분석
- 실제 맞라이너 추정
```

MVP에서는 빠른 모드만 구현하고, 정확 모드는 후순위로 둔다.

---

## 8. API 호출량과 캐싱 전략

### 8.1 호출량 문제

예를 들어 최근 100판을 검색하면 대략 다음 호출이 필요하다.

```text
1회: Riot ID → PUUID
1회: PUUID → matchId 목록
100회: match 상세 조회
총 약 102회
```

개인용 API Key는 호출 제한이 작기 때문에, 같은 matchId를 반복 조회하면 금방 rate limit에 걸릴 수 있다.

따라서 캐싱은 거의 필수이다.

### 8.2 SQLite 캐시

개인용 도구라면 SQLite 하나로 충분하다.

권장 파일:

```text
data/matchup_finder.db
```

권장 테이블:

```sql
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
```

### 8.3 캐시 정책

권장 정책:

```text
account_cache:
- Riot ID → PUUID 조회 결과 저장
- 7일~30일 정도 재사용 가능
- 단, Riot ID 변경 가능성이 있으므로 너무 오래 고정하지는 않기

match_cache:
- matchId 상세 raw_json 저장
- 과거 경기는 변하지 않으므로 사실상 영구 캐시 가능
- 같은 matchId는 다시 API 호출하지 않기

search_history:
- 사용자가 과거에 어떤 조건을 검색했는지 기록
- 필수는 아니지만 디버깅과 재검색에 유용
```

---

## 9. UI/UX 설계

### 9.1 목표

UI는 화려할 필요가 없다.

중요한 것은 다음이다.

- 검색 조건이 명확할 것
- 결과가 한눈에 보일 것
- 상대 Riot ID 복사가 쉬울 것
- OP.GG 검색으로 이어지기 쉬울 것
- API 호출 상태와 오류를 사용자가 이해하기 쉬울 것

### 9.2 MVP UI

추천: Streamlit

이유:

- Python만으로 빠르게 구현 가능
- HTML/CSS 부담이 적음
- 입력 폼, 버튼, 테이블, 다운로드 버튼 구현이 쉬움
- 개인용 로컬 도구에 적합

화면 구성:

```text
[상단 제목]
LoL 매치업 상대 닉네임 추출기

[검색 조건]
내 Riot ID: [____________]
큐 타입: [솔로 랭크]
검색 기간: [최근 30일 ▼]
내 챔피언: [아리 ▼]
상대 챔피언: [사일러스 ▼]
라인: [미드 ▼]

[검색 버튼]

[결과 요약]
총 N판 발견

[결과 테이블]
날짜 | 상대 Riot ID | 결과 | 내 KDA | OP.GG

[복사용 목록]
textarea 형태로 Riot ID만 출력

[CSV 다운로드]
```

### 9.3 결과 테이블 컬럼

필수:

```text
날짜
상대 Riot ID
결과
내 챔피언
상대 챔피언
매치 ID
OP.GG 링크
```

선택:

```text
내 KDA
내 CS
내 딜량
게임 길이
패치 버전
```

### 9.4 OP.GG 링크 생성

기본 아이디어:

```text
https://op.gg/ko/lol/summoners/kr/{gameName}-{tagLine}
```

예:

```text
https://op.gg/ko/lol/summoners/kr/Hide%20on%20bush-KR1
```

주의:

- 공백, 특수문자, 한글 닉네임은 URL 인코딩이 필요하다.
- OP.GG URL 규칙이 변경될 수 있으므로, 링크 생성 실패에 대비해 Riot ID 텍스트 복사를 항상 제공한다.
- 가장 안정적인 기능은 “링크”보다 “복사용 Riot ID 목록”이다.

---

## 10. 기술 스택 제안

### 10.1 1차 MVP

권장 스택:

```text
Python
requests 또는 httpx
Streamlit
SQLite
python-dotenv
pandas
```

장점:

- 구현이 빠르다.
- UI 부담이 적다.
- 개인용 로컬 실행에 적합하다.
- API 테스트와 데이터 후처리가 편하다.

예상 폴더 구조:

```text
lol-matchup-finder/
  README.md
  .env.example
  requirements.txt
  app.py
  src/
    riot_client.py
    matchup_filter.py
    cache.py
    models.py
    opgg.py
    utils.py
  data/
    matchup_finder.db
```

### 10.2 2차 확장

웹앱으로 확장하고 싶다면:

```text
FastAPI backend
React/Vite frontend
SQLite cache
```

하지만 초기 목적에는 과하다.

처음부터 FastAPI + React로 가면 UI, 라우팅, 상태관리, 빌드, 배포까지 일이 커진다. 이 프로젝트의 본질은 “조건 필터링 후 닉네임 출력”이므로, MVP는 Streamlit이 적합하다.

---

## 11. 설정 파일

`.env.example`:

```env
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RIOT_REGION=asia
RIOT_PLATFORM=kr
DEFAULT_QUEUE_ID=420
CACHE_DB_PATH=data/matchup_finder.db
```

설명:

```text
RIOT_REGION=asia
- Match-v5와 Account-v1에서 한국 서버 계정/매치를 조회할 때 사용

RIOT_PLATFORM=kr
- 필요 시 League-v4, Summoner-v4 같은 platform routing API에서 사용

DEFAULT_QUEUE_ID=420
- 솔로 랭크
```

---

## 12. 주요 모듈 설계

### 12.1 riot_client.py

역할:

- Riot API 호출 담당
- API Key 헤더 처리
- rate limit 대응
- 429 재시도
- 요청 실패 처리

주요 함수:

```python
class RiotClient:
    def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict:
        ...

    def get_match_ids(
        self,
        puuid: str,
        queue_id: int,
        start_time: int,
        end_time: int,
        start: int = 0,
        count: int = 100,
    ) -> list[str]:
        ...

    def get_match_by_id(self, match_id: str) -> dict:
        ...
```

### 12.2 cache.py

역할:

- SQLite 저장/조회
- 이미 조회한 matchId 재사용
- account_cache 관리

주요 함수:

```python
class MatchCache:
    def get_account(self, riot_id: str) -> dict | None:
        ...

    def save_account(self, riot_id: str, account: dict) -> None:
        ...

    def get_match(self, match_id: str) -> dict | None:
        ...

    def save_match(self, match_id: str, match: dict) -> None:
        ...
```

### 12.3 matchup_filter.py

역할:

- 매치 상세 JSON에서 조건에 맞는 경기인지 판단
- 상대 라이너 participant 추출
- 결과 row 생성

주요 함수:

```python
def extract_matchup_result(
    match: dict,
    target_puuid: str,
    my_champion: str,
    enemy_champion: str,
    lane: str,
) -> dict | None:
    ...
```

### 12.4 opgg.py

역할:

- Riot ID 기반 OP.GG 링크 생성
- URL 인코딩 처리

주요 함수:

```python
def build_opgg_url(game_name: str, tag_line: str, region: str = "kr") -> str:
    ...
```

### 12.5 app.py

역할:

- Streamlit UI
- 입력값 받기
- 검색 실행
- 결과 테이블/복사용 목록/CSV 다운로드 출력

---

## 13. 검색 알고리즘

전체 흐름:

```text
1. 사용자 입력 수집
2. Riot ID 파싱
3. account_cache에서 PUUID 확인
4. 없으면 Account API 호출
5. 검색 기간을 Unix timestamp로 변환
6. Match API로 matchId 목록 조회
7. 각 matchId에 대해:
   7-1. match_cache에서 상세 데이터 확인
   7-2. 없으면 Riot API 호출 후 캐시 저장
   7-3. 내가 아리 미드인지 확인
   7-4. 상대 미드가 사일러스인지 확인
   7-5. 맞으면 결과 리스트에 추가
8. 결과 정렬
9. 화면 출력
10. 복사용 목록과 CSV 제공
```

---

## 14. 에러 처리

### 14.1 Riot ID 형식 오류

입력:

```text
Hide on bush
```

문제:

```text
태그라인이 없음
```

표시:

```text
Riot ID는 '게임이름#태그' 형식으로 입력해주세요.
예: Hide on bush#KR1
```

### 14.2 API Key 없음

표시:

```text
RIOT_API_KEY가 설정되어 있지 않습니다.
.env 파일에 Riot API Key를 넣어주세요.
```

### 14.3 403 Forbidden

가능 원인:

- API Key 만료
- API Key 오타
- Developer key 만료

표시:

```text
Riot API Key가 만료되었거나 잘못되었습니다.
Developer Portal에서 새 키를 발급받아 .env를 갱신해주세요.
```

### 14.4 429 Rate Limit

표시:

```text
Riot API 호출 제한에 도달했습니다.
잠시 후 다시 시도하거나, 검색 기간을 줄여주세요.
이미 조회한 경기는 캐시에서 재사용됩니다.
```

처리:

- Retry-After 헤더가 있으면 대기 후 재시도
- 없으면 짧은 backoff 적용
- MVP에서는 과도한 자동 재시도보다 사용자에게 명확히 알리는 것이 좋다.

### 14.5 검색 결과 없음

표시:

```text
조건에 맞는 경기를 찾지 못했습니다.

확인해볼 것:
- 검색 기간을 늘려보세요.
- 내 챔피언/상대 챔피언 철자를 확인하세요.
- 라인이 미드로 기록되지 않은 경기일 수 있습니다.
```

---

## 15. 챔피언 목록 처리 방식

이번 구현에서는 **Riot Data Dragon에서 전체 챔피언 목록을 자동으로 가져와 한글로 표시**한다. 챔피언 이름을 코드에 하드코딩하지 않는다.

### 15.1 데이터 소스

Data Dragon은 Riot이 공식 제공하는 정적 게임 리소스 저장소이며, API Key가 필요 없다.

```text
최신 버전 조회:
https://ddragon.leagueoflegends.com/api/versions.json

한글 챔피언 목록:
https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/champion.json
```

`champion.json`에서 사용하는 필드:

- `data.{key}.id` — 영문 키 (예: `Ahri`). Match-V5의 `championName`과 동일한 값.
- `data.{key}.name` — 한글 이름 (예: `아리`). UI 표시용.
- `data.{key}.key` — 숫자 ID. 필요 시 사용.

### 15.2 갱신 정책

- 앱 시작 시 한 번 호출해서 메모리/캐시에 적재한다.
- 결과는 SQLite 또는 로컬 파일에 캐싱하고, 패치 버전이 바뀌었을 때만 다시 받아온다.
- 사용자가 “챔피언 목록 새로고침” 버튼으로 강제 갱신할 수 있게 둔다 (선택).

### 15.3 UI/내부 처리

- 드롭다운에는 **한글 이름**으로 표시한다. 예: `아리`, `사일러스`, `오리아나`
- 사용자가 한글 이름을 고르면, 내부에서는 영문 키(`Ahri`, `Sylas`)로 변환해 Match-V5의 `participants[].championName`과 비교한다.
- 한글 → 영문, 영문 → 한글 양방향 매핑 테이블을 메모리에 들고 있는다.
- 결과 표시에서도 챔피언 이름은 한글로 출력한다.

---

## 16. MVP 범위

### 반드시 구현

- Riot ID 입력
- 한국 서버 고정 (`asia` / `kr`)
- 솔로 랭크 queue=420 검색
- 기간 선택
- Data Dragon에서 챔피언 목록 자동 조회 (ko_KR)
- 한글 이름으로 내 챔피언 / 상대 챔피언 선택
- 라인 선택
- 조건에 맞는 경기 필터링
- 상대 Riot ID 출력 (한글 챔피언 이름과 함께)
- 복사용 목록 출력
- CSV 다운로드
- SQLite match_cache
- API Key 오류 처리
- rate limit 기본 처리

### 나중으로 미룰 것

- 로그인 기능
- 사용자 계정 저장
- 배포용 웹서비스
- 모든 챔피언 통계 대시보드
- timeline 기반 정확 라인 판정
- 승률 그래프
- 라인전 지표 상세 분석
- 자동 OP.GG 크롤링
- 다중 유저 검색
- 듀오 여부 분석
- 리플레이/영상 연동

---

## 17. 향후 확장 아이디어

### 17.1 매치업 통계

상대 닉네임 출력뿐 아니라 다음 통계도 가능하다.

```text
아리 vs 사일러스 총 N판
승률
평균 KDA
평균 CS
평균 딜량
15분 골드 차이
15분 CS 차이
첫 데스 평균 시간
솔킬 여부
```

단, 15분 골드/CS 차이, 솔킬 여부 등은 timeline 분석이 필요할 수 있다.

### 17.2 챔피언별 상대 매치업 표

예:

```text
아리 vs 사일러스: 6판, 승률 66.7%
아리 vs 오리아나: 4판, 승률 50.0%
아리 vs 빅토르: 3판, 승률 33.3%
```

### 17.3 원챔 복기 도구

아리 원챔 기준으로 다음 기능을 추가할 수 있다.

```text
상대 챔피언별 최근 경기 리스트
자주 지는 상대 챔피언
자주 이기는 상대 챔피언
특정 상대 챔피언전 리플레이 메모
밴 추천 후보
라인전 주의 메모
```

### 17.4 OP.GG 보조 링크 모음

결과 행마다 여러 사이트 링크를 붙일 수 있다.

```text
OP.GG
DEEPLOL
Your.GG
LoLalytics
LeagueOfGraphs
```

---

## 18. 구현 순서

### 1단계: API 연결 확인

- Riot API Key는 이미 발급된 상태로 가정한다.
- `.env`에 `RIOT_API_KEY`, `RIOT_REGION=asia`, `RIOT_PLATFORM=kr` 설정
- Riot ID로 PUUID 조회 테스트 (Account-V1)
- PUUID로 matchId 목록 조회 테스트 (Match-V5, queue=420)
- matchId로 match 상세 조회 테스트 (Match-V5)
- Data Dragon 한글 챔피언 목록(`ko_KR/champion.json`) 조회 테스트

### 2단계: 필터링 로직 구현

- target_puuid participant 찾기
- 내 챔피언 조건 확인
- 내 라인 조건 확인
- 상대팀 같은 라인 participant 찾기
- 상대 챔피언 조건 확인
- 결과 row 생성

### 3단계: Streamlit UI 구현

- 입력 폼
- 검색 버튼
- 로딩 상태 표시
- 결과 테이블
- 복사용 목록
- CSV 다운로드

### 4단계: SQLite 캐시 구현

- account_cache
- match_cache
- 캐시 우선 조회
- API 조회 후 캐시 저장

### 5단계: 안정화

- 403 처리
- 429 처리
- 검색 결과 없음 처리
- Riot ID 파싱 오류 처리
- 한글 닉네임/공백 닉네임 URL 인코딩

### 6단계: 사용성 개선

- 최근 검색 조건 저장
- 기본값 아리/사일러스/미드
- 결과 정렬
- OP.GG 링크 버튼
- 중복 상대 닉네임 그룹화 옵션

---

## 19. 개발 시 주의점

### 19.1 Riot ID 변경 가능성

Riot ID는 변경될 수 있으므로 account_cache는 영구 고정하지 않는 것이 좋다.

### 19.2 과거 경기 데이터는 안정적

한 번 끝난 match 상세 데이터는 변하지 않는다고 봐도 되므로 match_cache는 장기 보관해도 된다.

### 19.3 queue 필터는 API 단계에서 적용

솔로 랭크만 볼 경우 matchId 조회 단계에서 `queue=420`을 적용한다.

### 19.4 챔피언 필터는 후처리

Riot API의 matchlist 조회는 특정 챔피언이나 상대 챔피언 필터를 직접 제공하지 않는다. 따라서 매치 상세를 가져와 직접 확인해야 한다.

### 19.5 URL보다 Riot ID 텍스트가 더 중요

OP.GG 링크는 편의 기능이다. URL 규칙은 사이트 변경에 영향을 받을 수 있다. 따라서 항상 복사용 Riot ID 텍스트를 핵심 출력으로 제공한다.

---

## 20. 참고 자료

- Riot Developer Portal - APIs  
  https://developer.riotgames.com/apis

- Riot Developer Portal - Rate Limiting  
  https://developer.riotgames.com/docs/portal

- Riot Developer Relations - Summoner Names to Riot ID  
  https://www.riotgames.com/en/DevRel/summoner-names-to-riot-id

- RiotWatcher MatchApiV5 문서  
  https://riot-watcher.readthedocs.io/en/latest/riotwatcher/LeagueOfLegends/MatchApiV5.html

- OP.GG Riot ID 검색 도움말  
  https://help.op.gg/hc/en-us/articles/31090336977177-How-to-search-stats-using-Riot-ID

---

## 21. 한 줄 요약

이 프로젝트는 “아리 vs 사일러스처럼 기존 전적 사이트에서 직접 필터링하기 어려운 특정 라인 매치업 경기만 찾아, 상대 Riot ID를 뽑아주는 개인용 전적 검색 보조 도구”이다.
