import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  Database,
  Download,
  ExternalLink,
  Search,
  Server,
  UploadCloud,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, searchCsvUrl } from "./api";
import type {
  DbSearchBody,
  DbSearchPayload,
  DetailPlayer,
  JobStatus,
  MatchDetail,
  MatchRow,
  MetaOptions,
  MultiSearchBody,
  MultiSearchPayload,
  RuneEntry,
  RunePage,
  SearchBody,
  SearchPayload,
  TeamSummary,
} from "./types";

type Workspace = "search" | "multi" | "db";
type DetailTab = "summary" | "build";

const PAGE_SIZE = 50;
const LANE_ORDER: Record<string, number> = {
  TOP: 0,
  JUNGLE: 1,
  MIDDLE: 2,
  BOTTOM: 3,
  UTILITY: 4,
};

function todayText(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoText(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

function championIcon(version: string, key?: string): string {
  return key
    ? `https://ddragon.leagueoflegends.com/cdn/${version}/img/champion/${key}.png`
    : "";
}

function itemIcon(version: string, itemId: number): string {
  return `https://ddragon.leagueoflegends.com/cdn/${version}/img/item/${itemId}.png`;
}

function opggUrl(gameName?: string, tagLine?: string): string {
  if (!gameName || !tagLine) {
    return "";
  }
  return `https://op.gg/ko/lol/summoners/kr/${encodeURIComponent(
    gameName,
  )}-${encodeURIComponent(tagLine)}`;
}

function splitRiotId(value?: string): { name: string; tag: string } {
  const [name = "", tag = ""] = (value || "").split("#", 2);
  return { name, tag };
}

function riotId(name?: string, tag?: string, fallback?: string): string {
  if (name && tag) {
    return `${name}#${tag}`;
  }
  return fallback || name || "-";
}

function secondsToMinutes(seconds?: number): string {
  return `${Math.max(Math.floor((seconds || 0) / 60), 0)}분`;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function useJobPoller(
  jobId: string | null,
  onDone: (status: JobStatus) => Promise<void>,
) {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api.getJob(jobId);
        if (cancelled) {
          return;
        }
        setJob(status);
        if (status.status === "succeeded") {
          await onDone(status);
          return;
        }
        if (status.status === "failed") {
          setError(status.error || "작업에 실패했습니다.");
          return;
        }
        window.setTimeout(poll, 900);
      } catch (exc) {
        if (!cancelled) {
          setError(
            exc instanceof Error ? exc.message : "job 조회에 실패했습니다.",
          );
        }
      }
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return { job, error, setError };
}

export default function App() {
  const [meta, setMeta] = useState<MetaOptions | null>(null);
  const [active, setActive] = useState<Workspace>("search");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getMeta()
      .then(setMeta)
      .catch((exc) =>
        setError(exc instanceof Error ? exc.message : "메타데이터 로딩 실패"),
      );
  }, []);

  if (error) {
    return <FatalError message={error} />;
  }

  if (!meta) {
    return <div className="boot">메타데이터를 불러오는 중...</div>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>LoL</span>
          <strong>Matchup Finder</strong>
        </div>
        <nav className="nav-list" aria-label="작업공간">
          <NavButton
            active={active === "search"}
            icon={<Search size={18} />}
            label="개별유저검색"
            onClick={() => setActive("search")}
          />
          <NavButton
            active={active === "multi"}
            icon={<UploadCloud size={18} />}
            label="멀티서치"
            onClick={() => setActive("multi")}
          />
          <NavButton
            active={active === "db"}
            icon={<Database size={18} />}
            label="DB조회"
            onClick={() => setActive("db")}
          />
        </nav>
        <div className="server-card">
          <div className="server-title">
            <Server size={16} />
            서버 상태
          </div>
          <span className={meta.apiKeyDetected ? "status ok" : "status danger"}>
            {meta.apiKeyDetected ? "RIOT_API_KEY 감지됨" : "RIOT_API_KEY 없음"}
          </span>
          <p>
            {meta.region} · {meta.platform} · queue {meta.queueId}
          </p>
          <p>Data Dragon {meta.dataDragonVersion}</p>
        </div>
      </aside>

      <main className="workspace">
        {active === "search" && <SearchWorkspace meta={meta} />}
        {active === "multi" && <MultiCollectWorkspace meta={meta} />}
        {active === "db" && <DbLookupWorkspace meta={meta} />}
      </main>
    </div>
  );
}

function FatalError({ message }: { message: string }) {
  return (
    <div className="fatal">
      <strong>앱을 시작할 수 없습니다.</strong>
      <p>{message}</p>
    </div>
  );
}

function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={`nav-button ${active ? "active" : ""}`}
      onClick={onClick}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function SearchWorkspace({ meta }: { meta: MetaOptions }) {
  const [body, setBody] = useState<SearchBody>({
    riotIdRaw: meta.latestSearchRiotId || "",
    periodKind: meta.defaultPeriod,
    customStart: daysAgoText(90),
    customEnd: todayText(),
    myChampionKorean: meta.champions.find(
      (champ) => champ.koreanName === "아리",
    )
      ? "아리"
      : meta.champions[0].koreanName,
    enemyChampionKorean: meta.champions.find(
      (champ) => champ.koreanName === "사일러스",
    )
      ? "사일러스"
      : meta.champions[0].koreanName,
    laneLabel: "미드",
    maxMatches: 100,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [payload, setPayload] = useState<SearchPayload | null>(null);

  const poller = useJobPoller(jobId, async (status) => {
    const result = await api.getJobResult(status.jobId);
    setPayload(result.payload as SearchPayload);
  });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPayload(null);
    poller.setError(null);
    const created = await api.createSearchJob(body);
    setJobId(created.jobId);
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="개별유저검색"
        title="Riot ID 기준 매치업 검색"
        description="최근 솔로 랭크에서 내가 고른 챔피언과 상대 라이너 조건이 맞는 경기만 찾습니다."
      />
      <form className="control-panel" onSubmit={submit}>
        <label className="field wide">
          <span>Riot ID</span>
          <input
            value={body.riotIdRaw}
            onChange={(event) =>
              setBody({ ...body, riotIdRaw: event.target.value })
            }
            placeholder="Hide on bush#KR1"
          />
        </label>
        <ChampionSelect
          label="내 챔피언"
          value={body.myChampionKorean}
          meta={meta}
          onChange={(value) => setBody({ ...body, myChampionKorean: value })}
        />
        <ChampionSelect
          label="상대 챔피언"
          value={body.enemyChampionKorean}
          meta={meta}
          onChange={(value) => setBody({ ...body, enemyChampionKorean: value })}
        />
        <SelectField
          label="라인"
          value={body.laneLabel}
          options={meta.laneOptions}
          onChange={(value) => setBody({ ...body, laneLabel: value })}
        />
        <SelectField
          label="기간"
          value={body.periodKind}
          options={meta.periodOptions}
          onChange={(value) => setBody({ ...body, periodKind: value })}
        />
        <SelectField
          label="최대 매치"
          value={String(body.maxMatches)}
          options={["50", "100", "200", "300"]}
          onChange={(value) => setBody({ ...body, maxMatches: Number(value) })}
        />
        {body.periodKind === meta.customPeriodLabel && (
          <>
            <DateField
              label="시작일"
              value={body.customStart || ""}
              onChange={(value) => setBody({ ...body, customStart: value })}
            />
            <DateField
              label="종료일"
              value={body.customEnd || ""}
              onChange={(value) => setBody({ ...body, customEnd: value })}
            />
          </>
        )}
        <button className="primary-action" type="submit">
          <Search size={18} />
          검색 실행
        </button>
      </form>

      <JobProgress job={poller.job} error={poller.error} />
      {payload && (
        <>
          <SearchSummary payload={payload} />
          <div className="toolbar">
            <a className="tool-button" href={searchCsvUrl(jobId || "")}>
              <Download size={16} />
              CSV 다운로드
            </a>
          </div>
          <MatchResultList
            rows={payload.results}
            meta={meta}
            focusPuuid={payload.account.puuid}
            showPlayerCopy={false}
          />
        </>
      )}
    </section>
  );
}

function MultiCollectWorkspace({ meta }: { meta: MetaOptions }) {
  const [body, setBody] = useState<MultiSearchBody>({
    riotIdsRaw: "",
    days: meta.defaultMultiDays,
    matchesPerPlayer: meta.defaultMultiMatchesPerPlayer,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [payload, setPayload] = useState<MultiSearchPayload | null>(null);

  const poller = useJobPoller(jobId, async (status) => {
    const result = await api.getJobResult(status.jobId);
    setPayload(result.payload as MultiSearchPayload);
  });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPayload(null);
    poller.setError(null);
    const created = await api.createMultiSearchJob(body);
    setJobId(created.jobId);
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="멀티서치"
        title="여러 Riot ID 매치 수집"
        description="직접 입력한 유저들의 솔로 랭크 match_cache를 저장하고 matchup_index에 반영합니다."
      />
      <form className="control-panel multi" onSubmit={submit}>
        <label className="field textarea-field">
          <span>Riot ID 목록</span>
          <textarea
            value={body.riotIdsRaw}
            onChange={(event) =>
              setBody({ ...body, riotIdsRaw: event.target.value })
            }
            placeholder={"Aoo#chi\nHide on bush#KR1"}
          />
        </label>
        <SelectField
          label="수집 기간"
          value={String(body.days)}
          options={meta.multiPeriodOptions.map(String)}
          format={(value) => `최근 ${value}일`}
          onChange={(value) => setBody({ ...body, days: Number(value) })}
        />
        <SelectField
          label="1인당 최대 매치"
          value={String(body.matchesPerPlayer)}
          options={meta.multiMatchesPerPlayerOptions.map(String)}
          format={(value) => `${value}판`}
          onChange={(value) =>
            setBody({ ...body, matchesPerPlayer: Number(value) })
          }
        />
        <button className="primary-action" type="submit">
          <UploadCloud size={18} />
          수집 실행
        </button>
      </form>
      <JobProgress job={poller.job} error={poller.error} />
      {payload && <MultiSummary payload={payload} />}
    </section>
  );
}

function DbLookupWorkspace({ meta }: { meta: MetaOptions }) {
  const [body, setBody] = useState<DbSearchBody>({
    myChampionKorean: meta.champions.find(
      (champ) => champ.koreanName === "아리",
    )
      ? "아리"
      : meta.champions[0].koreanName,
    enemyChampionKorean: meta.dbEnemyAllLabel,
    laneLabel: "미드",
    periodKind: meta.defaultPeriod,
    customStart: daysAgoText(90),
    customEnd: todayText(),
    currentPatchOnly: false,
    page: 1,
    pageSize: PAGE_SIZE,
  });
  const [payload, setPayload] = useState<DbSearchPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runSearch = async (nextBody = body) => {
    setError(null);
    try {
      setPayload(await api.dbSearch(nextBody));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "DB조회에 실패했습니다.");
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextBody = { ...body, page: 1 };
    setBody(nextBody);
    runSearch(nextBody);
  };

  const movePage = (page: number) => {
    const nextBody = { ...body, page };
    setBody(nextBody);
    runSearch(nextBody);
  };

  const exportCsv = async () => {
    const blob = await api.exportDbSearch(body);
    downloadBlob(blob, "db_matchup_results.csv");
  };

  const pageCount = payload
    ? Math.max(Math.ceil(payload.total / PAGE_SIZE), 1)
    : 1;

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="DB조회"
        title="matchup_index 빠른 조회"
        description="Riot API 호출 없이 로컬 DB 인덱스만 사용해 전체 결과 통계와 목록을 확인합니다."
      />
      <form className="control-panel" onSubmit={submit}>
        <ChampionSelect
          label="내 챔피언"
          value={body.myChampionKorean}
          meta={meta}
          onChange={(value) => setBody({ ...body, myChampionKorean: value })}
        />
        <SelectField
          label="상대 챔피언"
          value={body.enemyChampionKorean}
          options={[
            meta.dbEnemyAllLabel,
            ...meta.champions.map((champ) => champ.koreanName),
          ]}
          onChange={(value) => setBody({ ...body, enemyChampionKorean: value })}
        />
        <SelectField
          label="라인"
          value={body.laneLabel}
          options={meta.laneOptions}
          onChange={(value) => setBody({ ...body, laneLabel: value })}
        />
        <SelectField
          label="기간"
          value={body.periodKind}
          options={meta.periodOptions}
          onChange={(value) => setBody({ ...body, periodKind: value })}
        />
        {body.periodKind === meta.customPeriodLabel && (
          <>
            <DateField
              label="시작일"
              value={body.customStart || ""}
              onChange={(value) => setBody({ ...body, customStart: value })}
            />
            <DateField
              label="종료일"
              value={body.customEnd || ""}
              onChange={(value) => setBody({ ...body, customEnd: value })}
            />
          </>
        )}
        <label className="check-field">
          <input
            type="checkbox"
            checked={body.currentPatchOnly}
            onChange={(event) =>
              setBody({ ...body, currentPatchOnly: event.target.checked })
            }
          />
          <span>이번 패치만</span>
        </label>
        <button className="primary-action" type="submit">
          <Database size={18} />
          DB조회 실행
        </button>
      </form>

      {error && <div className="error-box">{error}</div>}
      {payload && (
        <>
          <DbSummary payload={payload} />
          <div className="toolbar">
            <button className="tool-button" onClick={exportCsv}>
              <Download size={16} />
              CSV 다운로드
            </button>
            <div className="pager">
              <button
                disabled={body.page <= 1}
                onClick={() => movePage(body.page - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                {body.page} / {pageCount}
              </span>
              <button
                disabled={body.page >= pageCount}
                onClick={() => movePage(body.page + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
          <MatchResultList
            rows={payload.results}
            meta={meta}
            showPlayerCopy
            clientPagination={false}
          />
        </>
      )}
    </section>
  );
}

function WorkspaceHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header className="workspace-header">
      <span>{eyebrow}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

function ChampionSelect({
  label,
  value,
  meta,
  onChange,
}: {
  label: string;
  value: string;
  meta: MetaOptions;
  onChange: (value: string) => void;
}) {
  return (
    <SelectField
      label={label}
      value={value}
      options={meta.champions.map((champion) => champion.koreanName)}
      onChange={onChange}
    />
  );
}

function SelectField({
  label,
  value,
  options,
  format,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  format?: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {format ? format(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}

function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function JobProgress({
  job,
  error,
}: {
  job: JobStatus | null;
  error: string | null;
}) {
  if (error) {
    return <div className="error-box">{error}</div>;
  }
  if (!job || job.status === "succeeded") {
    return null;
  }
  return (
    <div className="progress-box">
      <div className="progress-head">
        <strong>{job.message || "작업 준비 중..."}</strong>
        <span>{Math.round(job.progress * 100)}%</span>
      </div>
      <div className="progress-track">
        <div style={{ width: `${Math.round(job.progress * 100)}%` }} />
      </div>
    </div>
  );
}

function SearchSummary({ payload }: { payload: SearchPayload }) {
  const wins = payload.results.filter((row) => row.win).length;
  const total = payload.results.length;
  const losses = total - wins;
  return (
    <SummaryGrid
      title={`${payload.account.game_name}#${payload.account.tag_line}`}
      meta={`${payload.lane_label} · ${payload.period_kind}`}
      items={[
        ["발견", `${total}`, "경기"],
        [
          "승률",
          total ? `${((wins / total) * 100).toFixed(1)}%` : "-",
          `${wins}승 ${losses}패`,
        ],
        ["스캔", `${payload.scanned_total}`, "매치"],
        [
          "캐시/API",
          `${payload.cache_hits} / ${payload.api_calls}`,
          payload.index_allowed
            ? `DB ${payload.indexed_rows} row`
            : `인덱스 제외 · ${payload.index_tier || "랭크 없음"}`,
        ],
      ]}
    />
  );
}

function MultiSummary({ payload }: { payload: MultiSearchPayload }) {
  return (
    <>
      <SummaryGrid
        title={`${payload.input_count}명 입력 · ${payload.period_label}`}
        meta={`1인당 최대 ${payload.matches_per_player}판`}
        items={[
          ["인덱스", `${payload.indexed_rows}`, "row"],
          [
            "성공/실패",
            `${payload.success_count} / ${payload.failure_count}`,
            "유저",
          ],
          [
            "매치/API",
            `${payload.discovered_matches} / ${payload.api_calls}`,
            `신규 상세 ${payload.new_match_details}`,
          ],
          ["캐시", `${payload.cache_hits}`, "hit"],
        ]}
      />
      {payload.failures.length > 0 && (
        <div className="failure-list">
          <h2>수집 실패 목록</h2>
          {payload.failures.map((failure) => (
            <p key={`${failure.riot_id_raw}-${failure.reason}`}>
              <strong>{failure.riot_id_raw}</strong>
              <span>{failure.reason}</span>
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function DbSummary({ payload }: { payload: DbSearchPayload }) {
  return (
    <SummaryGrid
      title={`${payload.my_champion_key} vs ${payload.enemy_champion_key || "전체"}`}
      meta={`${payload.lane_label} · ${payload.period_kind} · ${
        payload.patch_prefix || "전체 패치"
      }`}
      items={[
        ["발견", `${payload.stats.total}`, "경기"],
        [
          "승률",
          payload.stats.winRate === null ? "-" : `${payload.stats.winRate}%`,
          `${payload.stats.wins}승 ${payload.stats.losses}패`,
        ],
        ["페이지", `${payload.results.length}`, "표시 중"],
        ["API", "0", "DB-only"],
      ]}
    />
  );
}

function SummaryGrid({
  title,
  meta,
  items,
}: {
  title: string;
  meta: string;
  items: Array<[string, string, string]>;
}) {
  return (
    <section className="summary">
      <div className="summary-main">
        <span>결과 요약</span>
        <strong>{title}</strong>
        <p>{meta}</p>
      </div>
      <div className="kpis">
        {items.map(([label, value, caption]) => (
          <div className="kpi" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <em>{caption}</em>
          </div>
        ))}
      </div>
    </section>
  );
}

function MatchResultList({
  rows,
  meta,
  focusPuuid,
  showPlayerCopy,
  clientPagination = true,
}: {
  rows: MatchRow[];
  meta: MetaOptions;
  focusPuuid?: string;
  showPlayerCopy: boolean;
  clientPagination?: boolean;
}) {
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const pageCount = clientPagination
    ? Math.max(Math.ceil(rows.length / PAGE_SIZE), 1)
    : 1;
  const visibleRows = useMemo(
    () =>
      clientPagination
        ? rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
        : rows,
    [clientPagination, page, rows],
  );

  useEffect(() => {
    setPage(1);
  }, [rows]);

  if (rows.length === 0) {
    return (
      <div className="empty">
        <strong>조건에 맞는 경기를 찾지 못했습니다.</strong>
        <p>기간을 늘리거나 챔피언/라인 선택이 맞는지 확인해 주세요.</p>
      </div>
    );
  }

  const toggle = (id: string) => {
    setOpenIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <section className="result-list">
      <div className="list-head">
        <h2>결과 목록</h2>
        {clientPagination && (
          <div className="pager compact">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              <ChevronLeft size={16} />
            </button>
            <span>
              {page} / {pageCount}
            </span>
            <button
              disabled={page >= pageCount}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>
      {visibleRows.map((row, index) => {
        const id = `${row.match_id || index}-${row.player_puuid || focusPuuid || index}`;
        const playerPuuid = row.player_puuid || focusPuuid;
        return (
          <article className="result-card" key={id}>
            <MatchResultCard
              row={row}
              meta={meta}
              showPlayerCopy={showPlayerCopy}
              detailOpen={openIds.has(id)}
              onToggle={() => toggle(id)}
            />
            {openIds.has(id) && row.match_id && playerPuuid && (
              <MatchDetailPanel
                matchId={row.match_id}
                playerPuuid={playerPuuid}
              />
            )}
          </article>
        );
      })}
    </section>
  );
}

function MatchResultCard({
  row,
  meta,
  showPlayerCopy,
  detailOpen,
  onToggle,
}: {
  row: MatchRow;
  meta: MetaOptions;
  showPlayerCopy: boolean;
  detailOpen: boolean;
  onToggle: () => void;
}) {
  const enemy = splitRiotId(row.enemy_riot_id);
  const enemyName = row.enemy_game_name || enemy.name;
  const enemyTag = row.enemy_tag_line || enemy.tag;
  const player = splitRiotId(row.player_riot_id);
  const playerId = riotId(
    row.player_game_name || player.name,
    row.player_tag_line || player.tag,
  );
  const enemyId = riotId(enemyName, enemyTag, row.enemy_riot_id);
  const enemyLink = opggUrl(enemyName, enemyTag);
  const resultClass = row.win ? "win" : "loss";

  return (
    <div className={`card-grid ${resultClass}`}>
      <div className="match-meta">
        <strong>{row.win ? "승리" : "패배"}</strong>
        <span>{row.game_date || "-"}</span>
        <em>{secondsToMinutes(row.game_duration)}</em>
      </div>
      <div className="player-summary">
        {showPlayerCopy && <span className="player-id">{playerId}</span>}
        <div className="champ-line">
          <div className="champion">
            <img
              src={championIcon(meta.dataDragonVersion, row.my_champion_key)}
              alt=""
            />
            <span>Lv {row.my_champion_level || 0}</span>
          </div>
          <div>
            <strong>
              {row.kills} / <b>{row.deaths}</b> / {row.assists}
            </strong>
            <p>
              CS {row.cs} ·{" "}
              {Number(row.damage_to_champions || 0).toLocaleString()} 피해
            </p>
          </div>
        </div>
        <div className="items">
          {(row.my_items || [])
            .slice(0, 7)
            .map((itemId, index) =>
              itemId ? (
                <img
                  key={`${itemId}-${index}`}
                  src={itemIcon(meta.staticDataVersion, itemId)}
                  alt=""
                />
              ) : (
                <span key={`empty-${index}`} />
              ),
            )}
        </div>
      </div>
      <div className="enemy-summary">
        <img
          src={championIcon(meta.dataDragonVersion, row.enemy_champion_key)}
          alt=""
        />
        <div>
          <span>상대 라이너</span>
          <strong>{enemyName || "-"}</strong>
          <em>
            #{enemyTag || "-"} · {row.enemy_champion_key}
          </em>
        </div>
      </div>
      <div className="actions">
        {showPlayerCopy && (
          <CopyButton value={playerId} label="플레이어 Riot ID 복사" />
        )}
        <CopyButton value={enemyId} label="상대 Riot ID 복사" />
        <a
          className={`icon-button ${enemyLink ? "" : "disabled"}`}
          href={enemyLink || undefined}
          target="_blank"
          rel="noreferrer"
          title="OP.GG 열기"
        >
          <ExternalLink size={16} />
        </a>
        <button className="icon-button" onClick={onToggle} title="매치 상세">
          {detailOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 900);
  };
  return (
    <button className="icon-button" onClick={copy} title={label}>
      {copied ? "OK" : <Copy size={16} />}
    </button>
  );
}

function MatchDetailPanel({
  matchId,
  playerPuuid,
}: {
  matchId: string;
  playerPuuid: string;
}) {
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>("summary");

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setActiveTab("summary");
    api
      .getMatchDetail(matchId, playerPuuid)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
        }
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : "상세 조회 실패");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, playerPuuid]);

  if (error) {
    return <div className="detail-panel error-box">{error}</div>;
  }
  if (!detail) {
    return <div className="detail-panel">매치 상세를 불러오는 중...</div>;
  }
  return (
    <div className="detail-panel">
      <div className="detail-tabs" role="tablist" aria-label="매치 상세 탭">
        <button
          className={activeTab === "summary" ? "active" : ""}
          onClick={() => setActiveTab("summary")}
          type="button"
        >
          종합
        </button>
        <button
          className={activeTab === "build" ? "active" : ""}
          onClick={() => setActiveTab("build")}
          type="button"
        >
          빌드
        </button>
      </div>
      {activeTab === "summary" ? (
        <MatchSummaryTab detail={detail} />
      ) : (
        <BuildTab detail={detail} />
      )}
    </div>
  );
}

function MatchSummaryTab({ detail }: { detail: MatchDetail }) {
  const allyPlayers = sortTeam([detail.me, ...(detail.othersAlly || [])]);
  const enemyPlayers = sortTeam(
    [detail.enemyLaner, ...(detail.othersEnemy || [])].filter(
      Boolean,
    ) as DetailPlayer[],
  );
  const allPlayers = [...allyPlayers, ...enemyPlayers];
  const maxDamage = Math.max(
    ...allPlayers.map((player) => player.damage || 0),
    1,
  );
  const allyTotals = teamTotals(allyPlayers);
  const enemyTotals = teamTotals(enemyPlayers);

  return (
    <div className="match-summary-tab">
      <div className="team-tables">
        <TeamTable
          title={detail.me.win ? "승리 (블루팀)" : "패배 (블루팀)"}
          players={allyPlayers}
          maxDamage={maxDamage}
        />
        <TeamCompare ally={allyTotals} enemy={enemyTotals} />
        <TeamTable
          title={detail.enemyLaner?.win ? "승리 (레드팀)" : "패배 (레드팀)"}
          players={enemyPlayers}
          maxDamage={maxDamage}
        />
      </div>
    </div>
  );
}

function sortTeam(players: DetailPlayer[]): DetailPlayer[] {
  return players
    .map((player, index) => ({ player, index }))
    .sort((left, right) => {
      const leftOrder = LANE_ORDER[left.player.team_position || ""] ?? 99;
      const rightOrder = LANE_ORDER[right.player.team_position || ""] ?? 99;
      return leftOrder - rightOrder || left.index - right.index;
    })
    .map(({ player }) => player);
}

function teamTotals(players: DetailPlayer[]) {
  return {
    kills: players.reduce((total, player) => total + (player.kills || 0), 0),
    gold: players.reduce((total, player) => total + (player.gold || 0), 0),
  };
}

function participantName(player: DetailPlayer): string {
  const name = player.riot_id_game_name || player.summoner_name || "-";
  return player.riot_id_tag_line ? `${name}#${player.riot_id_tag_line}` : name;
}

function kdaRatio(player: DetailPlayer): string {
  if (!player.deaths) {
    return "Perfect";
  }
  return ((player.kills + player.assists) / player.deaths).toFixed(2);
}

function TeamTable({
  title,
  players,
  maxDamage,
}: {
  title: string;
  players: DetailPlayer[];
  maxDamage: number;
}) {
  const resultClass = players[0]?.win ? "win" : "loss";
  return (
    <section className={`team-table ${resultClass}`}>
      <div className="team-table-title">{title}</div>
      <div className="team-table-head">
        <span>챔피언</span>
        <span>플레이어</span>
        <span>KDA</span>
        <span>피해량</span>
        <span>CS</span>
        <span>골드</span>
        <span>아이템</span>
      </div>
      {players.map((player) => (
        <div
          className="team-table-row"
          key={player.puuid || participantName(player)}
        >
          <div className="team-champion">
            <div className="level-champion">
              {player.championIconUrl && (
                <img src={player.championIconUrl} alt="" />
              )}
              <span>{player.champion_level || 0}</span>
            </div>
            <div className="summoner-spells">
              {player.summoner1IconUrl && (
                <img src={player.summoner1IconUrl} alt="" />
              )}
              {player.summoner2IconUrl && (
                <img src={player.summoner2IconUrl} alt="" />
              )}
            </div>
          </div>
          <div className="team-player-name">
            <strong>{participantName(player)}</strong>
            <span>{player.rankLabel || player.championNameKo}</span>
          </div>
          <div className="team-kda">
            <strong>
              {player.kills}/{player.deaths}/{player.assists}
            </strong>
            <span>{kdaRatio(player)}:1</span>
          </div>
          <div className="team-damage">
            <span>{Number(player.damage || 0).toLocaleString()}</span>
            <div className="damage-bar">
              <div
                style={{
                  width: `${((player.damage || 0) / maxDamage) * 100}%`,
                }}
              />
            </div>
          </div>
          <div className="team-small-stat">{player.cs || 0}</div>
          <div className="team-small-stat">
            {Number(player.gold || 0).toLocaleString()}
          </div>
          <ItemStrip urls={player.itemIconUrls || []} />
        </div>
      ))}
    </section>
  );
}

function ItemStrip({ urls = [] }: { urls?: Array<string | null> }) {
  return (
    <div className="item-strip">
      {[...urls, null, null, null, null, null, null, null]
        .slice(0, 7)
        .map((url, index) =>
          url ? (
            <img key={`${url}-${index}`} src={url} alt="" />
          ) : (
            <span key={`empty-${index}`} />
          ),
        )}
    </div>
  );
}

function TeamCompare({
  ally,
  enemy,
}: {
  ally: { kills: number; gold: number };
  enemy: { kills: number; gold: number };
}) {
  return (
    <div className="team-compare">
      <CompareBar label="Total Kill" left={ally.kills} right={enemy.kills} />
      <CompareBar label="Total Gold" left={ally.gold} right={enemy.gold} />
    </div>
  );
}

function CompareBar({
  label,
  left,
  right,
}: {
  label: string;
  left: number;
  right: number;
}) {
  const total = Math.max(left + right, 1);
  return (
    <div className="compare-row">
      <span>{Number(left).toLocaleString()}</span>
      <div className="compare-track">
        <div
          className="compare-left"
          style={{ width: `${(left / total) * 100}%` }}
        />
        <strong>{label}</strong>
        <div
          className="compare-right"
          style={{ width: `${(right / total) * 100}%` }}
        />
      </div>
      <span>{Number(right).toLocaleString()}</span>
    </div>
  );
}

function BuildTab({ detail }: { detail: MatchDetail }) {
  const buildTimeline = detail.buildTimeline;
  const me = detail.me;
  const itemGroups = groupItemEvents(buildTimeline?.item_events || []);
  return (
    <div className="build-tab">
      <section className="build-section">
        <h3>아이템 빌드</h3>
        {itemGroups.length ? (
          <div className="build-sequence item-build-sequence">
            {itemGroups.map((group, index) => (
              <div
                className="item-build-group"
                key={`${group.minute}-${index}`}
              >
                {index > 0 && <span className="build-arrow">›</span>}
                <div className="item-build-stack">
                  <div className="item-build-icons">
                    {group.events.map((event, eventIndex) => (
                      <span
                        key={`${event.item_id}-${event.timestamp}-${eventIndex}`}
                      >
                        {event.icon_url ? (
                          <img src={event.icon_url} alt="" />
                        ) : (
                          event.item_id
                        )}
                      </span>
                    ))}
                  </div>
                  <em>{group.minute}분</em>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <TimelineNotice error={detail.timelineError} />
        )}
      </section>

      <section className="build-section">
        <h3>스킬 빌드</h3>
        {buildTimeline?.skill_events.length ? (
          <>
            <div className="skill-icons">
              {uniqueSkillEvents(detail).map((event) => (
                <div
                  className="skill-icon-card"
                  key={event.skillSlot || event.skill_slot || event.label}
                >
                  {event.iconUrl && (
                    <img src={event.iconUrl} alt={event.spellName} />
                  )}
                  <strong>{event.label}</strong>
                </div>
              ))}
            </div>
            <div className="skill-order">
              {buildTimeline.skill_events.map((event) => (
                <span
                  className={event.label === "R" ? "ultimate" : ""}
                  key={`${event.level}-${event.timestamp}`}
                >
                  {event.label}
                </span>
              ))}
            </div>
          </>
        ) : (
          <TimelineNotice error={detail.timelineError} />
        )}
      </section>

      <section className="build-section">
        <h3>룬</h3>
        <div className="rune-pages">
          <RunePageBlock
            page={me.primaryRunePage}
            fallbackIconUrls={me.primaryRuneIconUrls || []}
            fallbackTitle={me.primaryTreeName || "주 룬"}
          />
          <RunePageBlock
            page={me.secondaryRunePage}
            compact
            fallbackIconUrls={me.secondaryRuneIconUrls || []}
            fallbackTitle={me.secondaryTreeName || "보조 룬"}
          />
          <StatShardBlock
            rows={me.statShardPage || []}
            fallbackIconUrls={me.statShardIconUrls || []}
          />
        </div>
      </section>
    </div>
  );
}

function groupItemEvents(
  events: NonNullable<MatchDetail["buildTimeline"]>["item_events"],
) {
  const groups: Array<{
    minute: number;
    events: NonNullable<MatchDetail["buildTimeline"]>["item_events"];
  }> = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (last && last.minute === event.minute) {
      last.events.push(event);
    } else {
      groups.push({ minute: event.minute, events: [event] });
    }
  }
  return groups;
}

function TimelineNotice({ error }: { error: string | null }) {
  return (
    <p className="timeline-notice">
      {error || "빌드 타임라인을 불러오지 못했습니다."}
    </p>
  );
}

function uniqueSkillEvents(detail: MatchDetail) {
  const bySlot = new Map<
    number,
    NonNullable<MatchDetail["buildTimeline"]>["skill_events"][number]
  >();
  for (const event of detail.buildTimeline?.skill_events || []) {
    const slot = event.skillSlot || event.skill_slot || 0;
    if (slot && !bySlot.has(slot)) {
      bySlot.set(slot, event);
    }
  }
  return [...bySlot.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, event]) => event);
}

function RunePageBlock({
  page,
  compact = false,
  fallbackIconUrls = [],
  fallbackTitle,
}: {
  page?: RunePage | null;
  compact?: boolean;
  fallbackIconUrls?: Array<string | null>;
  fallbackTitle: string;
}) {
  if (!page || page.slots.length === 0) {
    return (
      <div className={`rune-page ${compact ? "compact" : ""}`}>
        <div className="rune-page-title">
          <span>{fallbackTitle}</span>
        </div>
        <div className="rune-slot selected-only">
          {fallbackIconUrls.filter(Boolean).map((url, index) => (
            <span className="rune-icon selected" key={`${url}-${index}`}>
              <img src={url || ""} alt="" />
            </span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className={`rune-page ${compact ? "compact" : ""}`}>
      <div className="rune-page-title">
        {page.treeIconUrl && <img src={page.treeIconUrl} alt="" />}
        <span>{page.treeName}</span>
      </div>
      {page.slots.map((slot, index) => (
        <div className="rune-slot" key={`${page.treeId}-${index}`}>
          {slot.map((rune) => (
            <RuneIcon rune={rune} key={rune.id} />
          ))}
        </div>
      ))}
    </div>
  );
}

function StatShardBlock({
  rows,
  fallbackIconUrls = [],
}: {
  rows: RuneEntry[][];
  fallbackIconUrls?: Array<string | null>;
}) {
  return (
    <div className="rune-page stat-shards">
      <div className="rune-page-title">
        <span>능력치 파편</span>
      </div>
      {rows.length
        ? rows.map((row, index) => (
            <div className="rune-slot" key={`stat-${index}`}>
              {row.map((rune) => (
                <RuneIcon rune={rune} key={`${index}-${rune.id}`} />
              ))}
            </div>
          ))
        : fallbackIconUrls.filter(Boolean).map((url, index) => (
            <div
              className="rune-slot selected-only"
              key={`stat-fallback-${index}`}
            >
              <span className="rune-icon selected">
                <img src={url || ""} alt="" />
              </span>
            </div>
          ))}
    </div>
  );
}

function RuneIcon({ rune }: { rune: RuneEntry }) {
  return (
    <span
      className={`rune-icon ${rune.selected ? "selected" : ""}`}
      title={rune.name}
    >
      {rune.iconUrl && <img src={rune.iconUrl} alt="" />}
    </span>
  );
}
