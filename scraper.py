"""HYRESULT에서 하이록스 기록을 가져온다.

페이지는 Next.js RSC 페이로드에 데이터를 담고 있으며, 경기 상태에 따라 형식이 다르다.

완주 기록 페이지
- 체크포인트 누적시간: p_r1..p_r8 (런 N 종료), p_w1..p_w8 (스테이션 N 종료), 초 단위
- 결과 요약: t_w1..t_w8 (스테이션 순수 시간), t_total, t_ra(러닝 합계), t_rx(록스존 합계)
- 구간 기록 표: 체크포인트 통과 시각(Time of Day)과 누적시간 → 웨이브 시작 시각 계산

실시간(Live) 페이지
- 구간 표: t_r1..t_w8 행마다 구간 시간(Time)과 누적시간(Total)
- startedAt(UTC), isRunning
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

SITE = "https://www.hyresult.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}

STATIONS = [
    "SkiErg",
    "Sled Push",
    "Sled Pull",
    "Burpee Broad Jump",
    "Row",
    "Farmers Carry",
    "Sandbag Lunges",
    "Wall Balls",
]
STATIONS_KO = [
    "스키에르그",
    "슬레드 푸시",
    "슬레드 풀",
    "버피 브로드 점프",
    "로잉",
    "파머스 캐리",
    "샌드백 런지",
    "월볼",
]

@dataclass
class Segment:
    kind: str  # run | station
    no: int  # 1~8
    name: str
    duration: int | None  # 초
    cumulative: int | None  # 구간 종료 시점의 누적시간(초)


@dataclass
class Race:
    result_id: str
    athlete: str
    event: str
    division: str
    checkpoints: list[int | None]  # [r1, w1, r2, w2, ..., r8, w8] 누적시간
    station_times: list[int | None]  # 스테이션 순수 시간
    wave_start: datetime | None  # 시간대 포함
    live: bool = False  # 실시간 페이지 여부
    running: bool = False  # 실시간 페이지의 진행 중 표시
    rank: int | None = None
    field_size: int | None = None
    run_total: int | None = None
    roxzone_total: int | None = None
    segments: list[Segment] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.checkpoints[-1] is not None

    @property
    def total(self) -> int | None:
        return self.checkpoints[-1]

    @property
    def last_index(self) -> int:
        """마지막으로 통과한 체크포인트 인덱스 (없으면 -1)."""
        reached = [i for i, c in enumerate(self.checkpoints) if c is not None]
        return reached[-1] if reached else -1

    @property
    def current_stage(self) -> int:
        """현재 수행 중인 단계 인덱스 (0=런1, 1=스테이션1, ..., 15=스테이션8, 16=완주)."""
        return 16 if self.finished else self.last_index + 1


def extract_id(url_or_id: str) -> str:
    m = re.search(r"/result/([A-Za-z0-9]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def _get(path: str) -> str:
    resp = requests.get(SITE + path, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _rsc_payload(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(json.loads(f'"{c}"') for c in chunks)


def _decode_after(payload: str, marker: str):
    i = payload.find(marker)
    if i < 0:
        return None
    # marker 안의 첫 '{'부터 JSON 객체 하나를 읽는다
    obj, _ = json.JSONDecoder().raw_decode(payload, i + marker.find("{"))
    return obj


def _parse_clock(s: str | None) -> int | None:
    """'1:02:03' 또는 '2:04' 형식을 초로 바꾼다. 형식이 아니면 None."""
    if not s or not re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", s):
        return None
    sec = 0
    for part in s.split(":"):
        sec = sec * 60 + int(part)
    return sec


def _build_segments(cps: list[int | None], st: list[int | None]) -> list[Segment]:
    """런 8개, 스테이션 8개 구간 시간을 계산한다.

    스테이션은 순수 스테이션 시간을 쓰고,
    런은 이전 스테이션 종료부터 다음 스테이션 시작까지(록스존 포함)로 계산한다.
    """
    segs = []
    prev_end = 0
    for n in range(8):
        r_cp, w_cp, t_w = cps[2 * n], cps[2 * n + 1], st[n]
        station_start = w_cp - t_w if (w_cp is not None and t_w is not None) else r_cp
        run = station_start - prev_end if (station_start is not None and prev_end is not None) else None
        segs.append(Segment("run", n + 1, f"Run {n + 1}", run, station_start))
        if t_w is None and w_cp is not None and r_cp is not None:
            t_w = w_cp - r_cp
        segs.append(Segment("station", n + 1, STATIONS[n], t_w, w_cp))
        prev_end = w_cp
    return segs


def _meta(html: str) -> tuple[str, str, str]:
    """페이지 제목·설명에서 (선수, 대회, 디비전)을 읽는다."""
    title = re.search(r"<title>(?:Live HYROX splits|HYROX race analysis) for (.+?) at (.+?) • ", html)
    desc = re.search(r'<meta name="description" content="[^"]* in division ([^"]+)"', html)
    athlete, event = (title.group(1), title.group(2)) if title else ("", "")
    return athlete, event, desc.group(1) if desc else ""


def _parse_finished(rid: str, html: str, payload: str) -> Race:
    cp_data = _decode_after(payload, '{"data":[{"checkpoint":"p_r1"')
    by_cp = {c["checkpoint"]: c for c in cp_data["data"]}
    order = [f"p_{k}{n}" for n in range(1, 9) for k in ("r", "w")]
    cps = [by_cp.get(k, {}).get("totalTime") for k in order]

    result = _decode_after(payload, '"result":{"idp"') or {}
    st = [result.get(f"t_w{n}") for n in range(1, 9)]
    athlete, event, division = _meta(html)

    # 웨이브 시작 = 첫 체크포인트 통과 시각 - 누적시간 (대회 현지 시각)
    wave_start = None
    m = re.search(
        r'"children":"(\d{1,2}:\d{2}:\d{2})"\}\],\["\$","td",null,\{"className":"[^"]*",'
        r'"children":"(\d{1,2}:\d{2}(?::\d{2})?)"',
        payload,
    )
    if m and result.get("date_start"):
        tod = datetime.strptime(f"{result['date_start']} {m.group(1)}", "%Y-%m-%d %H:%M:%S")
        wave_start = tod - timedelta(seconds=_parse_clock(m.group(2)))

    return Race(
        result_id=rid,
        athlete=result.get("name") or athlete,
        event=result.get("event") or event,
        division=division or result.get("division", ""),
        checkpoints=cps,
        station_times=st,
        wave_start=wave_start,  # 시간대 없음 (현지 시각)
        rank=by_cp.get("p_w8", {}).get("position"),
        field_size=cp_data.get("num_idps"),
        run_total=result.get("t_ra"),
        roxzone_total=result.get("t_rx"),
        segments=_build_segments(cps, st),
    )


def _parse_live(rid: str, html: str, payload: str) -> Race:
    rows: dict[str, tuple[int | None, int | None]] = {}
    for m in re.finditer(r'\["\$","tr","(t_[rw]\d)",', payload):
        if m.group(1) in rows:
            continue  # 같은 표가 두 번 들어 있다
        seg = payload[m.end(): m.end() + 2500]
        t = re.search(r'font-mono font-semibold","children":\["([^"]*)"', seg)
        tot = re.search(r'font-mono text-muted-foreground","children":"([^"]*)"', seg)
        rows[m.group(1)] = (_parse_clock(t and t.group(1)), _parse_clock(tot and tot.group(1)))

    cps = [rows.get(f"t_{k}{n}", (None, None))[1] for n in range(1, 9) for k in ("r", "w")]
    st = [rows.get(f"t_w{n}", (None, None))[0] for n in range(1, 9)]

    started = re.search(r'"startedAt":"([^"]+)","fallback":\d+,"isRunning":(true|false)', payload)
    wave_start = running = None
    if started:
        wave_start = datetime.strptime(started.group(1) + "00", "%Y-%m-%d %H:%M:%S%z")
        running = started.group(2) == "true"

    athlete, event, division = _meta(html)
    return Race(
        result_id=rid,
        athlete=athlete or rid,
        event=event,
        division=division,
        checkpoints=cps,
        station_times=st,
        wave_start=wave_start,
        live=True,
        running=bool(running),
        segments=_build_segments(cps, st),
    )


def fetch_race(url_or_id: str) -> Race:
    rid = extract_id(url_or_id)
    html = _get(f"/result/{rid}")
    payload = _rsc_payload(html)
    if '{"data":[{"checkpoint":"p_r1"' in payload:
        return _parse_finished(rid, html, payload)
    if '["$","tr","t_r1",' in payload:
        return _parse_live(rid, html, payload)
    raise ValueError("기록 데이터를 찾지 못했습니다 (아직 출발 전이거나 사이트 구조가 바뀌었을 수 있음)")


def fmt(sec: int | None) -> str:
    if sec is None:
        return "-"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
