"""하이록스 실시간 트래커 (Streamlit)

실행: uv run streamlit run hyrox/app.py
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from scraper import (
    DIVISIONS_KO,
    STATIONS,
    STATIONS_KO,
    Race,
    extract_id,
    fetch_race,
    fmt,
    list_divisions,
    search_starters,
)

DEFAULT_URL = "https://www.hyresult.com/result/LR3MS4JI5BA8ED"
DEFAULT_EVENT = "s9-2026-seoul"
REFRESH_SEC = 30
TZ = ZoneInfo("Asia/Seoul")  # 완주 기록의 통과 시각은 현지 시각이라 서울 대회 기준으로 해석
STATION_ANIMS = ["ski", "push", "pull", "bbj", "row", "carry", "lunge", "wb"]
TEMPLATE = (Path(__file__).parent / "figure.html").read_text(encoding="utf-8")

st.set_page_config(page_title="HYROX 트래커", layout="wide")


@st.cache_data(ttl=REFRESH_SEC - 5, show_spinner=False)
def load(result_id: str) -> Race:
    return fetch_race(result_id)


@st.cache_data(ttl=3600, show_spinner=False)
def load_divisions(event: str) -> list[str]:
    return list_divisions(event)


@st.cache_data(ttl=300, show_spinner=False)
def load_search(event: str, division: str, name: str) -> list[dict]:
    return search_starters(event, division, name)


def wave_start_kst(race: Race) -> datetime | None:
    ws = race.wave_start
    if ws is None:
        return None
    return ws.astimezone(TZ) if ws.tzinfo else ws.replace(tzinfo=TZ)


def figure_data(race: Race) -> dict:
    stage = race.current_stage
    if race.finished:
        anim, kind, title, sub = "cheer", "finish", "완주!", f"{race.event} · {race.division}"
    elif race.last_index < 0:
        anim, kind, title, sub = "idle", "", "출발 대기", "첫 체크포인트 기록 전"
    elif stage % 2 == 0:
        n = stage // 2
        anim, kind, title, sub = "run", "run", f"Run {n + 1}", f"러닝 {n + 1} / 8"
    else:
        n = stage // 2
        anim, kind = STATION_ANIMS[n], "station"
        title, sub = STATIONS[n], f"스테이션 {n + 1} / 8 · {STATIONS_KO[n]}"

    ws = wave_start_kst(race)
    return {
        "stage": stage,
        "anim": anim,
        "kind": kind,
        "stageTitle": title,
        "stageSub": sub,
        "finished": race.finished,
        "running": race.running and not race.finished,
        "total": race.total,
        "rank": race.rank,
        "fieldSize": race.field_size,
        "waveStartEpoch": ws.timestamp() if ws else None,
        "waveStartText": ws.strftime("%H:%M:%S") if ws else None,
        "lastCumulative": race.checkpoints[race.last_index] if race.last_index >= 0 else 0,
        "segments": [
            {"kind": s.kind, "no": s.no, "duration": s.duration} for s in race.segments
        ],
    }


def segment_table(race: Race, kind: str) -> pd.DataFrame:
    rows = []
    for s in race.segments:
        if s.kind != kind:
            continue
        name = s.name if kind == "run" else f"{s.name} ({STATIONS_KO[s.no - 1]})"
        rows.append({"구간": name, "소요": fmt(s.duration), "누적": fmt(s.cumulative)})
    return pd.DataFrame(rows)


def finish_summary(race: Race) -> None:
    runs = [s.duration for s in race.segments if s.kind == "run"]
    stations = [s.duration for s in race.segments if s.kind == "station"]
    station_sum = sum(stations) if None not in stations else None
    run_sum = race.run_total or (sum(runs) if None not in runs else None)

    st.markdown("### 기록 요약")
    cols = st.columns(5)
    cols[0].metric("최종 기록", fmt(race.total))
    cols[1].metric("순위", f"{race.rank} / {race.field_size}" if race.rank else "-")
    cols[2].metric("러닝 합계", fmt(run_sum))
    cols[3].metric("스테이션 합계", fmt(station_sum))
    cols[4].metric("록스존", fmt(race.roxzone_total))

    # 가장 빠른/느린 런, 가장 오래 걸린 스테이션
    run_segs = [s for s in race.segments if s.kind == "run" and s.duration]
    st_segs = [s for s in race.segments if s.kind == "station" and s.duration]
    if run_segs and st_segs:
        fast = min(run_segs, key=lambda s: s.duration)
        slow = max(run_segs, key=lambda s: s.duration)
        hard = max(st_segs, key=lambda s: s.duration)
        st.caption(
            f"가장 빠른 런 {fast.name} {fmt(fast.duration)} · 가장 느린 런 {slow.name} {fmt(slow.duration)} "
            f"· 가장 오래 걸린 스테이션 {hard.name} {fmt(hard.duration)}"
        )


# ---------- 선수 찾기 ----------
with st.sidebar:
    st.header("선수 찾기")
    mode = st.radio("방법", ["이름 검색", "URL 입력"], horizontal=True)
    target = None
    if mode == "이름 검색":
        event = st.text_input("대회 코드", DEFAULT_EVENT, help="HYRESULT 대회 주소의 /event/ 뒤 부분")
        try:
            divisions = load_divisions(event)
        except Exception as e:
            divisions = []
            st.error(f"대회 정보를 불러오지 못했습니다: {e}")
        if divisions:
            division = st.selectbox("디비전", divisions, format_func=lambda d: DIVISIONS_KO.get(d, d))
            name = st.text_input("이름", placeholder="일부만 입력해도 됩니다 (예: 정우)")
            if name.strip():
                results = load_search(event, division, name.strip())
                if results:
                    pick = st.selectbox(
                        f"검색 결과 {len(results)}팀",
                        results,
                        format_func=lambda r: f"{r['names']} · {r['age_group']} · {r['wave']}",
                    )
                    target = pick["id"]
                else:
                    st.info("검색 결과가 없습니다")
    else:
        target = extract_id(st.text_input("HYRESULT 결과 URL", DEFAULT_URL))

st.title("HYROX 실시간 트래커")


@st.fragment(run_every=REFRESH_SEC)
def tracker(result_id: str) -> None:
    try:
        race = load(result_id)
    except Exception as e:
        st.error(f"기록을 불러오지 못했습니다: {e}")
        return

    ws = wave_start_kst(race)
    st.subheader(race.athlete)
    meta = [race.event, race.division]
    if ws:
        meta.append(f"웨이브 시작 {ws:%H:%M:%S}")
    st.caption(" · ".join(m for m in meta if m))

    html = TEMPLATE.replace("__DATA__", json.dumps(figure_data(race), ensure_ascii=False))
    st.iframe(html, height=380)

    if race.finished:
        finish_summary(race)

    left, right = st.columns(2)
    left.markdown("**런 (록스존 포함)**")
    left.dataframe(segment_table(race, "run"), hide_index=True, width="stretch")
    right.markdown("**스테이션**")
    right.dataframe(segment_table(race, "station"), hide_index=True, width="stretch")

    st.caption(
        f"데이터 갱신 {datetime.now(TZ):%H:%M:%S} · {REFRESH_SEC}초마다 자동 갱신 · 출처 HYRESULT"
    )


if target:
    tracker(target)
else:
    st.info("왼쪽에서 대회·디비전·이름으로 선수를 검색해 주세요.")
