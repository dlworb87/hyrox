"""하이록스 실시간 트래커 (Streamlit)

실행: uv run streamlit run hyrox/app.py
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from scraper import STATIONS, STATIONS_KO, Race, extract_id, fetch_race, fmt

REFRESH_SEC = 30
TZ = ZoneInfo("Asia/Seoul")  # 완주 기록의 통과 시각은 현지 시각이라 서울 대회 기준으로 해석
STATION_ANIMS = ["ski", "push", "pull", "bbj", "row", "carry", "lunge", "wb"]
TEMPLATE = (Path(__file__).parent / "figure.html").read_text(encoding="utf-8")

st.set_page_config(page_title="HYROX 트래커", layout="wide")


@st.cache_data(ttl=REFRESH_SEC - 5, show_spinner=False)
def load(result_id: str) -> Race:
    return fetch_race(result_id)


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
    summary, note = finish_summary(race) if race.finished else (None, "")
    return {
        "summary": summary,
        "summaryNote": note,
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


def segment_table(race: Race) -> pd.DataFrame:
    """런과 스테이션을 진행 순서대로 한 표에 담는다. 진행 중인 구간에는 ▶ 표시."""
    rows = []
    for i, s in enumerate(race.segments):
        icon = "🏃" if s.kind == "run" else "🏋️"
        mark = "▶ " if i == race.current_stage else ""
        rows.append({"구간": f"{mark}{icon} {s.name}", "소요": fmt(s.duration), "누적": fmt(s.cumulative)})
    return pd.DataFrame(rows).set_index("구간")


def finish_summary(race: Race) -> tuple[list[list[str]], str]:
    """완주 카드에 넣을 요약 수치와 한 줄 메모."""
    runs = [s.duration for s in race.segments if s.kind == "run"]
    stations = [s.duration for s in race.segments if s.kind == "station"]
    station_sum = sum(stations) if None not in stations else None
    run_sum = race.run_total or (sum(runs) if None not in runs else None)
    summary = [
        ["러닝 합계", fmt(run_sum)],
        ["스테이션 합계", fmt(station_sum)],
        ["록스존", fmt(race.roxzone_total)],
    ]

    note = ""
    run_segs = [s for s in race.segments if s.kind == "run" and s.duration]
    st_segs = [s for s in race.segments if s.kind == "station" and s.duration]
    if run_segs and st_segs:
        fast = min(run_segs, key=lambda s: s.duration)
        slow = max(run_segs, key=lambda s: s.duration)
        hard = max(st_segs, key=lambda s: s.duration)
        note = (
            f"가장 빠른 런 {fast.name} {fmt(fast.duration)} · 가장 느린 런 {slow.name} {fmt(slow.duration)} "
            f"· 가장 오래 걸린 스테이션 {hard.name} {fmt(hard.duration)}"
        )
    return summary, note


st.header("HYROX 실시간 트래커")
# ?id=결과ID 로 열면 그 기록을 바로 보여준다
url = st.text_input(
    "HYRESULT 결과 URL",
    value=st.query_params.get("id", ""),
    placeholder="https://www.hyresult.com/result/...",
)
target = extract_id(url) if url.strip() else None
if target:
    st.query_params["id"] = target
else:
    st.query_params.clear()


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

    data = json.dumps(figure_data(race), ensure_ascii=False).replace("</", "<\\/")
    st.iframe(TEMPLATE.replace("__DATA__", data), height="content")

    st.markdown("**구간 기록** · 런은 록스존 포함")
    st.table(segment_table(race))

    st.caption(
        f"데이터 갱신 {datetime.now(TZ):%H:%M:%S} · {REFRESH_SEC}초마다 자동 갱신 · 출처 HYRESULT"
    )


if target:
    tracker(target)
else:
    st.info("HYRESULT에서 선수 결과 페이지를 열고, 주소를 위 입력란에 붙여넣어 주세요.")
