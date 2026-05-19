export interface ChampionOption {
  koreanName: string;
  key: string;
  iconUrl: string;
}

export interface MetaOptions {
  champions: ChampionOption[];
  laneOptions: string[];
  periodOptions: string[];
  defaultPeriod: string;
  customPeriodLabel: string;
  multiPeriodOptions: number[];
  multiMatchesPerPlayerOptions: number[];
  defaultMultiDays: number;
  defaultMultiMatchesPerPlayer: number;
  dbEnemyAllLabel: string;
  dataDragonVersion: string;
  staticDataVersion: string;
  apiKeyDetected: boolean;
  region: string;
  platform: string;
  queueId: number;
  latestSearchRiotId: string | null;
}

export interface SearchBody {
  riotIdRaw: string;
  periodKind: string;
  customStart: string | null;
  customEnd: string | null;
  myChampionKorean: string;
  enemyChampionKorean: string;
  laneLabel: string;
  maxMatches: number;
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
  page: number;
  pageSize: number;
}

export interface JobStatus {
  jobId: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  message: string;
  error: string | null;
}

export interface MatchRow {
  match_id?: string;
  game_creation?: number;
  game_date?: string;
  my_champion_key?: string;
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
}

export interface SearchPayload {
  results: MatchRow[];
  scanned_total: number;
  cache_hits: number;
  api_calls: number;
  account: {
    puuid: string;
    game_name: string;
    tag_line: string;
  };
  my_champion_key: string;
  enemy_champion_key: string;
  lane_label: string;
  period_kind: string;
  indexed_rows: number;
  index_allowed: boolean;
  index_tier: string | null;
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
  period_label: string;
  matches_per_player: number;
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
  my_champion_key: string;
  enemy_champion_key: string | null;
  lane_label: string;
  period_kind: string;
  patch_prefix: string | null;
}

export interface JobPayload {
  kind: string;
  payload: SearchPayload | MultiSearchPayload;
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
}
