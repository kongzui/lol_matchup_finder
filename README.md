# LoL 매치업 찾기

관심 유저의 솔로 랭크 경기를 내 컴퓨터에 모아 두고,
**내 챔피언·상대 챔피언·라인·기간 조건에 맞는 경기**를 찾은 뒤
**상대 Riot ID 목록**을 복사하거나 CSV로 내려받는 개인용 도구입니다.

> 예) "내가 아리 미드를 했고 상대 미드가 사일러스였던 경기" 만 모아서
> 상대 Riot ID를 OP.GG/DEEPLOL/Your.GG 등에 붙여넣기 좋게 보여줍니다.

## 주요 특징

- **한국 서버 / 솔로 랭크 전용** (`asia` 리전, queue=420 고정)
- **한글 챔피언 이름 UI**: Data Dragon `ko_KR` 자동 조회 (하드코딩 없음)
- **SQLite 캐시**: 한 번 가져온 매치 상세는 다시 호출하지 않음
- **CSV / 복사용 텍스트**: 상대 Riot ID를 그대로 붙여넣기 가능

## 1. 설치

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 환경 설정

`.env.example`을 복사해 `.env`를 만들고 API Key를 채워 넣으세요.

```env
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
RIOT_REGION=asia
RIOT_PLATFORM=kr
DEFAULT_QUEUE_ID=420
CACHE_DB_PATH=data/matchup_finder.db
```

> Riot Developer Portal: <https://developer.riotgames.com/>
> 개인용 Development Key는 24시간마다 갱신됩니다.

## 3. 실행

```powershell
run.bat
```

백엔드는 기본 `8000`, React 프론트는 기본 `5173` 포트에서 실행됩니다.
다른 프로젝트의 프로세스를 종료하지 않고 다음 사용 가능한 포트를 자동으로 선택하며,
배치 파일에 출력된 React 프론트 주소로 접속하면 됩니다.

## 4. 연결 점검 스크립트

UI 없이 API 연결만 빠르게 확인하고 싶다면:

```powershell
python scripts/check_riot_api.py "Hide on bush#KR1"
```

다음을 순서대로 점검합니다.

1. Data Dragon `ko_KR/champion.json` 로딩
2. Account-V1 (Riot ID → PUUID)
3. Match-V5 매치 ID 목록 (최근 30일 / 솔로 랭크 / 5개)
4. Match-V5 매치 상세 1건

## 5. 폴더 구조

```text
lol_matchup_finder/
  frontend/             # React/Vite 프론트엔드
  requirements.txt
  .env.example
  src/
    api/                # FastAPI 로컬 API
    riot_client.py      # Riot API 호출 (Account-V1 / Match-V5)
    matchup_filter.py   # 매치업 조건 필터
    cache.py            # SQLite 캐시 (account/match/search)
    champions.py        # Data Dragon 챔피언 목록 (ko_KR)
    opgg.py             # OP.GG 검색 링크 생성
    utils.py            # Riot ID 파싱 / 기간 변환 / 라인 매핑
  scripts/
    check_riot_api.py   # API 연결 점검 스크립트
  data/
    matchup_finder.db   # 경기·관심 유저·수집 상태를 담는 로컬 DB (자동 생성)
```

## 6. 로컬 저장 정책

| 캐시 | TTL | 비고 |
|------|-----|------|
| account_cache | 7일 | Riot ID는 변경 가능하므로 너무 길게 두지 않음 |
| match_cache | 영구 | 끝난 매치 데이터는 변하지 않음 |
| champion_cache | 7일 | 만료 시 패치 버전을 다시 확인 |

> **주의:** `data/matchup_finder.db`는 단순 캐시 파일이 아닙니다. 이 파일에는
> 저장한 경기 원본과 검색용 자료뿐 아니라 관심 유저, 메모, 자동 수집 설정,
> 최근 수집 완료 위치도 함께 들어 있습니다. 파일을 지우면 이 정보가 모두
> 사라질 수 있으므로 캐시 정리 목적으로 삭제하지 마세요. 초기화가 필요하다면
> 앱을 종료하고 먼저 파일을 별도 위치에 백업한 뒤, 삭제 범위를 확인해야 합니다.

## 7. 알려진 한계

- 라인 판정은 Match-V5의 `teamPosition`만 사용합니다.
  라인 스왑/리메이크/포지션 꼬임 상황에서 부정확할 수 있습니다.
- OP.GG URL 규칙이 바뀌면 링크가 깨질 수 있어, 항상 **복사용 Riot ID 텍스트**를 같이 제공합니다.
- 개인용 Development Key는 호출 제한이 작습니다.
  너무 큰 기간/매치 수를 한 번에 조회하면 429에 걸릴 수 있습니다.
