// 대시보드 프론트 로직.
// main.py가 백그라운드에서 계속 data/results/latest.json을 갱신하는 동안,
// 이 스크립트는 /api/results를 주기적으로 폴링해서 화면을 그 시점 최신 상태로 유지한다.
// (그래서 "수집이 다 끝나야 뭔가 보인다"가 아니라, 첫 배치가 들어오는 순간부터 바로 보임)

const els = {
  count: document.getElementById("count"),
  sitesWrap: document.getElementById("sites"),
  updatedAt: document.getElementById("updated-at"),
  sheetBody: document.getElementById("sheet-body"),
  emptyState: document.getElementById("empty-state"),
  search: document.getElementById("search"),
  siteFilter: document.getElementById("site-filter"),
  maxPrice: document.getElementById("max-price"),
  downloadMd: document.getElementById("download-md"),
  downloadJson: document.getElementById("download-json"),
  notifyToggle: document.getElementById("notify-toggle"),
  notifyCeiling: document.getElementById("notify-ceiling"),
  collectNow: document.getElementById("collect-now"),
  collectStatus: document.getElementById("collect-status"),
};

let lastCount = els.count ? Number(els.count.textContent) : 0;
let debounceTimer = null;

function currentFilterParams() {
  const params = new URLSearchParams();
  if (els.search.value.trim()) params.set("q", els.search.value.trim());
  if (els.siteFilter.value) params.set("site", els.siteFilter.value);
  if (els.maxPrice.value) params.set("max_price", els.maxPrice.value);
  return params;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderRows(items) {
  if (!items.length) {
    els.sheetBody.innerHTML = "";
    els.emptyState.hidden = false;
    return;
  }
  els.emptyState.hidden = true;

  els.sheetBody.innerHTML = items.map((item) => `
    <tr>
      <td class="col-img">${item.image ? `<img src="${escapeHtml(item.image)}" loading="lazy" alt="">` : ""}</td>
      <td class="col-title">${escapeHtml(item.title)}</td>
      <td><span class="tag">${escapeHtml(item.site)}</span></td>
      <td class="col-sizes">${item.sizes_in_stock.map((s) => `<span class="size-chip">${escapeHtml(s)}</span>`).join("")}</td>
      <td class="col-price">${item.price.toFixed(2)} ${escapeHtml(item.currency)}</td>
      <td class="col-link"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">보러가기</a></td>
    </tr>
  `).join("");
}

function renderSites(sitesDone) {
  const entries = Object.entries(sitesDone || {});
  if (!entries.length) return;
  els.sitesWrap.innerHTML = entries.map(([site, done]) => `
    <span class="pill ${done ? "pill--done" : "pill--live"}" data-site="${escapeHtml(site)}">
      ${escapeHtml(site)} · ${done ? "완료" : "수집중"}
    </span>
  `).join("");
}

function renderCount(count) {
  els.count.textContent = count;
  if (count !== lastCount) {
    els.count.classList.add("pulse");
    setTimeout(() => els.count.classList.remove("pulse"), 600);
    lastCount = count;
  }
}

function applyCollectionStatus(status) {
  if (!status) return;
  const running = status.status === "running";
  els.collectNow.disabled = running;
  els.collectNow.textContent = running ? "수집 중…" : "지금 수집하기";
  els.collectStatus.classList.toggle("status-text--error", status.status === "error");
  els.collectStatus.textContent = status.status === "error" ? `오류: ${status.error || "알 수 없음"}` : "";
  return running;
}

async function refresh() {
  try {
    const res = await fetch(`/api/results?${currentFilterParams().toString()}`);
    if (!res.ok) return;
    const snapshot = await res.json();

    renderCount(snapshot.count);
    renderSites(snapshot.sites_done);
    renderRows(snapshot.items);
    els.updatedAt.textContent = snapshot.generated_at || "-";
    const collecting = applyCollectionStatus(snapshot.collection_status);

    return { sitesDone: snapshot.sites_done, collecting };
  } catch (e) {
    console.error("결과 갱신 실패:", e);
  }
}

function schedulePoll() {
  refresh().then((state) => {
    const sitesDone = state && state.sitesDone;
    const allSitesDone = sitesDone && Object.values(sitesDone).length > 0
      && Object.values(sitesDone).every(Boolean);
    const stillBusy = (state && state.collecting) || !allSitesDone;
    // 수집 중이거나 아직 안 끝난 사이트가 있으면 자주, 다 끝났으면 뜸하게
    setTimeout(schedulePoll, stillBusy ? 2500 : 20000);
  });
}

function debouncedRefresh() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(refresh, 250);
}

[els.search, els.siteFilter, els.maxPrice].forEach((el) => {
  el.addEventListener("input", debouncedRefresh);
});

els.downloadMd.addEventListener("click", () => {
  const params = currentFilterParams();
  params.set("format", "md");
  window.location.href = `/download?${params.toString()}`;
});

els.downloadJson.addEventListener("click", () => {
  const params = currentFilterParams();
  params.set("format", "json");
  window.location.href = `/download?${params.toString()}`;
});

async function saveNotifySettings() {
  const body = {
    enabled: els.notifyToggle.checked,
    price_ceiling: els.notifyCeiling.value || null,
  };
  try {
    await fetch("/api/notify-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    console.error("알림 설정 저장 실패:", e);
  }
}

els.notifyToggle.addEventListener("change", saveNotifySettings);
els.notifyCeiling.addEventListener("change", saveNotifySettings);

els.collectNow.addEventListener("click", async () => {
  els.collectNow.disabled = true;
  els.collectNow.textContent = "요청 중…";
  try {
    const res = await fetch("/api/collect", { method: "POST" });
    const data = await res.json();
    if (!data.started) {
      // 이미 다른 요청으로 수집이 돌고 있던 경우 - 상태만 반영
      console.log("이미 수집이 진행 중입니다.");
    }
    applyCollectionStatus(data.status);
  } catch (e) {
    console.error("수집 시작 요청 실패:", e);
    els.collectNow.disabled = false;
    els.collectNow.textContent = "지금 수집하기";
  }
});

schedulePoll();