#!/usr/bin/env python3
"""build_places.py — Dot Maps 지명 색인(검색 2단계: 오타·초성·부분 이름) 【1,610】

왜: OSM 공식 검색(Nominatim)은 띄어쓰기 단위로만 맞추고, 오타·초성(«ㅅㅇㅅㅊ»)·부분 이름(«맹학교»)을
    못 찾는다. 1초에 1번만 물을 수 있어 여러 번 다시 묻기도 느리다. 그래서 일주일에 한 번 «이름 있는 곳»만
    골라 지역별 작은 색인 파일을 만들고, 앱이 그 지역 파일을 받아 손안에서 찾는다(Pelias·Photon 같은
    지명 색인 + Algolia 식 오타 허용 + 네이버·카카오 식 초성 검색).

입력: OSM 파일(보통 `osmium tags-filter` 로 이름 있는 곳만 먼저 줄인 파일)
출력: <out>/v1/names/index.json + <out>/v1/names/<la>/<la>_<lo>.json
      칸 = 위도·경도 0.1도(서울 기준 약 11km × 9km). 칸 이름 = floor(좌표×10).
      한 줄 = [이름, 위도×1e5(정수), 경도×1e5(정수), 종류 글자]  — 작게(앱이 3×3칸을 받는다)
종류 글자: s 역·지하철 출구 · c 복지·돌봄 · e 학교·도서관 · h 병원·약국 · g 관공서 · b 버스정류장 · p 공원·볼거리 · m 상점 · o 그 밖
지하철 출구에 이름이 없으면 500m 안 가장 가까운 역 이름 + «N번 출구»로 짓는다(OSM 한국 데이터는 출구에 번호만 있는 경우가 많다).

라이선스: OSM 데이터는 ODbL. 결과물도 ODbL 로 공개한다(index.json 에 표기).
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

import osmium

CELL = 0.1
# 앱 koFuzzy KIND_RANK 와 같은 순서(역 → 복지 → 학교 → 관공서 → 병원 → 정류장 → 공원 → 상점 → 그 밖)
KIND_ORDER = {k: i for i, k in enumerate("sceghbpmo")}
SCALE = 100000


def kind_of(t):
    rw, pt, am, hw = t.get("railway"), t.get("public_transport"), t.get("amenity"), t.get("highway")
    if rw in ("station", "halt", "subway_entrance") or (pt == "station"):
        return "s"
    if am in ("social_facility", "community_centre") or t.get("social_facility"):
        return "c"
    if am in ("school", "university", "college", "library", "kindergarten"):
        return "e"
    if am in ("hospital", "clinic", "pharmacy", "doctors"):
        return "h"
    if am in ("townhall", "police", "post_office", "courthouse", "fire_station") or t.get("office") == "government":
        return "g"
    if hw == "bus_stop" or pt == "platform":
        return "b"
    if t.get("leisure") == "park" or t.get("tourism") in ("attraction", "museum", "gallery", "viewpoint") or t.get("historic"):
        return "p"
    if t.get("shop") in ("convenience", "supermarket", "department_store", "mall") or am in ("bank", "marketplace"):
        return "m"
    return "o"


def name_of(t):
    return (t.get("name:ko") or t.get("name") or "").strip()


def cell_of(lat, lon):
    return (math.floor(lat / CELL), math.floor(lon / CELL))


def dist_m(a_lat, a_lon, b_lat, b_lon):
    k = math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot((a_lat - b_lat) * 111320, (a_lon - b_lon) * 111320 * k)


class Names(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.items = []          # (name, lat, lon, kind)
        self.stations = []       # (name, lat, lon)
        self.exits = []          # (ref, lat, lon)
        self.stats = defaultdict(int)

    def _add(self, t, lat, lon):
        kind = kind_of(t)
        nm = name_of(t)
        if t.get("railway") == "subway_entrance" and not nm:
            ref = (t.get("ref") or "").strip()
            if ref:
                self.exits.append((ref, lat, lon))
            return
        if not nm or len(nm) > 60:
            return
        if t.get("railway") == "station" or t.get("public_transport") == "station":
            self.stations.append((nm, lat, lon))
        self.items.append((nm, lat, lon, kind))
        self.stats[kind] += 1

    def node(self, n):
        if not n.tags or not n.location.valid():
            return
        self._add({tg.k: tg.v for tg in n.tags}, n.location.lat, n.location.lon)

    def way(self, w):
        t = {tg.k: tg.v for tg in w.tags}
        if not name_of(t):
            return
        lats, lons = [], []
        for nd in w.nodes:
            if nd.location.valid():
                lats.append(nd.location.lat); lons.append(nd.location.lon)
        if not lats:
            return
        self._add(t, sum(lats) / len(lats), sum(lons) / len(lons))   # 면(학교 담장 등)은 가운데 점


def main():
    if len(sys.argv) < 3:
        print("usage: build_places.py <input.osm.pbf> <out_dir> [--max-mb 150]", file=sys.stderr)
        sys.exit(2)
    src, out = sys.argv[1], sys.argv[2]
    max_mb = float(sys.argv[sys.argv.index("--max-mb") + 1]) if "--max-mb" in sys.argv else 150.0
    t0 = time.time()
    h = Names()
    h.apply_file(src, locations=True, idx="flex_mem")

    # 이름 없는 지하철 출구 → «가장 가까운 역 + N번 출구»(500m 안 · 역 칸 색인으로 빠르게)
    by_cell = defaultdict(list)
    for s in h.stations:
        by_cell[(math.floor(s[1] * 100), math.floor(s[2] * 100))].append(s)
    for ref, lat, lon in h.exits:
        best, bd = None, 500.0
        c = (math.floor(lat * 100), math.floor(lon * 100))
        for dla in (-1, 0, 1):
            for dlo in (-1, 0, 1):
                for s in by_cell.get((c[0] + dla, c[1] + dlo), []):
                    d = dist_m(lat, lon, s[1], s[2])
                    if d < bd:
                        bd, best = d, s
        if best:
            base = best[0] if best[0].endswith("역") else best[0] + "역"
            h.items.append((f"{base} {ref}번 출구", lat, lon, "s"))
            h.stats["s_exit"] += 1
        else:
            h.stats["exit_no_station"] += 1

    # 같은 이름이 30m 안에 겹치면 하나만(정류장 양방향 등은 30m 넘게 떨어져 둘 다 남는다)
    cells = defaultdict(list)
    seen = defaultdict(list)
    for nm, lat, lon, kind in h.items:
        key = nm.replace(" ", "")
        if any(dist_m(lat, lon, a, b) < 30 for a, b in seen[key]):
            h.stats["dup"] += 1
            continue
        seen[key].append((lat, lon))
        cells[cell_of(lat, lon)].append([nm, round(lat * SCALE), round(lon * SCALE), kind])

    root = os.path.join(out, "v1", "names")
    os.makedirs(root, exist_ok=True)
    total, biggest = 0, (0, None)
    for (la, lo), rows in cells.items():
        rows.sort(key=lambda r: (KIND_ORDER.get(r[3], 9), r[0]))   # 모르는 종류 글자가 와도 멈추지 않게(뒤로)
        d = os.path.join(root, str(la))
        os.makedirs(d, exist_ok=True)
        body = json.dumps({"v": 1, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(d, f"{la}_{lo}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        n = len(body.encode("utf-8"))
        total += n
        if n > biggest[0]:
            biggest = (n, f"{la}_{lo}")
    index = {
        "v": 1, "cell": CELL, "scale": SCALE, "path": "names/{la}/{la}_{lo}.json",
        "cols": ["name", "lat", "lon", "kind"],
        "kinds": {"s": "역·지하철 출구", "c": "복지·돌봄", "e": "학교·도서관", "h": "병원·약국", "g": "관공서",
                  "b": "버스정류장", "p": "공원·볼거리", "m": "상점", "o": "그 밖"},
        "cells": len(cells), "rows": sum(len(r) for r in cells.values()), "bytes": total,
        "stats": dict(h.stats), "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
    }
    with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    mb = total / 1e6
    print(f"names cells={len(cells)} rows={index['rows']} size={mb:.1f}MB biggest={biggest[1]}({biggest[0]/1e3:.0f}KB) "
          f"stats={dict(h.stats)} in {time.time()-t0:.0f}s")
    if mb > max_mb:
        print(f"ERROR: {mb:.0f}MB > {max_mb:.0f}MB", file=sys.stderr)
        sys.exit(1)
    if not cells:
        print("ERROR: 빈 결과 — 배포하지 않습니다", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
