# React/FastAPI 기반 UI 전면 재설계 계획

## 1. 현재 맥락

현재 앱은 Streamlit 기반 로컬 도구다.
핵심 서비스 로직은 이미 Python 모듈로 분리되어 있다.

- `src/search_service.py`: 개별 유저 매치업 검색
- `src/multi_search_service.py`: 여러 Riot ID 기반 매치 수집/인덱싱
- `src/db_search_service.py`: `matchup_index` 기반 DB조회
- `src/cache.py`: SQLite 캐시, match_cache, matchup_index, timeline_cache 관리
- `src/champions.py`, `src/static_data.py`: Data Dragon 챔피언/아이템/룬/주문 정적 데이터

문제는 주로 UI 계층에 있다.

- Streamlit 기본 렌더링과 HTML/CSS 조합이 섞여 있어 구조가 복잡하다.
- 탭 안의 코드가 모두 실행되는 Streamlit 특성 때문에 결과 목록과 상세 패널 상태 관리가 까다롭다.
- DB조회처럼 결과가 많을 수 있는 화면에서 전체 카드/이미지/버튼 렌더링 비용이 커진다.
- 다크 모드 색상 대비가 충분하지 않아 일부 텍스트 가독성이 떨어진다.
- UI 컴포넌트와 API/서비스 연결 방식이 한 파일권에 뒤섞여 있어 유지보수가 어렵다.

따라서 Streamlit UI를 개선하는 수준이 아니라, UI를 React로 분리하고 Python은 FastAPI 백엔드로 전환한다.

## 2. 반드시 유지할 핵심 기능

새 구조에서도 아래 기능과 실행 흐름은 유지해야 한다.

1. 개별유저검색
   - Riot ID를 입력한다.
   - 내 챔피언, 상대 챔피언, 라인, 기간, 최대 검색 매치 수를 선택한다.
   - Riot API와 로컬 캐시를 사용해 최근 솔로 랭크 매치를 분석한다.
   - 조건에 맞는 매치업 결과를 보여준다.
   - 검색 대상 유저가 인덱싱 기준 티어 이상이면 `matchup_index`에 반영한다.
   - 결과 CSV를 다운로드할 수 있다.
   - 각 결과에서 상대 Riot ID 복사, OP.GG 열기, 매치 상세 열기를 제공한다.

2. 멀티서치 수집
   - 여러 Riot ID를 입력한다.
   - 유저별 최근 솔로 랭크 matchId를 수집한다.
   - match detail을 `match_cache`에 저장하고 `matchup_index`에 반영한다.
   - 성공/실패 유저, 수집 매치 수, API 호출 수, 캐시 hit 수, 인덱싱 row 수를 보여준다.
   - 실패한 Riot ID와 사유를 표시한다.

3. DB조회
   - Riot API를 호출하지 않고 `matchup_index`만 조회한다.
   - 내 챔피언, 상대 챔피언 또는 전체, 라인, 기간, 현재 패치 여부로 조회한다.
   - DB 내부 조회는 결과 수를 제한하지 않는다.
   - 프론트에서는 페이지네이션으로 일부만 표시한다.
   - 전체 결과 기준 통계와 CSV 다운로드를 제공한다.
   - 각 결과에서 플레이어 Riot ID 복사, 상대 Riot ID 복사, OP.GG 열기, 매치 상세 열기를 제공한다.

4. 매치 상세
   - `matchId`와 기준 플레이어 PUUID로 상세 패널을 연다.
   - 기존 `extract_focus_view` 로직처럼 기준 플레이어, 맞라이너, 나머지 플레이어 정보를 보여준다.
   - 아이템, 스펠, 룬, 스킬/아이템 빌드 타임라인 정보를 보여준다.
   - timeline이 캐시에 없으면 기존 Riot API fetch 로직을 재사용해 가져온다.

5. 정적 데이터
   - 챔피언 이름은 한국어로 표시한다.
   - 내부 비교에는 Data Dragon 영문 champion key를 사용한다.
   - 챔피언/아이템/룬/스펠 아이콘 URL은 기존 정적 데이터 헬퍼를 재사용한다.

## 3. 목표 구조

새 구조는 로컬 2서버 방식으로 실행한다.

- 백엔드: FastAPI, 기본 포트 `8000`
- 프론트: Vite React TypeScript, 기본 포트 `5173`
- 인증/권한은 추가하지 않는다.
- 로컬 사용 도구이므로 job 상태는 서버 재시작 시 사라져도 된다.

### 백엔드 역할

Python 백엔드는 기존 서비스 로직을 그대로 감싸는 API 서버가 된다.

- Riot API 호출
- SQLite 캐시 접근
- 검색/수집/인덱싱 실행
- DB조회
- 매치 상세 데이터 조립
- CSV 생성
- Data Dragon 정적 데이터 제공
- 긴 작업의 진행률 상태 관리

### 프론트 역할

React 프론트는 화면 상태와 사용자 경험을 담당한다.

- 작업 공간 네비게이션
- 검색 조건 입력
- job 진행률 폴링
- 결과 목록 페이지네이션
- 카드형 결과 표시
- 상세 패널 lazy fetch
- CSV 다운로드 호출
- Clipboard API 기반 Riot ID 복사
- 고대비 다크 UI

## 4. API 설계

### 메타데이터

`GET /api/meta/options`

반환 내용:

- 챔피언 목록
- 라인 옵션
- 기간 옵션
- 멀티서치 기간 옵션
- 멀티서치 1인당 매치 수 옵션
- Data Dragon 버전
- Riot API key 감지 여부
- 서버 region/platform/queue_id 정보

### 개별유저검색 job

`POST /api/jobs/search`

요청:

- `riotIdRaw`
- `periodKind`
- `customStart`
- `customEnd`
- `myChampionKorean`
- `enemyChampionKorean`
- `laneLabel`
- `maxMatches`

응답:

- `jobId`

### 멀티서치 job

`POST /api/jobs/multi-search`

요청:

- `riotIdsRaw`
- `days`
- `matchesPerPlayer`

응답:

- `jobId`

### job 상태 조회

`GET /api/jobs/{jobId}`

반환 내용:

- `jobId`
- `status`: `queued`, `running`, `succeeded`, `failed`
- `progress`: 0.0부터 1.0
- `message`
- `error`

### job 결과 조회

`GET /api/jobs/{jobId}/result`

반환 내용:

- 개별유저검색 또는 멀티서치 payload
- job이 완료되지 않았으면 409 응답
- job이 실패했으면 오류 메시지 반환

### DB조회

`POST /api/db-search`

요청:

- `myChampionKorean`
- `enemyChampionKorean`
- `laneLabel`
- `periodKind`
- `customStart`
- `customEnd`
- `currentPatchOnly`
- `page`
- `pageSize`

중요한 규칙:

- 백엔드의 DB조회 자체는 조건에 맞는 전체 결과를 가져온다.
- 응답에는 전체 통계와 `total`을 포함한다.
- `results`에는 요청한 page slice만 담는다.

### 매치 상세

`GET /api/matches/{matchId}/detail?playerPuuid=...`

반환 내용:

- 기준 플레이어 상세
- 맞라이너 상세
- 나머지 팀원/상대 요약
- 빌드 타임라인
- 아이콘 URL을 프론트에서 바로 쓸 수 있는 정규화된 필드

### CSV 다운로드

`GET /api/exports/search/{jobId}.csv`

- 완료된 개별유저검색 결과를 CSV로 내려준다.

`POST /api/exports/db-search.csv`

- DB조회 조건 기준 전체 결과를 CSV로 내려준다.

## 5. 프론트 UI 설계

### 화면 구조

React 컴포넌트는 아래처럼 나눈다.

- `AppShell`
  - 전체 레이아웃
  - 상단 또는 좌측 네비게이션
  - 서버/API key 상태 표시

- `SearchWorkspace`
  - 개별유저검색 조건 입력
  - job 시작
  - 진행률 표시
  - 결과 목록 표시

- `MultiCollectWorkspace`
  - Riot ID 목록 입력
  - 수집 조건 입력
  - job 시작
  - 진행률, KPI, 실패 목록 표시

- `DbLookupWorkspace`
  - DB조회 조건 입력
  - 즉시 조회
  - 전체 통계 표시
  - 페이지네이션 결과 목록 표시

- `MatchResultList`
  - 검색 결과 카드 리스트
  - 페이지네이션
  - CSV 다운로드 버튼

- `MatchResultCard`
  - 날짜/승패
  - 플레이어 정보
  - 챔피언, KDA, CS, 피해량
  - 상대 라이너 정보
  - 복사, OP.GG, 상세 버튼

- `MatchDetailPanel`
  - 카드 아래 확장형 상세 패널
  - lazy fetch
  - 로딩/실패/성공 상태 처리

### 시각 방향

전적사이트형 UI를 목표로 한다.

- 정보 밀도는 높게 유지한다.
- 카드와 패널은 8px 이하 radius를 사용한다.
- 본문 텍스트는 충분히 밝게 유지한다.
- 보조 텍스트도 너무 어두운 회색을 쓰지 않는다.
- 승리/패배 색상은 명확히 구분한다.
- 장식성 gradient/orb는 쓰지 않는다.
- 첫 화면은 랜딩 페이지가 아니라 실제 검색/조회 도구다.

### 색상 기준

권장 토큰:

- 앱 배경: `#10141d`
- 패널 배경: `#171c26`
- 패널 보조 배경: `#1d2430`
- 경계선: `#303846`
- 기본 텍스트: `#f4f7fb`
- 본문 텍스트: `#d7dee8`
- 보조 텍스트: `#aab4c3`
- 약한 텍스트: `#9ca3af`
- 강조: `#21d19f`
- 승리: `#21d19f`
- 패배: `#ff6b7a`
- 경고: `#f4c430`

## 6. 마이그레이션 계획

1. 백엔드 API 추가
   - FastAPI 앱을 새로 만든다.
   - 기존 서비스 dataclass와 payload를 API 응답으로 직렬화한다.
   - job store와 background thread 실행 구조를 만든다.

2. 프론트 앱 추가
   - `frontend/`에 Vite React TS 프로젝트를 만든다.
   - API 클라이언트와 타입을 정의한다.
   - 세 작업 공간과 결과/상세 컴포넌트를 구현한다.

3. Streamlit 제거
   - React UI 검증 후 `app.py`, `src/ui/*`를 제거한다.
   - `requirements.txt`에서 Streamlit을 제거한다.

4. 실행 스크립트 갱신
   - `run.bat`에서 백엔드와 프론트 개발 서버를 각각 실행한다.
   - 백엔드는 `localhost:8000`, 프론트는 `localhost:5173`을 사용한다.

5. 문서 갱신
   - README에 새 실행 방법을 쓴다.
   - 기존 Streamlit 실행 설명은 제거한다.

## 7. 검증 기준

### 백엔드

- `GET /api/meta/options`가 정상 응답한다.
- `POST /api/db-search`가 조건에 맞는 전체 `total`과 page 결과를 반환한다.
- DB조회 `total`은 SQLite count와 일치해야 한다.
- `POST /api/jobs/search`가 jobId를 반환한다.
- `GET /api/jobs/{jobId}`가 진행률과 상태를 반환한다.
- job 완료 후 `GET /api/jobs/{jobId}/result`가 기존 payload 의미와 같은 결과를 반환한다.
- 잘못된 Riot ID, API key 없음, 날짜 역전, 챔피언 인식 실패는 명확한 오류로 응답한다.

### 프론트

- `npm run format:web`이 통과한다.
- `npm run check:web`이 통과한다.
- DB조회 페이지네이션이 동작한다.
- 상세 열기/닫기가 즉시 반응한다.
- 상세 데이터는 필요한 순간에만 fetch한다.
- CSV 다운로드가 동작한다.
- Riot ID 복사가 iframe 없이 Clipboard API로 동작한다.
- 데스크톱/모바일 폭에서 텍스트가 겹치지 않는다.
- 다크 모드에서 모든 주요 텍스트가 충분히 잘 보인다.

### 통합

- 개별유저검색 job 진행률 폴링이 완료 결과로 이어진다.
- 멀티서치 수집 결과의 KPI와 실패 목록이 기존 의미와 일치한다.
- DB조회는 Riot API 호출 없이 동작한다.
- 기존 `match_cache`, `matchup_index`, `match_timeline_cache` 데이터는 그대로 재사용된다.

## 8. 명시적 기본값과 가정

- 이 앱은 로컬 사용 도구로 본다.
- 인증/권한 기능은 추가하지 않는다.
- job 상태는 인메모리로 관리하며 서버 재시작 시 사라져도 된다.
- DB조회는 API 내부에서 결과 수를 제한하지 않는다.
- 프론트 페이지네이션 기본값은 page size 50으로 둔다.
- 상세 패널 v1은 카드 아래 확장형으로 구현한다.
- 핵심 검색/수집/인덱싱 로직은 변경하지 않는다.
- UI/UX와 API 경계만 전면 재설계한다.

