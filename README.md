# 다음걸음 (next-step-care)

노인·장애 당사자와 보호자에게 **"다음에 뭘 하면 되는지"**를 쉽고 빠르게 알려 주는 공익 서비스 (가칭).
돌봄이 갑자기 필요해졌을 때 받을 수 있는 제도, 우리 동네 기관, 공식 창구를 순서대로 이어 준다.

- 목적: 공익. 수익·경쟁은 고려하지 않음
- 대상: 당사자 / 보호자 두 진입로
- 첫 지역: 화성시 (경기도 데이터 우선)
- 첫 시나리오: 퇴원 후 집에서 돌봐야 할 때

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/DATA_TOOL_FEATURE.md`](docs/DATA_TOOL_FEATURE.md) | 데이터 → 툴 → 기능 구조, 기능 목록(탑다운), 첫 단계 |
| [`docs/DIAGNOSIS.md`](docs/DIAGNOSIS.md) | 자가 진단과 방향 수정 |
| [`registry/datasets.csv`](registry/datasets.csv) | 원천 데이터 목록 (계층·대상·우선순위) |
| [`docs/DATA_STATUS.md`](docs/DATA_STATUS.md) | 수집 중인 데이터와 채움 현황 |

## 데이터 수집

```
python -m nsc sync pull                       # R2에서 상태·최근 수집본 받기
python -m nsc collect ltc welfare             # 매일: 장기요양기관(+상세), 복지서비스
python -m nsc collect providers standard files  # 매주: 사회서비스 제공기관, 표준데이터, 포털 파일
python -m nsc build                           # 화성시 화면 데이터(site/data/hwaseong) + 변경 기록
python -m nsc sync push                       # R2에 올리기
```

GitHub Actions `collect`가 매일 06:40, 일요일 05:10(한국)에 돌고, `pages`가 결과를 GitHub Pages에 배포합니다.
필요한 Secrets: `DATA_GO_KR_KEY`, `VWORLD_KEY`(주소→좌표, 브이월드), `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
사용자 입력은 저장하지 않습니다.

## 출발점

공공데이터 아이디어 뱅크([public-api-monitor](https://github.com/flytothejy/public-api-monitor))의 큰 방향 '돌봄·포용 내비'에서 파생.
