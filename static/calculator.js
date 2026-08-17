// 장바구니 기반 원가 계산기.
// 상품은 대시보드에서 "장바구니 담기"로 미리 담아온 것을 cart-store.js(localStorage)로
// 읽어온다. 체크된 항목들의 가격만 각각 원화로 환산해서 합산하고, 그 합계에 대해
// 배송비/수수료/관세/부가세를 계산한다 (계산 공식 자체는 기존 단일상품 계산기와
// 동일 — 대상만 "선택한 항목들의 합"으로 바뀜).

const els = {
  cartList: document.getElementById("cart-list"),
  selectAll: document.getElementById("cart-select-all"),
  emptyHint: document.getElementById("cart-empty-hint"),
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
// 장바구니 자체(담은 상품 목록)와 "이번 계산에 포함할지" 체크 상태는 다른 개념이라
// 따로 관리한다: product_id -> boolean (기본 true = 전체 선택)
let checkedState = {};

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

function toKrw(amount, currency) {
  if (!amount) return 0;
  currency = (currency || "KRW").toUpperCase();
  if (currency === "KRW") return amount;
  if (!fxRates || !fxRates.KRW || !fxRates[currency]) return null;
  return amount * (fxRates.KRW / fxRates[currency]);
}

function fmtKrw(n) {
  if (n == null || Number.isNaN(n)) return "-";
  return `₩${Math.round(n).toLocaleString("ko-KR")}`;
}

function renderCartList() {
  const cart = getCart();

  if (!cart.length) {
    els.cartList.innerHTML = "";
    els.emptyHint.hidden = false;
    els.selectAll.checked = false;
    els.selectAll.disabled = true;
    calculate();
    return;
  }
  els.emptyHint.hidden = true;
  els.selectAll.disabled = false;

  cart.forEach((item) => {
    if (!(item.product_id in checkedState)) checkedState[item.product_id] = true;
  });

  els.cartList.innerHTML = cart.map((item) => {
    const checked = checkedState[item.product_id] !== false;
    return `
      <li class="cart-item" data-product-id="${escapeHtml(item.product_id)}">
        <input type="checkbox" class="cart-item__check" ${checked ? "checked" : ""}>
        ${item.image ? `<img src="${escapeHtml(item.image)}" loading="lazy" alt="">` : ""}
        <div class="cart-item__info">
          <p class="cart-item__title">${escapeHtml(item.title)}</p>
          <p class="cart-item__meta">${escapeHtml(item.site)} · ${Number(item.price).toFixed(2)} ${escapeHtml(item.currency)}</p>
        </div>
        <button type="button" class="cart-item__remove" title="장바구니에서 빼기">✕</button>
      </li>
    `;
  }).join("");

  els.selectAll.checked = cart.every((item) => checkedState[item.product_id] !== false);
  calculate();
}

els.cartList.addEventListener("click", (e) => {
  const li = e.target.closest(".cart-item");
  if (!li) return;
  const productId = li.dataset.productId;

  if (e.target.closest(".cart-item__remove")) {
    delete checkedState[productId];
    removeFromCart(productId);
    renderCartList();
    return;
  }

  if (e.target.classList.contains("cart-item__check")) {
    checkedState[productId] = e.target.checked;
    els.selectAll.checked = getCart().every((item) => checkedState[item.product_id] !== false);
    calculate();
  }
});

els.selectAll.addEventListener("change", () => {
  const cart = getCart();
  cart.forEach((item) => { checkedState[item.product_id] = els.selectAll.checked; });
  renderCartList();
});

function calculate() {
  const cart = getCart();
  const selected = cart.filter((item) => checkedState[item.product_id] !== false);

  if (!selected.length) {
    els.breakdown.innerHTML = '<p class="calc-empty">계산할 상품을 체크해주세요.</p>';
    els.totalKrw.textContent = "-";
    return;
  }

  let priceKrw = 0;
  let fxMissing = false;
  for (const item of selected) {
    const converted = toKrw(Number(item.price) || 0, item.currency);
    if (converted == null) { fxMissing = true; break; }
    priceKrw += converted;
  }

  const shippingKrw = toKrw(parseFloat(els.shipping.value) || 0, els.shippingCurrency.value);

  if (fxMissing || shippingKrw == null) {
    els.breakdown.innerHTML = '<p class="calc-empty">환율 정보를 불러오는 중이거나, 지원하지 않는 통화가 포함돼 있습니다.</p>';
    els.totalKrw.textContent = "-";
    return;
  }

  const cardFeePct = parseFloat(els.cardFeePct.value) || 0;
  const dutyRatePct = parseFloat(els.dutyRatePct.value) || 0;
  const vatRatePct = parseFloat(els.vatRatePct.value) || 0;
  const deminimisUsd = els.deminimisMode.value === "us" ? 200 : 150;
  const deminimisKrw = toKrw(deminimisUsd, "USD") || 0;

  const cardFeeKrw = (priceKrw + shippingKrw) * (cardFeePct / 100);

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
    <div class="calc-row"><span>상품가 합계 (${selected.length}개)</span><span>${fmtKrw(priceKrw)}</span></div>
    <div class="calc-row"><span>배송비</span><span>${fmtKrw(shippingKrw)}</span></div>
    <div class="calc-row"><span>해외결제 수수료 (${cardFeePct}%)</span><span>${fmtKrw(cardFeeKrw)}</span></div>
    <div class="calc-row"><span>관세 ${isExempt ? "(면세)" : `(${dutyRatePct}%)`}</span><span>${fmtKrw(dutyKrw)}</span></div>
    <div class="calc-row"><span>부가세 ${isExempt ? "(면세)" : `(${vatRatePct}%)`}</span><span>${fmtKrw(vatKrw)}</span></div>
    <div class="calc-row calc-row--note">
      <span>${isExempt
        ? `면세 기준(약 ${fmtKrw(deminimisKrw)}, 물품가격 합계 기준) 이하라 관세/부가세 없음`
        : `면세 기준(약 ${fmtKrw(deminimisKrw)}) 초과`}</span>
    </div>
  `;
  els.totalKrw.textContent = fmtKrw(totalKrw);
}

[els.shipping, els.shippingCurrency, els.cardFeePct, els.dutyRatePct, els.vatRatePct, els.deminimisMode]
  .forEach((el) => el.addEventListener("input", calculate));

window.addEventListener("kitpass-cart-changed", renderCartList);

(async function init() {
  await loadFxRates();
  renderCartList();
})();