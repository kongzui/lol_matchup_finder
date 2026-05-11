"""매치 상세 패널 HTML 렌더링."""

from __future__ import annotations

from html import escape
from typing import Any

from src.champions import ChampionData, champion_icon_url
from src.static_data import (
    StaticData,
    item_icon_url,
    stat_shard_icon_url,
    stat_shard_name,
)


STAT_SHARD_ROWS: tuple[tuple[int, ...], ...] = (
    (5008, 5005, 5007),  # 공격
    (5008, 5010, 5001),  # 유연
    (5011, 5013, 5001),  # 방어
)


def _h(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _kda_ratio(kills: int, deaths: int, assists: int) -> str:
    """KDA 비율을 문자열로 만든다."""
    if deaths == 0:
        return "Perfect" if (kills + assists) > 0 else "0.00"
    return f"{(kills + assists) / deaths:.2f}"


def _format_number(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _items_html(version: str, items: list[int]) -> str:
    """아이템 6칸 + 갭 + 트링킷 1칸을 HTML로 만든다."""
    main_items = items[:6]
    trinket = items[6] if len(items) > 6 else 0
    slots: list[str] = []

    for item_id in main_items:
        url = item_icon_url(version, item_id)
        if url:
            slots.append(
                f'<div class="detail-item"><img src="{_h(url)}" alt="" /></div>'
            )
        else:
            slots.append('<div class="detail-item"></div>')

    slots.append('<div class="detail-gap"></div>')
    trinket_url = item_icon_url(version, trinket)
    if trinket_url:
        slots.append(
            f'<div class="detail-item"><img src="{_h(trinket_url)}" alt="" /></div>'
        )
    else:
        slots.append('<div class="detail-item"></div>')

    return "".join(slots)


def _runes_html(player: dict[str, Any], static_data: StaticData) -> str:
    """주룬·보조룬·스탯샤드를 한 줄로 표시한다."""
    primary_tree_id = player.get("primary_tree_id")
    secondary_tree_id = player.get("secondary_tree_id")
    primary_runes = player.get("primary_runes") or []
    secondary_runes = player.get("secondary_runes") or []
    shards = [
        player.get("stat_offense"),
        player.get("stat_flex"),
        player.get("stat_defense"),
    ]

    def _img(url: str | None, alt: str, cls: str = "") -> str:
        if not url:
            return f'<div class="detail-rune-empty" title="{_h(alt)}"></div>'
        cls_attr = f' class="{_h(cls)}"' if cls else ""
        return f'<img{cls_attr} src="{_h(url)}" alt="{_h(alt)}" title="{_h(alt)}" />'

    primary_imgs = [
        _img(
            static_data.rune_icon_url(rune_id),
            static_data.rune_name(rune_id),
            "keystone" if idx == 0 else "",
        )
        for idx, rune_id in enumerate(primary_runes)
    ]
    secondary_imgs = [
        _img(static_data.rune_icon_url(rune_id), static_data.rune_name(rune_id))
        for rune_id in secondary_runes
    ]
    shard_imgs = [
        _img(stat_shard_icon_url(shard_id), stat_shard_name(shard_id))
        for shard_id in shards
    ]

    primary_tree_icon = static_data.tree_icon_url(primary_tree_id)
    secondary_tree_icon = static_data.tree_icon_url(secondary_tree_id)

    return f"""
<div class="detail-runes">
  <div class="rune-group">
    {_img(primary_tree_icon, static_data.tree_name(primary_tree_id), "tree-icon")}
    <div class="runes">{"".join(primary_imgs)}</div>
  </div>
  <div class="rune-group">
    {_img(secondary_tree_icon, static_data.tree_name(secondary_tree_id), "tree-icon")}
    <div class="runes">{"".join(secondary_imgs)}</div>
  </div>
  <div class="rune-group shards">
    <div class="runes">{"".join(shard_imgs)}</div>
  </div>
</div>
"""


def _item_build_html(version: str, item_events: list[dict[str, Any]]) -> str:
    """아이템 구매 순서를 시간축으로 표시한다."""
    if not item_events:
        return '<div class="build-empty">타임라인에서 아이템 구매 기록을 찾지 못했습니다.</div>'

    item_groups = _group_item_events(item_events, max_gap_ms=10_000)
    cells: list[str] = []
    for idx, group in enumerate(item_groups[:18]):
        minute = int(group["timestamp"] // 60000)
        arrow = '<span class="build-arrow">›</span>' if idx > 0 else ""
        icons = []
        for event in group["events"]:
            item_id = event.get("item_id")
            url = item_icon_url(version, item_id)
            icons.append(
                f'<img src="{_h(url)}" alt="{item_id}" title="{item_id}" />'
                if url
                else '<div class="build-icon-empty"></div>'
            )
        cells.append(
            f"""
<div class="build-step">
  {arrow}
  <div class="build-group-icons">{"".join(icons)}</div>
  <span>{minute}분</span>
</div>
"""
        )

    return f'<div class="item-build">{"".join(cells)}</div>'


def _group_item_events(
    item_events: list[dict[str, Any]],
    max_gap_ms: int,
) -> list[dict[str, Any]]:
    """가까운 시간에 산 아이템을 한 구매 묶음으로 합친다."""
    groups: list[dict[str, Any]] = []
    for event in item_events:
        timestamp = int(event.get("timestamp") or 0)
        if not groups or timestamp - int(groups[-1]["last_timestamp"]) > max_gap_ms:
            groups.append(
                {
                    "timestamp": timestamp,
                    "last_timestamp": timestamp,
                    "events": [event],
                }
            )
            continue

        groups[-1]["events"].append(event)
        groups[-1]["last_timestamp"] = timestamp

    return groups


def _skill_build_html(
    champion_key: str,
    champion_data: ChampionData,
    skill_events: list[dict[str, Any]],
) -> str:
    """스킬 레벨업 순서를 표시한다."""
    if not skill_events:
        return '<div class="build-empty">타임라인에서 스킬 레벨업 기록을 찾지 못했습니다.</div>'

    first_order: list[int] = []
    for event in skill_events:
        skill_slot = int(event.get("skill_slot") or 0)
        if skill_slot and skill_slot not in first_order:
            first_order.append(skill_slot)

    icon_cells = []
    for idx, skill_slot in enumerate(first_order[:4]):
        spell = champion_data.spell_info(champion_key, skill_slot) or {}
        url = champion_data.spell_icon_url(champion_key, skill_slot)
        label = {1: "Q", 2: "W", 3: "E", 4: "R"}.get(skill_slot, "")
        arrow = '<span class="build-arrow">›</span>' if idx > 0 else ""
        icon = (
            f'<img src="{_h(url)}" alt="{_h(spell.get("name"))}" title="{_h(spell.get("name"))}" />'
            if url
            else '<div class="build-icon-empty"></div>'
        )
        icon_cells.append(
            f"""
<div class="skill-priority-step">
  {arrow}
  <div class="build-icon">{icon}<b>{_h(label)}</b></div>
</div>
"""
        )

    level_cells = []
    for event in skill_events[:18]:
        label = event.get("label") or ""
        is_r = " ult" if label == "R" else ""
        level_cells.append(f'<span class="skill-level{is_r}">{_h(label)}</span>')

    return f"""
<div class="skill-build">
  <div class="skill-priority">{"".join(icon_cells)}</div>
  <div class="skill-levels">{"".join(level_cells)}</div>
</div>
"""


def _detailed_runes_html(player: dict[str, Any], static_data: StaticData) -> str:
    """룬을 슬롯 단위 선택 상태로 표시한다."""
    primary_tree_id = player.get("primary_tree_id")
    secondary_tree_id = player.get("secondary_tree_id")
    selected = set(player.get("primary_runes") or []) | set(
        player.get("secondary_runes") or []
    )

    def _tree_html(tree_id: int | None) -> str:
        tree_icon = static_data.tree_icon_url(tree_id)
        tree_name = static_data.tree_name(tree_id)
        rows = []
        for slot in static_data.tree_slots(tree_id):
            runes = []
            for rune_id in slot:
                url = static_data.rune_icon_url(rune_id)
                name = static_data.rune_name(rune_id)
                cls = "selected" if rune_id in selected else "muted"
                if url:
                    runes.append(
                        f'<img class="{cls}" src="{_h(url)}" alt="{_h(name)}" title="{_h(name)}" />'
                    )
            if runes:
                rows.append(f'<div class="rune-slot-row">{"".join(runes)}</div>')

        tree_head = (
            f'<img src="{_h(tree_icon)}" alt="{_h(tree_name)}" /><strong>{_h(tree_name)}</strong>'
            if tree_icon
            else f"<strong>{_h(tree_name)}</strong>"
        )
        return f"""
<div class="rune-tree">
  <div class="rune-tree-head">{tree_head}</div>
  {"".join(rows)}
</div>
"""

    selected_shards = [
        player.get("stat_offense"),
        player.get("stat_flex"),
        player.get("stat_defense"),
    ]
    shard_rows = []
    for row_idx, options in enumerate(STAT_SHARD_ROWS):
        selected_shard = (
            selected_shards[row_idx] if row_idx < len(selected_shards) else None
        )
        shard_imgs = []
        for shard_id in options:
            url = stat_shard_icon_url(shard_id)
            name = stat_shard_name(shard_id)
            if not url:
                continue
            cls = "selected" if shard_id == selected_shard else "muted"
            shard_imgs.append(
                f'<img class="{cls}" src="{_h(url)}" alt="{_h(name)}" title="{_h(name)}" />'
            )
        shard_rows.append(f'<div class="rune-slot-row">{"".join(shard_imgs)}</div>')

    return f"""
<div class="rune-detail-grid">
  {_tree_html(primary_tree_id)}
  {_tree_html(secondary_tree_id)}
  <div class="rune-tree shards">
    <div class="rune-tree-head"><strong>능력치 파편</strong></div>
    {"".join(shard_rows)}
  </div>
</div>
"""


def _render_focus_build(
    me: dict[str, Any],
    champion_data: ChampionData,
    static_data: StaticData,
    build_timeline: dict[str, Any] | None,
) -> str:
    """검색 유저 전용 상세 빌드 정보를 표시한다."""
    version = static_data.version or champion_data.version
    champion_key = me.get("champion_key", "")
    item_events = build_timeline.get("item_events", []) if build_timeline else []
    skill_events = build_timeline.get("skill_events", []) if build_timeline else []

    return f"""
<div class="focus-build-panel">
  <div class="detail-title">검색 유저 상세 빌드</div>
  <div class="build-section">
    <div class="build-section-title">아이템 빌드</div>
    {_item_build_html(version, item_events)}
  </div>
  <div class="build-section">
    <div class="build-section-title">스킬 빌드</div>
    {_skill_build_html(champion_key, champion_data, skill_events)}
  </div>
  <div class="build-section">
    <div class="build-section-title">룬</div>
    {_detailed_runes_html(me, static_data)}
  </div>
</div>
"""


def _render_player_block(
    player: dict[str, Any],
    champion_data: ChampionData,
    static_data: StaticData,
    label: str,
) -> str:
    """라이너 한 명의 상세 블록 HTML."""
    version = static_data.version or champion_data.version
    champion_key = player.get("champion_key", "")
    ko_name = champion_data.to_korean_name(champion_key)
    champ_url = champion_icon_url(version, champion_key)
    level = player.get("champion_level") or 0

    spell1 = static_data.summoner_icon_url(player.get("summoner1_id"))
    spell2 = static_data.summoner_icon_url(player.get("summoner2_id"))
    spell1_name = static_data.summoner_name(player.get("summoner1_id"))
    spell2_name = static_data.summoner_name(player.get("summoner2_id"))

    kills = int(player.get("kills", 0))
    deaths = int(player.get("deaths", 0))
    assists = int(player.get("assists", 0))
    cs = int(player.get("cs", 0))
    gold = int(player.get("gold", 0))
    damage = int(player.get("damage", 0))

    name = player.get("riot_id_game_name") or player.get("summoner_name") or "-"
    tag = player.get("riot_id_tag_line") or ""
    result_cls = "win" if player.get("win") else "loss"
    result_text = "승리" if player.get("win") else "패배"

    def _spell(url: str | None, name_text: str) -> str:
        if not url:
            return '<div class="detail-item small"></div>'
        return f'<img src="{_h(url)}" alt="{_h(name_text)}" title="{_h(name_text)}" />'

    return f"""
<div class="detail-player {result_cls}">
  <div class="detail-player-head">
    <div class="detail-label">{_h(label)}</div>
    <span class="detail-result {result_cls}">{result_text}</span>
  </div>
  <div class="detail-main">
    <div class="detail-champ">
      <img src="{_h(champ_url)}" alt="{_h(ko_name)}" />
      <span>Lv {level}</span>
    </div>
    <div class="detail-spells">
      {_spell(spell1, spell1_name)}
      {_spell(spell2, spell2_name)}
    </div>
    <div class="detail-items">{_items_html(version, player.get("items") or [])}</div>
    <div class="detail-stats">
      <div class="name">{_h(name)}<span>#{_h(tag)}</span></div>
      <div class="kda">{kills}/{deaths}/{assists}<span>{_kda_ratio(kills, deaths, assists)} KDA</span></div>
      <div class="extras">{_h(ko_name)} · CS {cs} · {_format_number(gold)} gold · {_format_number(damage)} dmg</div>
    </div>
  </div>
  {_runes_html(player, static_data)}
</div>
"""


def _render_team_summary(
    others_ally: list[dict[str, Any]],
    others_enemy: list[dict[str, Any]],
    champion_data: ChampionData,
    version: str,
) -> str:
    """나머지 8명 요약 블록 HTML."""

    def _row(player: dict[str, Any]) -> str:
        champion_key = player.get("champion_key", "")
        ko_name = champion_data.to_korean_name(champion_key)
        champ_url = champion_icon_url(version, champion_key)
        nick = player.get("riot_id_game_name") or player.get("summoner_name") or "-"
        tag = player.get("riot_id_tag_line") or ""
        kills = player.get("kills", 0)
        deaths = player.get("deaths", 0)
        assists = player.get("assists", 0)
        return f"""
<div class="team-row">
  <img src="{_h(champ_url)}" alt="{_h(ko_name)}" title="{_h(ko_name)}" />
  <div class="who">
    <span class="nick">{_h(nick)}</span><span class="tag">#{_h(tag)}</span>
  </div>
  <div class="team-kda">{kills}/{deaths}/{assists}</div>
</div>
"""

    return f"""
<div class="detail-others">
  <div class="team-col ally">
    <div class="team-label">아군</div>
    {"".join(_row(player) for player in others_ally)}
  </div>
  <div class="team-col enemy">
    <div class="team-label">적군</div>
    {"".join(_row(player) for player in others_enemy)}
  </div>
</div>
"""


def render_match_detail(
    focus: dict[str, Any],
    champion_data: ChampionData,
    static_data: StaticData,
    build_timeline: dict[str, Any] | None = None,
) -> str:
    """매치 상세 패널 전체 HTML을 만든다."""
    focus_build_html = (
        _render_focus_build(
            focus["me"],
            champion_data,
            static_data,
            build_timeline,
        )
        if build_timeline is not None
        else ""
    )
    enemy = focus.get("enemy_laner")
    enemy_html = (
        _render_player_block(enemy, champion_data, static_data, "맞라이너")
        if enemy
        else '<div class="detail-player loss">상대 라이너 정보를 찾지 못했습니다.</div>'
    )
    others_html = _render_team_summary(
        focus.get("others_ally") or [],
        focus.get("others_enemy") or [],
        champion_data,
        static_data.version,
    )
    return f"""
<div class="detail-panel">
  <div class="detail-title">맞라인 상세</div>
  {focus_build_html}
  {enemy_html}
  <div class="detail-title">나머지 플레이어</div>
  {others_html}
</div>
"""
