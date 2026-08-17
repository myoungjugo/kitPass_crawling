// 장바구니 저장소 — localStorage 기반 (서버에 저장 안 함, 브라우저/기기별로 따로 유지됨).
// dashboard.js(담기 버튼)와 calculator.js(장바구니 목록+계산)가 같이 쓴다.
const CART_KEY = "kitpass_cart_v1";

function getCart() {
  try {
    const raw = localStorage.getItem(CART_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function saveCart(items) {
  localStorage.setItem(CART_KEY, JSON.stringify(items));
  window.dispatchEvent(new CustomEvent("kitpass-cart-changed", { detail: { count: items.length } }));
}

function isInCart(productId) {
  return getCart().some((i) => i.product_id === productId);
}

function addToCart(item) {
  const cart = getCart();
  if (cart.some((i) => i.product_id === item.product_id)) return cart; // 중복 방지
  cart.push(item);
  saveCart(cart);
  return cart;
}

function removeFromCart(productId) {
  const cart = getCart().filter((i) => i.product_id !== productId);
  saveCart(cart);
  return cart;
}

function cartCount() {
  return getCart().length;
}