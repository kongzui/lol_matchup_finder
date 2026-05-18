# 공통 DB 기반 매치업 검색 구조 재설계 메모

## 1. 지금까지의 맥락

현재 앱은 크게 두 가지 사용자 흐름을 가지고 있다.

1. `Riot ID 검색`
   - 사용자가 입력한 특정 Riot ID의 최근 솔로 랭크 matchId를 Riot API로 가져온다.
   - 각 match detail을 `match_cache`에 저장한다.
   - 검색 조건에 맞는 경우 즉석에서 결과를 뽑아 화면에 보여준다.

2. `챌린저 매치업`
   - KR 챌린저 목록을 seed로 가져온다.
   - 챌린저 유저별 최근 솔로 랭크 matchId를 수집한다.
   - matchId를 `set`으로 중복 제거한다.
   - match detail을 `match_cache`에 저장한다.
   - 현재는 저장된 raw JSON을 즉석 파싱해서 검색 결과를 만든다.

앞으로는 여기에 세 번째 흐름인 `DB조회`를 명확히 분리한다.

정리하면 목표 기능 단위는 다음 세 가지다.

1. `개별유저검색`
   - 특정 Riot ID를 기준으로 API에서 그 유저의 최근 매치를 가져온다.
   - 그 유저 중심의 결과를 즉시 보여준다.
   - 가져온 매치는 티어 조건을 통과한 경우에만 공통 DB 검색 자산으로 인덱싱한다.

2. `챌린저 검색`
   - 챌린저 목록과 챌린저 seed의 최근 매치를 API로 수집한다.
   - 특정 챔피언 매치업을 즉석에서 찾는 기능이 아니라, 고티어 매치 데이터를 쌓는 수집 기능에 가깝다.
   - 수집된 매치는 공통 DB에 저장되어 이후 DB조회에서 재사용된다.

3. `DB조회`
   - API를 호출하지 않는다.
   - 이미 공통 DB에 쌓인 매치업 인덱스만 검색한다.
   - `오리아나 vs 사일러스`, `아리 vs 카타리나` 같은 조건 변경은 DB 필터만 바뀐다.

즉, 현재 구조는 이미 `match_cache`를 통해 상세 매치 JSON을 재사용하고 있지만,
세 흐름이 함께 쓰는 검색용 DB 인덱스는 아직 없다.

## 2. 문제점

현재 방식의 가장 큰 문제는 API 수집, 개별 유저 결과 표시, DB 기반 조회의 역할이 섞여 있다는 점이다.

- 개별유저검색은 API 조회와 화면 결과 표시가 같이 일어난다. 이 자체는 괜찮지만, 가져온 매치가 공통 검색 자산으로 충분히 활용되지 않는다.
- 반대로 개별유저검색으로 조회한 모든 매치를 무조건 공통 DB에 넣으면, 닉네임 오입력이나 저티어 개인 조회가 고티어 매치업 DB를 흐릴 수 있다.
- 챌린저 검색은 이름상 검색처럼 보이지만, 실제로는 고티어 데이터를 수집하는 성격이 강하다.
- DB조회는 별도 기능으로 분리되어 있지 않아, 이미 저장된 매치업을 조건만 바꿔 빠르게 찾는 흐름이 약하다.
- `오리아나 vs 사일러스`를 찾은 뒤 같은 DB에 기록이 있어도,
  `아리 vs 카타리나`를 검색할 때는 다시 raw JSON을 훑는 구조에 가깝다.
- 챌린저 검색에서 얻은 매치와 개별 유저 검색에서 얻은 매치가
  같은 DB 자산으로 명확히 통합되어 있지 않다.
- Riot API는 챔피언/상대 챔피언 조건을 직접 필터링해주지 못하므로,
  필터링은 로컬 DB 기준으로 설계하는 편이 맞다.

## 3. 목표 구조

핵심 방향은 세 기능을 분리하되, DB 저장 구조는 공통으로 쓰는 것이다.

```text
개별유저검색 ─┐
챌린저 검색   ├─→ match_cache(raw JSON) → 조건 통과 → matchup_index(검색용) → DB조회
기존 캐시백필 ┘
```

각 기능의 역할은 이렇게 나눈다.

1. 개별유저검색
   - 사용자가 입력한 Riot ID 기준으로 최신 매치를 API에서 가져온다.
   - 해당 유저 관점의 검색 결과를 기존처럼 즉시 보여준다.
   - 해당 유저가 인덱싱 허용 티어 이상이면 가져온 match detail을 공통 DB에 저장하고 인덱싱한다.
   - 티어 기준 미만이면 화면 결과만 보여주고 공통 DB 인덱싱은 하지 않는다.

2. 챌린저 검색
   - 챌린저 랭킹과 seed 기반으로 고티어 match detail을 수집한다.
   - 수집된 match detail을 공통 DB에 저장하고 인덱싱한다.
   - 화면에서는 수집 진행률, 새 매치 수, 캐시 hit, API 호출 수를 보여준다.

3. DB조회
   - Riot API를 호출하지 않는다.
   - `matchup_index`를 기준으로 챔피언, 상대 챔피언, 라인, 기간, 패치 조건을 필터링한다.
   - 개별유저검색과 챌린저 검색이 과거에 인덱싱한 매치를 함께 조회한다.

4. 공통 DB/인덱서
   - matchId별 raw JSON은 `match_cache`에 저장한다.
   - 검색용 row는 `matchup_index`에 저장한다.
   - 어떤 경로로 들어온 match detail이든 같은 인덱싱 판단 규칙을 통과한다.

## 4. 중요한 요구사항

### 4.1 개별 유저 검색도 조건부로 공통 DB 자산이 되어야 한다

개별 유저 검색에서 가져온 매치는 티어 조건을 통과한 경우 `match_cache`에만 머물면 안 된다.

예를 들어 특정 유저 검색 과정에서 `아리 vs 카타리나` 경기가 포함된 match detail을 가져왔다면,
그 조회 대상 유저가 기준 티어 이상일 때 이후 `DB조회`에서 그 기록을 찾을 수 있어야 한다.

따라서 match detail이 저장되는 모든 경로는 공통 인덱싱 가능 여부를 판단해야 한다.

```text
개별 유저 검색으로 받은 match detail
→ match_cache 저장
→ 조회 대상 유저 티어 확인
→ 기준 이상이면 matchup_index 저장
→ 이후 DB조회에서 재사용
```

```text
챌린저 수집으로 받은 match detail
→ match_cache 저장
→ matchup_index 저장
→ 이후 DB조회에서 재사용
```

### 4.2 DB조회는 DB 기준이어야 한다

DB조회 버튼은 Riot API를 호출하지 않는다.

검색은 다음처럼 동작해야 한다.

```sql
SELECT *
FROM matchup_index
WHERE lane = 'MIDDLE'
  AND player_champion_key = 'Orianna'
  AND enemy_champion_key = 'Sylas';
```

나중에 `Ahri vs Katarina`를 검색해도 같은 테이블에서 바로 찾는다.

### 4.3 UI에서도 세 기능을 분리한다

UI의 논리적 영역은 다음처럼 나눈다.

1. 개별유저검색
   - 특정 Riot ID 입력.
   - 해당 유저 중심 결과 표시.
   - 티어 조건을 통과한 경우에만 공통 DB조회용 인덱스가 쌓인다.

2. 챌린저 검색
   - 챌린저/확장 seed 기준 데이터 수집.
   - 검색 결과 카드보다 수집 상태와 DB 적재 결과가 중심이다.

3. DB조회
   - 챔피언/상대 챔피언/라인/기간 조건으로 공통 DB를 조회한다.
   - API 호출 없이 기존 기록만 보여준다.

## 5. 추가할 테이블 초안

### 5.1 `player_registry`

매치에서 발견된 모든 플레이어를 저장한다.

```text
puuid PRIMARY KEY
riot_id_game_name
riot_id_tag_line
summoner_name
first_seen_at
last_seen_at
```

### 5.2 `collection_seed`

챌린저 검색에서 API 수집 대상으로 삼을 플레이어를 저장한다.
개별유저검색으로 조회된 유저도 필요하면 `manual_user` source로 등록할 수 있지만,
개별유저검색 자체가 이 테이블에 의존하면 안 된다.

```text
puuid
source
source_champion_key
priority
is_active
last_collected_at
last_seen_as_source_at
inactive_at
created_at
updated_at
PRIMARY KEY (puuid, source, source_champion_key)
```

`source` 예시:

- `challenger`
- `observed_top3`
- `manual_user`

`challenger` source의 `is_active`는 현재 챌린저 랭킹에 남아 있는지를 의미한다.
랭킹 밖으로 밀려난 유저는 삭제하지 않고 `is_active = 0`, `inactive_at`만 갱신한다.

### 5.3 `challenger_snapshot_runs`

챌린저 랭킹을 가져온 실행 단위를 저장한다.

```text
id INTEGER PRIMARY KEY AUTOINCREMENT
queue
top_n
fetched_at
```

### 5.4 `challenger_player_snapshots`

챌린저 랭킹 스냅샷의 개별 플레이어 row를 저장한다.

```text
snapshot_id
puuid
rank
league_points
wins
losses
PRIMARY KEY (snapshot_id, puuid)
```

이 테이블은 랭킹 이력을 보존하기 위한 테이블이다.
어제 300위였지만 오늘 300위 밖으로 밀려난 유저도 과거 스냅샷에는 남는다.

### 5.5 `challenger_players_current`

현재 챌린저 랭킹 상태를 빠르게 보기 위한 캐시성 테이블이다.

```text
puuid PRIMARY KEY
rank
league_points
wins
losses
is_current
last_snapshot_id
last_seen_at
```

용도:

- UI에서 현재 챌린저 상태를 빠르게 보여준다.
- `collection_seed`의 `challenger` source 활성/비활성 판단에 사용한다.
- 과거 이력의 원본은 `challenger_player_snapshots`에 남긴다.

### 5.6 `ranked_profile_cache`

개별유저검색에서 공통 DB 인덱싱 여부를 판단하기 위한 솔로 랭크 정보를 저장한다.

```text
puuid
queue_id
tier
rank
league_points
wins
losses
fetched_at
PRIMARY KEY (puuid, queue_id)
```

용도:

- 개별유저검색에서 조회 대상 유저가 기준 티어 이상인지 판단한다.
- 같은 유저를 반복 조회할 때 랭크 확인 API 호출을 줄인다.
- Riot ID는 바뀔 수 있으므로 판단 기준은 Riot ID 문자열이 아니라 PUUID로 둔다.

### 5.7 `match_discovery`

어떤 흐름이 어떤 matchId를 발견했는지 저장한다.
챌린저 검색, 개별유저검색, 백필이 모두 같은 matchId를 발견할 수 있으므로 출처 추적용으로 둔다.

```text
match_id
source_puuid
source
discovered_at
PRIMARY KEY (match_id, source_puuid, source)
```

`source` 예시:

- `challenger`
- `manual_user`
- `cache_backfill`

### 5.8 `matchup_index`

검색용 핵심 테이블이다.

```text
id INTEGER PRIMARY KEY AUTOINCREMENT
match_id
queue_id
game_creation
game_version
lane
player_puuid
player_riot_id
player_game_name
player_tag_line
player_champion_key
enemy_puuid
enemy_riot_id
enemy_game_name
enemy_tag_line
enemy_champion_key
win
kills
deaths
assists
cs
gold_earned
damage_to_champions
game_duration
player_champion_level
player_summoner1_id
player_summoner2_id
player_items_json
player_primary_tree_id
player_primary_runes_json
player_secondary_tree_id
player_secondary_runes_json
indexed_at
UNIQUE (match_id, player_puuid)
```

한 매치에서 10명 전체를 검사하므로, 정상적인 소환사의 협곡 게임이면 최대 10개 row가 생긴다.
각 row는 한 플레이어 관점의 매치업이다.

### 5.9 `player_champion_stats`

로컬 DB에 관측된 기록 기준으로 플레이어별 챔피언 판수를 저장한다.

```text
puuid
champion_key
games
wins
last_played_at
updated_at
PRIMARY KEY (puuid, champion_key)
```

이 테이블은 `observed_top3` seed를 만들 때 사용한다.

## 6. 인덱싱 규칙

1. `queueId`는 기본적으로 솔로 랭크 `420`만 인덱싱한다.
2. `teamPosition`이 비어 있는 참가자는 건너뛴다.
3. 상대팀에서 같은 `teamPosition`을 가진 플레이어를 찾지 못하면 건너뛴다.
4. `matchup_index`는 한 플레이어 관점으로 저장한다.
5. 같은 경기를 다시 인덱싱해도 `UNIQUE (match_id, player_puuid)` 기준으로 덮어쓴다.
6. 아이템/룬 배열은 JSON TEXT로 저장하고, 화면 출력 시 list로 복원한다.
7. `match_cache`는 원본 보관용, `matchup_index`는 검색용으로 역할을 나눈다.
8. 개별유저검색으로 가져온 match detail은 조회 대상 유저가 인덱싱 허용 티어 이상일 때만 `matchup_index`에 반영한다.
9. 챌린저 검색으로 가져온 match detail은 수집 목적 자체가 고티어 데이터이므로 별도 티어 게이트 없이 인덱싱한다.
10. `match_cache`에 raw JSON이 있다는 사실만으로 DB조회 대상이 되지는 않는다. DB조회 대상 여부는 `matchup_index`에 row가 있는지로 결정한다.
11. 기존 `match_cache` 백필은 무조건 전체 인덱싱하지 않는다. 출처가 챌린저이거나, 개별유저검색 출처의 조회 대상 유저가 티어 조건을 통과한 경우만 인덱싱한다.

## 7. 개별유저검색의 역할

개별유저검색은 기존 사용자 경험을 유지하면서, 공통 DB에 데이터를 공급하는 역할도 한다.

- 사용자는 Riot ID를 입력한다.
- 해당 유저의 matchId를 Riot API에서 가져온다.
- 없는 match detail만 API로 가져와 `match_cache`에 저장한다.
- 조회 대상 유저의 솔로 랭크 티어를 확인한다.
- 기준 티어 이상이면 새로 저장되거나 아직 인덱싱되지 않은 match detail을 `matchup_index`에 반영한다.
- 기준 티어 미만이거나 랭크 정보가 없으면 화면 결과만 보여주고 공통 DB 인덱싱은 건너뛴다.
- 화면 결과는 기존처럼 해당 유저 중심으로 보여준다.

`match_cache` 저장과 `matchup_index` 저장은 분리해서 본다.

- `match_cache`: 현재 개별유저검색 화면을 빠르게 다시 보여주기 위한 raw JSON 캐시로 쓸 수 있다.
- `matchup_index`: DB조회에 노출되는 검색 자산이다.
- 저티어 또는 랭크 정보 없음 사용자의 매치는 `match_cache`에 남아 있을 수 있지만, `matchup_index`에 넣지 않으면 DB조회 결과에는 나오지 않는다.

중요한 점은 개별유저검색의 결과 표시 기준과 DB조회 기준을 섞지 않는 것이다.

- 개별유저검색 결과: 입력한 유저가 선택 챔피언을 플레이한 경기만 보여준다.
- 공통 DB 인덱싱: 입력 유저가 티어 조건을 통과한 경우에만 해당 match detail 안의 10명 전체를 인덱싱한다.
- DB조회: 나중에 어떤 챔피언 조합이든 `matchup_index`에서 다시 찾는다.

개별유저검색의 인덱싱 티어 게이트:

- 화면 조회 자체는 티어와 관계없이 허용한다.
- 공통 DB 인덱싱 여부만 티어로 제한한다.
- 기본 기준은 `DIAMOND` 이상으로 둔다.
- 기준 티어는 상수로 분리해 나중에 `MASTER` 이상 또는 `EMERALD` 이상으로 쉽게 바꿀 수 있게 한다.

## 8. 챌린저 검색의 역할

챌린저 검색은 이름은 검색이지만, 구조상 고티어 데이터 수집 작업으로 본다.
즉시 특정 매치업 결과를 찾는 기능은 `DB조회`가 담당한다.

흐름:

```text
챌린저 목록 조회
→ challenger_snapshot_runs 생성
→ challenger_player_snapshots 저장
→ challenger_players_current 갱신
→ collection_seed 갱신
→ seed별 최근 matchId 조회
→ match_discovery 저장
→ match_cache에 없는 match detail만 API 호출
→ match_cache 저장
→ matchup_index 갱신
→ player_champion_stats 갱신
```

처음에는 챌린저만 기본 seed로 둔다.
이후 DB 관측 기준으로 특정 유저의 특정 챔피언이 top3 안에 들어오면
`observed_top3` seed로 확장할 수 있게 한다.

### 8.1 챌린저 랭킹 변동 처리

챌린저 랭킹은 매일 바뀌므로, 현재 랭킹과 과거 관측 이력을 분리한다.

1. 새로 챌린저에 들어온 유저
   - 이번 스냅샷에 처음 등장한 PUUID는 `challenger_players_current`에 추가한다.
   - `collection_seed`에 `source = challenger`, `is_active = 1`로 추가한다.
   - 다음 수집부터 최근 matchId를 가져온다.

2. 어제까지 상위 300명이었지만 오늘 밖으로 밀려난 유저
   - `challenger_player_snapshots`의 과거 기록은 삭제하지 않는다.
   - `challenger_players_current.is_current = 0`으로 바꾼다.
   - `collection_seed`의 `challenger` source는 `is_active = 0`, `inactive_at = now`로 바꾼다.
   - 그 유저의 과거 `match_cache`, `matchup_index` row는 삭제하지 않는다.
   - 다만 `challenger` source로는 새 matchId를 더 수집하지 않는다.

3. 다시 챌린저로 복귀한 유저
   - `challenger_players_current.is_current = 1`로 갱신한다.
   - 기존 `collection_seed`를 재사용해 `is_active = 1`, `inactive_at = NULL`로 되살린다.
   - `last_collected_at` 이후의 matchId만 이어서 수집한다.

4. 랭킹 밖이지만 observed top3 조건을 만족하는 유저
   - `challenger` source는 비활성일 수 있다.
   - 별도로 `observed_top3` source가 활성이라면 그 source 기준으로 계속 수집할 수 있다.
   - 하나의 PUUID가 여러 source를 가질 수 있으므로 source별 활성 상태를 따로 관리한다.

DB조회는 “현재 챌린저인가”가 아니라 “인덱싱된 매치업인가”를 기본 기준으로 삼는다.
필요하면 나중에 `현재 챌린저만`, `수집 당시 챌린저`, `전체 인덱스` 같은 조회 필터를 추가할 수 있다.

챌린저 검색은 다음 데이터를 만들어내는 것이 성공 기준이다.

- 새로 발견한 matchId 수
- 새로 저장한 match detail 수
- 캐시 hit 수
- 새로 또는 다시 생성한 `matchup_index` row 수
- 새로 들어온 챌린저 수
- 랭킹 밖으로 나가 비활성화된 챌린저 seed 수
- 갱신된 챌린저 스냅샷/seed/player champion stats 수

## 9. DB조회 역할

DB조회는 API 호출 없이 동작한다.
개별유저검색과 챌린저 검색이 쌓아둔 공통 DB를 조회하는 읽기 전용 기능이다.

검색 조건:

- 내 챔피언
- 상대 챔피언 또는 전체
- 라인
- 기간
- 이번 패치만 여부
- 최대 결과 수
- 수집/랭킹 범위 필터는 v1에서는 기본적으로 전체 인덱스를 대상으로 한다.

검색 결과는 기존 `render_challenger_results`가 기대하는 row 형태와 최대한 맞춘다.
그래야 화면 렌더링과 CSV 다운로드 변경 범위를 줄일 수 있다.

단, 이름상 `challenger_results`에 묶이는 구조는 장기적으로 어색하므로,
구현 시에는 가능하면 `IndexedMatchupSearchPayload`처럼 DB조회 전용 payload를 두고
렌더링은 재사용 가능한 결과 카드 컴포넌트를 공유한다.

추후 추가 가능한 DB조회 필터:

- `전체 인덱스`: 기본값. 개별유저검색과 챌린저 검색으로 인덱싱된 모든 매치업.
- `수집 당시 챌린저`: 해당 매치가 챌린저 source 수집으로 발견된 경우.
- `현재 챌린저`: 조회 시점의 `challenger_players_current.is_current = 1`인 플레이어만.

기본값은 `전체 인덱스`로 둔다.
랭킹이 변동되어도 과거에 합법적으로 수집된 매치업 이력을 잃지 않기 위해서다.

## 10. 구현 순서

1. `cache.py`에 새 테이블 생성 SQL과 저장/조회 메서드를 추가한다.
2. match detail 하나를 받아 `matchup_index` row로 변환하는 공통 인덱서 함수를 만든다.
3. `save_match` 이후 인덱싱을 호출할 수 있는 흐름을 만든다.
4. 개별유저검색에서 조회 대상 유저의 솔로 랭크 티어를 확인하고, 기준 이상일 때만 인덱싱되도록 연결한다.
5. 챌린저 검색 전용 요청/응답 dataclass와 서비스를 만든다.
6. DB조회 전용 요청/응답 dataclass와 서비스를 만든다.
7. UI를 `개별유저검색`, `챌린저 검색`, `DB조회`의 세 역할로 분리한다.
8. 기존 `match_cache`에 들어 있는 과거 데이터 백필 기능을 추가하되, 출처/티어 조건을 확인할 수 없는 매치는 기본적으로 인덱싱하지 않는다.
9. `ruff format .`, `ruff check .`로 검증한다.

## 11. 주의할 점

- 기존 Riot ID 단일 검색의 화면 경험은 최대한 유지한다.
- `match_cache`의 raw JSON 구조를 바꾸지 않는다.
- DB조회 버튼에서 Riot API를 호출하지 않도록 역할을 분리한다.
- 챌린저 검색은 오래 걸릴 수 있으므로 진행률과 API 호출 수를 화면에 보여준다.
- 이미 캐시된 match detail은 다시 API로 가져오지 않는다.
- 챌린저 검색과 개별유저검색이 같은 matchId를 발견해도 상세 데이터와 인덱스는 중복 저장되지 않아야 한다.
- 개별유저검색의 결과 기준과 DB조회 결과 기준을 섞지 않는다.
- 개별유저검색은 티어 미달이어도 화면 조회는 정상 제공하고, 공통 DB 인덱싱만 건너뛴다.
- legacy `match_cache` 백필은 DB 오염을 막기 위해 보수적으로 처리한다.
- 챌린저 랭킹에서 이탈한 유저의 과거 인덱스는 삭제하지 않고, 새 수집 대상에서만 제외한다.

## 12. 현재 확정된 기본값

- 기본 수집 대상: KR 챌린저
- DB조회 대상: 수집된 매치의 전체 10명
- DB조회 방식: DB-only
- DB조회 기본 랭킹 범위: 전체 인덱스
- API 호출 위치: 개별유저검색 또는 챌린저 검색
- 개별유저검색 공통 DB 인덱싱 기준: 조회 대상 유저의 솔로 랭크 `DIAMOND` 이상
- observed top3 기준: Riot mastery API가 아니라 로컬 DB 관측 기록
- 기존 단일 Riot ID 검색: 유지하되, 티어 조건을 통과한 경우에만 가져온 매치를 공통 인덱스에 반영
