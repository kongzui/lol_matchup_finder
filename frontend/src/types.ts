export interface ChampionOption {
  koreanName: string;
  key: string;
  iconUrl: string;
}

export interface MetaOptions {
  champions: ChampionOption[];
  laneOptions: string[];
  customPeriodLabel: string;
  dbPeriodOptions: string[];
  defaultDbPeriod: string;
  multiPeriodOptions: number[];
  multiMatchesPerPlayerOptions: number[];
  defaultMultiDays: number;
  defaultMultiMatchesPerPlayer: number;
  allLabel: string;
  dbEnemyAllLabel: string;
  dataDragonVersion: string;
  staticDataVersion: string;
  apiKeyDetected: boolean;
  region: string;
  platform: string;
  queueId: number;
  latestSearchRiotId: string | null;
  dbMatchCount: number;
  dbLastMatchCreation: number | null;
  dbIndexedMatchCount: number;
  dbIndexRowCount: number;
  dbIndexMaxId: number;
  dbLastIndexedMatchCreation: number | null;
  autoRefresh: {
    status:
      | "not_checked"
      | "checking"
      | "skipped"
      | "ready"
      | "running"
      | "succeeded"
      | "partial"
      | "failed"
      | "stale";
    message: string;
    jobId: string | null;
    targetCount: number;
    plannedTargetCount: number;
    days: number | null;
    matchesPerPlayer: number | null;
    lastCollectedAt: number | null;
    requiresConfirmation: boolean;
    successCount: number | null;
    failureCount: number | null;
    failures: Array<{ riot_id_raw: string; reason: string }>;
  };
}

export interface MultiSearchBody {
  riotIdsRaw: string;
  days: number;
  matchesPerPlayer: number;
}

export interface DbSearchBody {
  myChampionKorean: string;
  enemyChampionKorean: string;
  laneLabel: string;
  periodKind: string;
  customStart: string | null;
  customEnd: string | null;
  currentPatchOnly: boolean;
  riotIdsRaw: string;
  targetPuuids?: string[];
  page: number;
  pageSize: number;
  snapshot?: DbSearchSnapshot;
}

export interface DbSearchSnapshot {
  targetPuuids: string[];
  startTs: number;
  endTs: number;
  patchPrefix: string | null;
  maxIndexId: number;
  targetLabel: string;
}

export interface JobStatus {
  jobId: string;
  kind: string;
  origin: string;
  title: string;
  targetSummary: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed";
  progress: number;
  message: string;
  error: string | null;
  createdAt: number;
  startedAt: number | null;
  updatedAt: number;
  finishedAt: number | null;
  resultAvailable: boolean;
}

export interface JobListPayload {
  serverInstanceId: string;
  serverStartedAt: number;
  activeJobs: JobStatus[];
  recentJobs: JobStatus[];
}

export interface MatchRow {
  match_id?: string;
  game_creation?: number;
  game_date?: string;
  my_champion_key?: string;
  enemy_puuid?: string;
  enemy_champion_key?: string;
  enemy_riot_id?: string;
  enemy_game_name?: string;
  enemy_tag_line?: string;
  player_riot_id?: string;
  player_game_name?: string;
  player_tag_line?: string;
  player_puuid?: string;
  win: boolean;
  kills: number;
  deaths: number;
  assists: number;
  cs: number;
  gold_earned?: number;
  damage_to_champions?: number;
  game_duration?: number;
  game_version?: string;
  my_champion_level?: number;
  my_items?: number[];
  mySummoner1IconUrl?: string | null;
  mySummoner2IconUrl?: string | null;
  myPrimaryTreeIconUrl?: string | null;
  mySecondaryTreeIconUrl?: string | null;
  myPrimaryRuneIconUrls?: Array<string | null>;
  mySecondaryRuneIconUrls?: Array<string | null>;
  myStatShardIconUrls?: Array<string | null>;
  myStatShardNames?: string[];
}

export interface MultiSearchPayload {
  input_count: number;
  success_count: number;
  failure_count: number;
  discovered_matches: number;
  new_match_details: number;
  cache_hits: number;
  api_calls: number;
  indexed_rows: number;
  continuation_advanced_count: number;
  period_label: string;
  matches_per_player: number;
  resolved_users: MemoTarget[];
  memos: Record<string, string>;
  failures: Array<{ riot_id_raw: string; reason: string }>;
}

export interface DbSearchPayload {
  results: MatchRow[];
  total: number;
  page: number;
  pageSize: number;
  stats: {
    total: number;
    wins: number;
    losses: number;
    winRate: number | null;
  };
  my_champion_key: string | null;
  enemy_champion_key: string | null;
  lane_label: string;
  period_kind: string;
  patch_prefix: string | null;
  cache_hits: number;
  api_calls: number;
  target_label: string;
  memos: Record<string, string>;
  snapshot: DbSearchSnapshot;
}

export interface JobPayload {
  kind: string;
  payload: MultiSearchPayload;
}

export interface MemoTarget {
  puuid: string;
  riot_id: string;
}

export interface PlayerMemo {
  puuid: string;
  memo: string;
  auto_refresh_enabled: boolean;
  updated_at: number;
  riot_id_game_name?: string | null;
  riot_id_tag_line?: string | null;
  summoner_name?: string | null;
  last_seen_at?: number | null;
  last_collected_at?: number | null;
  pending_collection_target_at?: number | null;
  collection_status?:
    | "current"
    | "needs_collection"
    | "not_collected"
    | "continuation_needed";
  game_count: number;
}

export interface ResolvePlayerMemoPayload {
  puuid: string;
  gameName: string;
  tagLine: string;
}

export interface DetailPlayer {
  puuid?: string;
  team_id?: number;
  team_position?: string;
  champion_key?: string;
  championNameKo: string;
  championIconUrl: string;
  riot_id_game_name?: string;
  riot_id_tag_line?: string;
  summoner_name?: string;
  rankLabel?: string;
  rankedProfile?: {
    tier?: string | null;
    rank?: string | null;
    league_points?: number | null;
    wins?: number | null;
    losses?: number | null;
  } | null;
  win: boolean;
  kills: number;
  deaths: number;
  assists: number;
  cs: number;
  gold: number;
  damage: number;
  vision: number;
  champion_level: number;
  summoner1IconUrl?: string | null;
  summoner2IconUrl?: string | null;
  summoner1Name?: string;
  summoner2Name?: string;
  itemIconUrls: Array<string | null>;
  primaryRuneIconUrls: Array<string | null>;
  secondaryRuneIconUrls: Array<string | null>;
  primaryRuneNames?: string[];
  secondaryRuneNames?: string[];
  primaryTreeIconUrl?: string | null;
  secondaryTreeIconUrl?: string | null;
  primaryTreeName?: string;
  secondaryTreeName?: string;
  statShardIconUrls: Array<string | null>;
  statShardNames?: string[];
  primaryRunePage?: RunePage | null;
  secondaryRunePage?: RunePage | null;
  statShardPage?: RuneEntry[][];
}

export interface RuneEntry {
  id: number;
  name: string;
  iconUrl?: string | null;
  selected: boolean;
}

export interface RunePage {
  treeId: number;
  treeName: string;
  treeIconUrl?: string | null;
  slots: RuneEntry[][];
}

export interface TeamSummary extends DetailPlayer {}

export interface MatchDetail {
  queueId: number;
  gameDuration: number;
  gameVersion: string;
  me: DetailPlayer;
  enemyLaner: DetailPlayer | null;
  othersAlly: TeamSummary[];
  othersEnemy: TeamSummary[];
  buildTimeline: {
    item_events: Array<{
      item_id: number;
      minute: number;
      timestamp: number;
      icon_url?: string | null;
    }>;
    skill_events: Array<{
      label: string;
      level: number;
      timestamp: number;
      skill_slot?: number;
      skillSlot?: number;
      iconUrl?: string | null;
      spellName?: string;
    }>;
  } | null;
  timelineError: string | null;
  detailLoad: {
    timelineSource: "not_requested" | "stored" | "downloaded" | "unavailable";
    rankCacheHits: number;
    externalApiCalls: number;
    rankError: string | null;
  };
}

export interface MatchBuildDetail {
  buildTimeline: MatchDetail["buildTimeline"];
  timelineError: string | null;
  detailLoad: {
    timelineSource: "stored" | "downloaded" | "unavailable";
    externalApiCalls: number;
  };
}

export interface MatchRankDetail {
  me: DetailPlayer;
  enemyLaner: DetailPlayer | null;
  othersAlly: TeamSummary[];
  othersEnemy: TeamSummary[];
  detailLoad: {
    rankCacheHits: number;
    externalApiCalls: number;
    rankError: string | null;
  };
}
