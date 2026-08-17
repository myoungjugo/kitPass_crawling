// 원가 계산기 프론트 로직.
// 상품 검색은 대시보드와 같은 /api/results?q=... 엔드포인트를 재사용한다 (새 엔드포인트 안 만듦).
// 환율은 서버가 이미 캐싱해둔 걸(services/fx.py) /api/fx-rates로 그대로 받아와서,
// 상품가/배송비 통화가 달라도 클라이언트에서 바로 원화 환산한다.

const els = {
  search: document.getElementById("item-search"),
  resultsList: document.getElementById("item-results"),
  selectedInfo: document.getElementById("selected-item-info"),
  price: document.getElementById("item-price"),
  currency: document.getElementById("item-currency"),
  shipping: document.getElementById("shipping-amount"),
  shippingCurrency: document.getElementById("shipping-currency"),
  cardFeePct: document.getElementById("card-fee-pct"),
  dutyRatePct: document.getElementById("duty-rate-pct"),
  vatRatePct: document.getElementById("vat-rate-pct"),
  deminimisMode: document.getElementById("deminimis-mode"),
  breakdown: document.getElementById("breakdown"),
  totalKrw: document.getElementById("total-krw"),
};

let fxRates = null;
let searchDebounce = null;

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadFxRates() {
  try {
    const res = await fetch("/api/fx-rates");
    const data = await res.json();
    fxRates = data.rates || null;
  } catch (e) {
    console.error("환율 조회 실패:", e);
    fxRates = null;
  }
}

// USD 기준 rates 테이블로 임의 통화 -> KRW 환산 (services/fx.py의 to_krw()와 동일한 방식)
function toKrw(amount, currency) {
  if (!amount) return 0;
  currency = (currency || "KRW").toUpperCase();
  if (currency === "KRW") return amount;
  if (!fxRates || !fxRates.KRW || !fxRates[currency]) return null; // 환율 없으면 계산 불가
  return amount * (fxRates.KRW / fxRates[currency]);
}

async function searchItems(q) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  const res = await fetch(`/api/results?${params.toString()}`);
  const data = await res.json();
  return (data.items || []).slice(0, 20);
}

function renderResults(items) {
  if (!items.length) {
    els.resultsList.innerHTML = '<li class="calc-empty">검색 결과 없음</li>';
    els.resultsList._items = [];
    return;
  }
  els.resultsList.innerHTML = items.map((item, idx) => `
    <li class="calc-result" data-idx="${idx}">
      ${item.image ? `<img src="${escapeHtml(item.image)}" loading="lazy" alt="">` : ""}
      <div>
        <p class="calc-result__title">${escapeHtml(item.title)}</p>
        <p class="calc-result__meta">${escapeHtml(item.site)} · ${item.price.toFixed(2)} ${escapeHtml(item.currency)}</p>
      </div>
    </li>
  `).join("");
  els.resultsList._items = items;
}

function selectItem(item) {
  els.price.value = item.price;
  els.currency.value = item.currency;
  els.selectedInfo.innerHTML = `
    ${item.image ? `<img src="${escapeHtml(item.image)}" alt="">` : ""}
    <div>
      <p>${escapeHtml(item.title)}</p>
      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.site)}에서 보기</a>
    </div>
  `;
  els.resultsList.innerHTML = "";
  els.search.value = "";
  calculate();
}

els.resultsList.addEventListener("click", (e) => {
  const li = e.target.closest(".calc-result");
  if (!li) return;
  const item = (els.resultsList._items || [])[Number(li.dataset.idx)];
  if (item) selectItem(item);
});

els.search.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  const q = els.search.value.trim();
  if (!q) {
    els.resultsList.innerHTML = "";
    return;
  }
  searchDebounce = setTimeout(async () => {
    renderResults(await searchItems(q));
  }, 250);
});

function fmtKrw(n) {
  if (n == null || Number.isNaN(n)) return "-";
  return `₩${Math.round(n).toLocaleString("ko-KR")}`;
}

function calculate() {
  const priceKrw = toKrw(parseFloat(els.price.value) || 0, els.currency.value);
  const shippingKrw = toKrw(parseFloat(els.shipping.value) || 0, els.shippingCurrency.value);

  if (priceKrw == null || shippingKrw == null) {
    els.breakdown.innerHTML = '<p class="calc-empty">환율 정보를 불러오는 중이거나, 지원하지 않는 통화입니다.</p>';
    els.totalKrw.textContent = "-";
    return;
  }

  const cardFeePct = parseFloat(els.cardFeePct.value) || 0;
  const dutyRatePct = parseFloat(els.dutyRatePct.value) || 0;
  const vatRatePct = parseFloat(els.vatRatePct.value) || 0;
  const deminimisUsd = els.deminimisMode.value === "us" ? 200 : 150;
  const deminimisKrw = toKrw(deminimisUsd, "USD") || 0;

  const cardFeeKrw = (priceKrw + shippingKrw) * (cardFeePct / 100);

  // 개인통관 면세 기준: 관습적으로 "물품가격"(배송비 제외) 기준으로 판단.
  // 면세 초과 시 과세표준은 상품가+배송비(CIF 유사)로 단순화해서 계산.
  const isExempt = priceKrw <= deminimisKrw;
  let dutyKrw = 0;
  let vatKrw = 0;
  if (!isExempt) {
    const taxableBase = priceKrw + shippingKrw;
    dutyKrw = taxableBase * (dutyRatePct / 100);
    vatKrw = (taxableBase + dutyKrw) * (vatRatePct / 100);
  }

  const totalKrw = priceKrw + shippingKrw + cardFeeKrw + dutyKrw + vatKrw;

  els.breakdown.innerHTML = `
    <div class="calc-row"><span>상품가</span><span>${fmtKrw(priceKrw)}</span></div>
    <div class="calc-row"><span>배송비</span><span>${fmtKrw(shippingKrw)}</span></div>
    <div class="calc-row"><span>해외결제 수수료 (${cardFeePct}%)</span><span>${fmtKrw(cardFeeKrw)}</span></div>
    <div class="calc-row"><span>관세 ${isExempt ? "(면세)" : `(${dutyRatePct}%)`}</span><span>${fmtKrw(dutyKrw)}</span></div>
    <div class="calc-row"><span>부가세 ${isExempt ? "(면세)" : `(${vatRatePct}%)`}</span><span>${fmtKrw(vatKrw)}</span></div>
    <div class="calc-row calc-row--note">
      <span>${isExempt
        ? `면세 기준(약 ${fmtKrw(deminimisKrw)}, 물품가격 기준) 이하라 관세/부가세 없음`
        : `면세 기준(약 ${fmtKrw(deminimisKrw)}) 초과`}</span>
    </div>
  `;
  els.totalKrw.textContent = fmtKrw(totalKrw);
}

[els.price, els.currency, els.shipping, els.shippingCurrency,
 els.cardFeePct, els.dutyRatePct, els.vatRatePct, els.deminimisMode]
  .forEach((el) => el.addEventListener("input", calculate));

(async function init() {
  await loadFxRates();
  calculate();
})();