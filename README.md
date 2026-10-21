# tw-mapdata — Dot Maps 보행·접근성 지도 데이터

Tactile Worlds 의 Dot Maps 가 닷패드 촉각 지도를 **1초 안팎으로** 그리도록,
매주 한 번 대한민국 OpenStreetMap 에서 보행·접근성 요소만 골라 작은 격자 파일로 공개합니다.
비용 0원(공개 저장소의 GitHub Actions + GitHub Pages).

- 결과: `https://young-dotincorp.github.io/tw-mapdata/v1/manifest.json`
- 칸 파일: `v1/cells/{la}/{la}_{lo}.json` (위도·경도 0.01도, `floor(좌표×100)`), 빈 칸은 파일 없음(404)
- 지명 색인(검색 2단계 · 1,610): `v1/names/index.json` + `v1/names/{la}/{la}_{lo}.json` (0.1도, `floor(좌표×10)`) — 한 줄 = [이름, 위도×1e5, 경도×1e5, 종류]. 역·지하철 출구(이름 없으면 «가까운 역 N번 출구»)·학교·복지관·병원·관공서·정류장·공원·상점. 만들다 실패해도 지도 파일은 그대로 배포됩니다.
- 데이터: © OpenStreetMap contributors, ODbL 1.0

## 처음 한 번 설정

1. GitHub → **New repository** → 이름 `tw-mapdata`, **Public**, 나머지는 비워 두고 Create.
2. **Add file → Upload files** 로 `build_cells.py`, `README.md`, `public` 폴더를 끌어다 놓고 Commit.
3. 워크플로 파일: **Add file → Create new file** → 파일 이름 칸에 `.github/workflows/build-mapdata.yml` 입력 →
   ZIP 안의 같은 파일 내용을 붙여 넣고 Commit. (Finder 는 `.github` 같은 점 폴더를 숨기기 때문)
4. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
5. **Actions → build-mapdata → Run workflow**. 15~25분 뒤 초록 체크가 뜨면 위 manifest 주소가 열립니다.

이후엔 매주 월요일 새벽 자동 갱신됩니다. 실패하면 배포하지 않아 지난주 데이터가 그대로 남습니다.
