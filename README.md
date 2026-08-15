# 축구 유니폼 크롤링 프로젝트

전체 카탈로그에서 축구팀 유니폼(S/M/L 재고 있는 것)을 찾아 가격 낮은 순으로 모아주는 개인용 트래커.

## 구조

```
core/            사이트 무관 공통 로직
  models.py        Product 데이터 모델
  diagnostics.py    네트워크 진단(속도/에러 측정)
  results_store.py  점진적 저장 (배치 들어올 때마다 필터+정렬+저장)
  orchestrator.py   사이트 병렬 실행
  config.py         이번 실행에 포함할 사이트 목록

services/        사이트 무관 공통 서비스
  http_client.py    재시도 로직 (429/5xx/네트워크 에러 구분해서 재시도)
  filters.py        축구팀 유니폼 판별 + S/M/L 재고 정규화
  notifier.py        Discord 알림 전송
  notify_state.py    알림 기준점(baseline) 관리

fetchers/        사이트별 파싱 (새 사이트 추가 시 여기만 건드리면 됨)
  base.py           공통 인터페이스 (fetch(on_batch) 콜백 방식)
  cultkits.py         Shopify products.json — 완성, 실제 재시도 버그 수정 반영
  classicfootballshirts.py  Magento, 사이트맵+상세페이지 — 동작하지만 사이즈 파싱 검증 필요
  kitbag.py           Fanatics 플랫폼 — 동작하지만 사이즈 파싱 검증 필요 (아래 참고)

main.py          실행 진입점
data/results/latest.json   실행 중에도 계속 갱신되는 결과 (viz가 읽을 파일)
data/diagnostics/          사이트별 요청 로그
```

## 실행 방법

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 필요시 값 수정
python main.py
```

실행 중에도 `data/results/latest.json`을 아무 때나 열어보면 그 시점까지 수집된
(필터+가격순 정렬 완료된) 결과를 바로 볼 수 있습니다 — 전체가 끝날 때까지 기다릴 필요 없음.

## 이번에 바뀐 것 (이전 대화 맥락)

1. **점진적 저장**: 사이트를 순차가 아니라 병렬로 돌리고(`core/orchestrator.py`),
   배치(페이지/상세페이지 묶음) 하나가 끝날 때마다 결과 파일을 즉시 갱신합니다
   (`core/results_store.py`). 예전엔 전체 파이프라인이 끝나야 결과가 하나도
   안 보였는데, 이제 가장 빠른 사이트의 첫 배치만 끝나도 바로 볼 수 있습니다.
2. **공통 구조 분리**: `services/`에 필터링·재시도·알림 로직을 공통으로 빼서,
   `fetchers/`의 사이트별 파일은 "이 사이트에서 어떻게 파싱하는지"만 담당합니다.
   새 사이트 추가할 때 필터/정렬/저장/알림 코드는 전혀 안 건드려도 됩니다.
3. **재시도 버그 수정**: 예전 cultkits.py는 네트워크 예외(타임아웃 등)를 못 잡아서
   "요청 실패"와 "카탈로그 진짜 끝"을 구분 못 했습니다 (`services/http_client.py`에서 수정,
   모든 fetcher가 공통으로 씀).
4. **United Store 제외**: todo 기준 재고 없음 확인되어 `core/config.py`에서 제외.

## ⚠️ 실행 전 반드시 확인해야 할 것

이번 세션에서 CFS/Kitbag은 실제 페이지를 최대한 조회해서 만들었지만, 두 가지는
도구 제약(JS/script 블록이 마크다운 변환 과정에서 제거됨) 때문에 **검증을 못 했습니다**:

### 1. 사이즈별 재고 파싱 (CFS, Kitbag 둘 다)
`_extract_sizes_best_effort()` 함수가 각 파일에 있는데, 표준 패턴을 가정한
추측입니다. **실제로 돌려보고 사이즈가 하나도 안 잡히면** (필터 때문에 상품이
0개로 나올 수 있음), 브라우저 개발자도구로 실제 상품 페이지를 열어서:
- CFS: 페이지 소스에서 `jsonConfig` 검색 (Magento 스와치 렌더러)
- Kitbag: "Size" 버튼들의 실제 HTML(class, data-속성, 품절 표시 방식) 확인

확인한 구조에 맞춰 해당 함수만 고치면 됩니다 (다른 코드는 안 건드려도 됨).

### 2. Kitbag 플랫폼이 프로젝트 문서와 다름
문서에는 "__platform_data__ JSON 임베드"라고 되어 있었는데, 지금 kitbag.com은
Fanatics 플랫폼(`www4.kitbag.com/en/{리그}/{팀}/{slug}/o-...` 형태)으로 바뀐
것으로 보입니다. `fetchers/kitbag.py` 상단 docstring에 확인된/안 된 내용을
정리해뒀습니다. `.env`의 `KITBAG_BASE_URL`, `KITBAG_CATEGORY_PATH`로 도메인/카테고리를
바꿀 수 있으니, 실제 접속해서 리다이렉트되는 도메인(www/www2/www3/www4)과
카테고리 URL을 확인해서 맞춰주세요.

### 3. CFS 상품 수 (10만 개+)
매 실행마다 전체를 도는 건 무리라 `CFS_MAX_PRODUCTS`(기본 1000)로 제한해뒀습니다.
todo에 있던 "최초 1회만 전체 수집, 이후 신상만 diff" 방식이 이 문제의 진짜 해결책이고,
이번엔 그 전까지의 임시 안전장치만 넣었습니다.

## 아직 안 만든 것 (다음에 할 것들)

- **diff 기반 신상 감지**: 지금 `main.py`는 매번 전체 재수집. "이전 실행과 비교해서
  새 상품만" 로직은 아직 없음 (todo의 핵심 항목)
- **시각화(viz) 화면**: `data/results/latest.json`을 읽어서 보여주는 대시보드
- **다운로드 기능**: 현재 리스트를 AI-readable 텍스트로 내보내기
- **CFS/Kitbag 사이즈 파싱 검증**: 위 참고
- **알림 on/off 전환 UI**: 지금은 환경변수(`NOTIFY_ENABLED`)로만 제어됨.
  스펙대로면 시각화 화면 안에서 토글 가능해야 함
