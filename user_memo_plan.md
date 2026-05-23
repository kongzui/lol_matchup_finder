# 유저 메모 기능 계획

DB에 있는 유저(puuid)에 짧은 메모를 달아두고, 닉네임이 표시되는 화면(특히 DB조회 결과)에서 닉네임 옆에 `(메모)` 형태로 표시한다.

예: `리그오브레전드#KR1 (Faker)`

---

## 1. 목표와 비목표

### 목표

- DB에 존재하는 유저(puuid)에 자유 텍스트 메모 1개를 저장/수정/삭제할 수 있다.
- 결과 카드의 닉네임 옆에 메모가 자동으로 함께 표시된다.
- 별도의 "유저 메모" 관리 화면에서 등록된 메모를 일괄 확인/편집할 수 있다.

### 비목표 (이번 범위 아님)

- 매치 결과 카드에서의 인라인 메모 편집 (필요해지면 후속 작업).
- 메모 태그/색상/카테고리.
- 메모 검색으로 매치를 필터링하는 기능.
- 닉네임(riot_id) 기반 저장. 닉변 시 메모가 끊어지는 문제 때문에 `puuid` 기준만 사용한다.

---

## 2. 데이터 모델

### 2.1 새 테이블

`src/cache.py`의 `_init_schema()`에 다음을 추가한다.

```sql
CREATE TABLE IF NOT EXISTS player_memo (
    puuid TEXT PRIMARY KEY,
    memo TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
```

- `memo` 빈 문자열은 저장하지 않는다 → 삭제로 처리.
- PK가 `puuid`이므로 한 유저당 한 줄. 업데이트는 upsert.

### 2.2 저장 키를 puuid로 고른 이유

- 롤은 닉변이 가능 → `riot_id` 기준이면 닉변 시 메모가 끊어진다.
- DB조회 대상은 이미 인덱싱된 매치의 참가자이므로 `matchup_index.player_puuid` / `enemy_puuid`로 자연스럽게 조인 가능.
- 신규 메모 등록 시에는 닉네임을 입력받아 → `player_registry` 또는 Riot 계정 조회를 거쳐 puuid를 확정한 뒤 저장한다.

---

## 3. 백엔드 API

기준 파일: `src/api/main.py`

### 3.1 신규 엔드포인트

| 메서드 | 경로 | 용도 |
|---|---|---|
| `GET`  | `/api/player-memos` | 등록된 모든 메모 목록 (관리 화면용) |
| `POST` | `/api/player-memos/lookup` | puuid 배열 → `{puuid: memo}` 일괄 조회 (결과 표시용) |
| `PUT`  | `/api/player-memos/{puuid}` | upsert. body: `{ "memo": "Faker" }` |
| `DELETE` | `/api/player-memos/{puuid}` | 삭제 |
| `POST` | `/api/player-memos/resolve-riot-id` | 닉네임#태그 → puuid 변환 (관리 화면에서 새 메모 추가용) |

### 3.2 캐시 메서드

`src/cache.py`에 추가:

```python
def get_player_memo(self, puuid: str) -> str | None: ...
def get_player_memos(self, puuids: list[str]) -> dict[str, str]: ...
def list_player_memos(self) -> list[dict[str, Any]]: ...
def upsert_player_memo(self, puuid: str, memo: str) -> None: ...
def delete_player_memo(self, puuid: str) -> None: ...
```

`list_player_memos`는 화면 표시에 필요한 닉네임도 같이 돌려주기 위해 `player_registry`와 LEFT JOIN해서 `riot_id_game_name`, `riot_id_tag_line`, `last_seen_at`까지 함께 반환한다.

### 3.3 DB조회 응답에 메모 동봉

N+1 호출을 막기 위해 `IndexedMatchupSearchPayload`에 메모 맵을 함께 실어 보낸다.

- `db_search_service.run_indexed_matchup_search` 마지막에 결과 row들의 `player_puuid` / `enemy_puuid`를 모아 `cache.get_player_memos()`로 조회 → payload에 `memos: dict[str, str]` 필드를 추가.
- API 응답 직렬화에서도 이 맵을 그대로 내려준다.

멀티서치 결과도 같은 방식으로 응답에 `memos` 맵을 첨부한다. (관련 서비스: `src/multi_search_service.py`)

개별유저검색 응답은 검색 대상 puuid가 단일이므로, 응답에 해당 puuid의 메모 1건만 포함해도 충분하다.

---

## 4. 프론트엔드

기준 파일: `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/api.ts`

### 4.1 사이드바에 메뉴 추가

`App.tsx:408` 의 `nav-list` 영역에 항목 추가:

```
개별유저검색
멀티서치
DB조회
유저 메모     ← 신규 추가 (DB조회 아래)
```

- `active` state 타입에 `"memo"` 추가.
- 적당한 lucide 아이콘 사용 (예: `StickyNote` 또는 `BookUser`).

### 4.2 닉네임 옆 메모 표시

표시 규칙:
- 메모 없음 → 닉네임만 (괄호 안 나옴)
- 메모 있음 → `닉네임#태그 (메모)`

적용 위치:

1. **검색한 유저(player) 닉네임 옆** — `MatchResultCard`의 `player-id` 부분 (`App.tsx:1421`).
2. **상대 라이너(enemy) 닉네임 옆** — `enemy-summary`의 `enemyName` 출력 부분 (`App.tsx:1457`).
3. **멀티서치 결과**의 유저별 닉네임 표시 부분.
4. **DB조회/멀티서치 응답의 puuid → memo 맵**을 카드 컴포넌트에 props로 흘려보낸다.

작은 헬퍼 한 개로 처리:

```ts
function withMemo(nickname: string, memo: string | undefined | null): string {
  return memo ? `${nickname} (${memo})` : nickname;
}
```

### 4.3 "유저 메모" 관리 화면

별도 패널(섹션) 하나. 구조:

```
[ 유저 메모 ]
 ┌─ 새 메모 추가 ────────────────────────────┐
 │ 닉네임#태그 [____________]  메모 [______]  [추가]
 └───────────────────────────────────────────┘

 ┌─ 등록된 메모 (12) ───────────────────────┐
 │ 검색: [____________]
 │
 │ 닉네임#태그          메모            마지막 활동      [편집] [삭제]
 │ 리그오브레전드#KR1   Faker           2026-05-20      [편집] [삭제]
 │ ...
 └───────────────────────────────────────────┘
```

동작:

- **목록**: `GET /api/player-memos` 결과를 그대로 렌더. 메모가 등록된 유저만 보인다 (DB에 있는 전체 유저를 나열하지 않음).
- **검색**: 클라이언트 측 단순 필터 (닉네임 또는 메모 텍스트 부분 일치).
- **추가**:
  1. 닉네임#태그 + 메모 입력.
  2. `POST /api/player-memos/resolve-riot-id`로 puuid 확정.
  3. 확정되면 `PUT /api/player-memos/{puuid}`로 upsert.
  4. puuid를 찾지 못하면 "DB에서 해당 유저를 찾을 수 없습니다" 에러.
- **편집**: 행 인라인 편집. 저장 시 `PUT`.
- **삭제**: 확인 후 `DELETE`.

---

## 5. 영향 받는 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `src/cache.py` | `player_memo` 테이블 스키마, CRUD 메서드 추가 |
| `src/db_search_service.py` | 결과에 puuid→memo 맵 첨부 |
| `src/multi_search_service.py` | 결과에 puuid→memo 맵 첨부 |
| `src/api/main.py` | 메모 CRUD/lookup/resolve 엔드포인트, DB조회/멀티서치 응답에 memos 필드 추가 |
| `frontend/src/types.ts` | `memos` 필드, 메모 관련 타입 추가 |
| `frontend/src/api.ts` | 메모 API 클라이언트 함수들 추가 |
| `frontend/src/App.tsx` | 사이드바 "유저 메모" 메뉴, 관리 화면, 카드 닉네임 옆 메모 표시 |

---

## 6. 작업 순서 (구현 시 권장)

1. **DB/캐시 계층** — `player_memo` 테이블 + `cache.py` CRUD 메서드. 여기까지가 가장 안전한 기반.
2. **백엔드 API** — CRUD/lookup/resolve 엔드포인트 추가. (FastAPI Swagger로 단독 검증 가능)
3. **DB조회/멀티서치 응답에 memos 첨부** — 백엔드만 먼저 변경.
4. **프론트 표시** — 카드에 메모 렌더링. 이 시점에 DB조회 결과에서 `(메모)`가 보이기 시작.
5. **프론트 관리 화면** — 사이드바 메뉴 + 유저 메모 패널 UI.
6. **resolve-riot-id 흐름 검증** — 신규 메모 추가가 puuid 매칭까지 정상 동작하는지 확인.

각 단계 끝마다 동작 확인 후 다음 단계로.

---

## 7. 미해결/추후 결정 사항

- 메모 길이 제한? (UI에서 잘림 처리 — 예: 30자 이상은 `…`)
- "유저 메모" 화면에서 등록된 puuid의 닉네임이 닉변되었을 때, 자동 갱신 UI 제공할지.
- 인라인 편집(매치 카드의 닉네임 우클릭/길게 누르기 → 메모 편집) 도입 시점.
