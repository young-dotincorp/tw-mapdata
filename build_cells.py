#!/usr/bin/env python3
"""build_cells.py — Dot Maps 보행·접근성 데이터 사전 추출기 (tw-mapdata)

왜: 공개 Overpass 서버는 전 세계가 함께 써서 자주 막힌다(2026-10-16 실측: 16회 모두 실패, 최대 25초).
    시각장애 사용자에게 «기다리다 실패»는 최악이다. 그래서 일주일에 한 번 OSM 원본에서
    보행·접근성 요소만 골라 «작은 격자 파일»로 미리 만들어 두고, 앱은 그 파일을 1초 안에 읽는다.

입력: OSM 파일(.osm.pbf / .osm) — 보통 `osmium tags-filter` 로 먼저 줄인 파일
출력: <out>/v1/manifest.json + <out>/v1/cells/<la>/<la>_<lo>.json
      격자 = 위도·경도 0.01도(서울 기준 약 1.1km × 0.9km). 칸 이름 = floor(좌표×100).
      비어 있는 칸은 파일을 만들지 않는다(앱은 404 = «이 칸엔 아무것도 없음»으로 읽는다).

앱(osmClient.js)의 Overpass 질의와 «같은 요소»만 담는다 → 지도 모양이 바뀌지 않는다.
  · 길: highway=footway|path|pedestrian|steps|living_street
  · 횡단보도: way footway=crossing | highway=crossing, node highway=crossing
  · 점자블록: way/node tactile_paving
  · 지점: node highway=bus_stop | public_transport=platform | entrance
  · 도로명 참조: 이름 있는 차도(primary…road) — 안내 문장의 «○○로 방향»용
  (건물은 앱이 지금 쓰지 않아 뺀다 — 파일 크기의 대부분을 차지한다)

라이선스: OSM 데이터는 ODbL. 결과물도 ODbL 로 공개한다(manifest·index.html 에 표기).
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

import osmium

CELL = 0.01            # 도
PREC = 6               # 소수 6자리 ≈ 0.1m
FOOT = {"footway", "path", "pedestrian", "steps", "living_street"}
ROAD = {"primary", "secondary", "tertiary", "residential", "unclassified",
        "living_street", "service", "trunk", "pedestrian", "road"}
KEEP = {  # 앱이 읽는 태그만(크기 절약)
    "name", "name:ko", "name:en", "highway", "footway", "crossing", "crossing:signals",
    "crossing_ref", "tactile_paving", "traffic_signals:sound", "traffic_signals:vibration",
    "kerb", "incline", "entrance", "public_transport", "bus", "ref", "level", "layer",
    "bridge", "tunnel", "wheelchair", "sidewalk", "surface", "indoor", "step_count",
    "handrail", "ramp",
}


def keep_tags(tags):
    return {t.k: t.v for t in tags if t.k in KEEP}


def way_wanted(t):
    hw = t.get("highway")
    if hw in FOOT:
        return True
    if t.get("footway") == "crossing" or hw == "crossing":
        return True
    if "tactile_paving" in t:
        return True
    if hw in ROAD and ("name" in t or "name:ko" in t):
        return True
    return False


def node_wanted(t):
    return (
        t.get("highway") in ("crossing", "bus_stop")
        or t.get("public_transport") == "platform"
        or "entrance" in t
        or "tactile_paving" in t
    )


def cell_of(lat, lon):
    return (math.floor(lat / CELL), math.floor(lon / CELL))


class Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.cells = defaultdict(list)
        self.stats = defaultdict(int)
        self.bounds = [90.0, 180.0, -90.0, -180.0]  # s w n e

    def _grow(self, lat, lon):
        b = self.bounds
        b[0] = min(b[0], lat); b[1] = min(b[1], lon); b[2] = max(b[2], lat); b[3] = max(b[3], lon)

    def node(self, n):
        if not n.tags:
            return
        t = {tg.k: tg.v for tg in n.tags}
        if not node_wanted(t) or not n.location.valid():
            return
        lat, lon = round(n.location.lat, PREC), round(n.location.lon, PREC)
        self.cells[cell_of(lat, lon)].append({"type": "node", "id": n.id, "lat": lat, "lon": lon, "tags": keep_tags(n.tags)})
        self.stats["node"] += 1
        self._grow(lat, lon)

    def way(self, w):
        t = {tg.k: tg.v for tg in w.tags}
        if not way_wanted(t):
            return
        g = []
        for nd in w.nodes:
            if not nd.location.valid():
                continue
            g.append(round(nd.location.lat, PREC)); g.append(round(nd.location.lon, PREC))
        if len(g) < 4:
            self.stats["way_skipped"] += 1
            return
        tags = keep_tags(w.tags)
        lats, lons = g[0::2], g[1::2]
        # 【v1.1】 길을 «칸마다 그 칸을 지나는 조각»으로 자른다.
        #   예전처럼 길 전체를 지나가는 모든 칸에 통째로 넣으면, 수십 km 도로(수천 점)가 칸 수백 개에
        #   복제되어 결과가 수 GB 로 불어난다(첫 실행 실패 원인 후보). 조각 끝은 이웃 칸까지 한 점 겹쳐
        #   앱에서 이어 붙여도 끊기지 않는다(경로 엔진이 같은 좌표를 한 노드로 합친다).
        segs = defaultdict(list)  # cell -> [segment index]
        for i in range(len(lats) - 1):
            c0 = cell_of(min(lats[i], lats[i + 1]), min(lons[i], lons[i + 1]))
            c1 = cell_of(max(lats[i], lats[i + 1]), max(lons[i], lons[i + 1]))
            if (c1[0] - c0[0] + 1) * (c1[1] - c0[1] + 1) > 16:  # 한 구간이 4칸 넘게 뻗음(데이터 오류) → 건너뜀
                self.stats["seg_too_long"] += 1
                continue
            for la in range(c0[0], c1[0] + 1):
                for lo in range(c0[1], c1[1] + 1):
                    segs[(la, lo)].append(i)
        part = 0
        for cell, idx in segs.items():
            run = [idx[0]]
            runs = []
            for j in idx[1:]:
                if j == run[-1] + 1:
                    run.append(j)
                else:
                    runs.append(run); run = [j]
            runs.append(run)
            for r in runs:
                pts = []
                for k in range(r[0], r[-1] + 2):
                    pts.append(lats[k]); pts.append(lons[k])
                self.cells[cell].append({"type": "way", "id": w.id, "p": part, "tags": tags, "g": pts})
                part += 1
        if part:
            self.stats["way"] += 1
            self.stats["way_parts"] += part
            for a, b in zip(lats, lons):
                self._grow(a, b)


def source_date(path):
    try:
        h = osmium.io.Reader(path, osmium.osm.osm_entity_bits.NOTHING).header()
        ts = h.get("osmosis_replication_timestamp") or h.get("timestamp")
        if ts:
            return ts
    except Exception:  # noqa: BLE001 — 헤더가 없어도 만든다
        pass
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    if len(sys.argv) < 3:
        print("usage: build_cells.py <input.osm.pbf> <out_dir> [--max-mb 900]", file=sys.stderr)
        sys.exit(2)
    src, out = sys.argv[1], sys.argv[2]
    max_mb = float(sys.argv[sys.argv.index("--max-mb") + 1]) if "--max-mb" in sys.argv else 900.0

    t0 = time.time()
    h = Collector()
    h.apply_file(src, locations=True, idx="flex_mem")

    root = os.path.join(out, "v1")
    os.makedirs(os.path.join(root, "cells"), exist_ok=True)
    total = 0
    biggest = (0, None)
    for (la, lo), els in h.cells.items():
        d = os.path.join(root, "cells", str(la))
        os.makedirs(d, exist_ok=True)
        body = json.dumps({"v": 1, "elements": els}, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(d, f"{la}_{lo}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        total += len(body.encode("utf-8"))
        if len(body) > biggest[0]:
            biggest = (len(body), f"{la}_{lo}")

    s, w, n, e = h.bounds
    manifest = {
        "v": 1,
        "cell": CELL,
        "path": "cells/{la}/{la}_{lo}.json",
        "coverage": {"south": s, "west": w, "north": n, "east": e} if h.cells else None,
        "sourceDate": source_date(src),
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cells": len(h.cells),
        "stats": dict(h.stats),
        "bytes": total,
        "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
    }
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    mb = total / 1e6
    print(f"cells={len(h.cells)} size={mb:.1f}MB biggest={biggest[1]}({biggest[0]/1e3:.0f}KB) "
          f"stats={dict(h.stats)} in {time.time()-t0:.0f}s")
    if mb > max_mb:  # GitHub Pages 사이트 한도(1GB) 보호 — 넘으면 배포하지 않는다(지난주 데이터 유지)
        print(f"ERROR: {mb:.0f}MB > {max_mb:.0f}MB", file=sys.stderr)
        sys.exit(1)
    if not h.cells:
        print("ERROR: 빈 결과 — 원본이 잘못됐을 수 있어 배포하지 않습니다", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
