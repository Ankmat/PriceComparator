'use strict';

/* ─── DOM refs ──────────────────────────────────────── */
const tabSearch        = document.getElementById('tab-search');
const tabBasket        = document.getElementById('tab-basket');
const searchForm       = document.getElementById('search-form');
const basketForm       = document.getElementById('basket-form');
const quickChips       = document.getElementById('quick-chips');
const searchInput      = document.getElementById('search-input');
const searchBtn        = document.getElementById('search-btn');
const basketInput      = document.getElementById('basket-input');
const basketAddBtn     = document.getElementById('basket-add-btn');
const basketChips      = document.getElementById('basket-chips');
const basketCompareBtn = document.getElementById('basket-compare-btn');

const loadingState     = document.getElementById('loading-state');
const loadingMsg       = document.getElementById('loading-msg');
const errorState       = document.getElementById('error-state');
const errorMsg         = document.getElementById('error-msg');
const retryBtn         = document.getElementById('retry-btn');
const results          = document.getElementById('results');
const basketResults    = document.getElementById('basket-results');

const resultsMeta      = document.getElementById('results-meta');
const bestBanner       = document.getElementById('best-banner');
const bestName         = document.getElementById('best-product-name');
const bestStore        = document.getElementById('best-product-store');
const bestUnit         = document.getElementById('best-product-unit');
const suggSection      = document.getElementById('suggestion-section');

const wwList           = document.getElementById('ww-list');
const colesList        = document.getElementById('coles-list');
const aldiList         = document.getElementById('aldi-list');
const wwCount          = document.getElementById('ww-count');
const colesCount       = document.getElementById('coles-count');
const aldiCount        = document.getElementById('aldi-count');

const basketSummary    = document.getElementById('basket-summary');
const basketBreakdown  = document.getElementById('basket-breakdown');

/* ─── State ─────────────────────────────────────────── */
let pending     = false;
let lastQuery   = '';
let basketItems = [];

/* ─── Tab switching ─────────────────────────────────── */
[tabSearch, tabBasket].forEach(tab => {
  tab.addEventListener('click', () => {
    tabSearch.classList.toggle('active', tab === tabSearch);
    tabBasket.classList.toggle('active', tab === tabBasket);
    tabSearch.setAttribute('aria-selected', tab === tabSearch);
    tabBasket.setAttribute('aria-selected', tab === tabBasket);

    const isSearch = tab.dataset.tab === 'search';
    searchForm.classList.toggle('hidden', !isSearch);
    basketForm.classList.toggle('hidden',  isSearch);
    quickChips.classList.toggle('hidden', !isSearch);
    showOnly(null);
  });
});

/* ─── Quick-search chips ────────────────────────────── */
document.querySelectorAll('.qchip').forEach(chip => {
  chip.addEventListener('click', () => {
    searchInput.value = chip.dataset.q;
    doSearch();
  });
});

/* ─── Search ────────────────────────────────────────── */
searchForm.addEventListener('submit', e => { e.preventDefault(); doSearch(); });
retryBtn.addEventListener('click', () => doSearch());

async function doSearch(queryOverride) {
  const query = (queryOverride || searchInput.value).trim();
  if (!query || pending) return;
  lastQuery = query;
  searchInput.value = query;

  pending = true;
  searchBtn.disabled = true;
  setText(loadingMsg, 'Searching Woolworths, Coles & Aldi simultaneously…');
  showOnly(loadingState);

  try {
    const res  = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Server error ${res.status}`);
    }
    const data = await res.json();
    renderSearch(query, data);
  } catch (err) {
    setText(errorMsg, err.message || 'Something went wrong. Please try again.');
    showOnly(errorState);
  } finally {
    pending = false;
    searchBtn.disabled = false;
  }
}

/* ─── Render search results ─────────────────────────── */
function renderSearch(query, data) {
  showOnly(results);

  // Meta row
  const cached = data.cached
    ? `<span class="meta-cached">⚡ Cached</span>`
    : '';
  const time = data.fetched_at
    ? ` · ${formatTime(data.fetched_at)}`
    : '';
  resultsMeta.innerHTML = `Results for <span class="meta-query">${escHtml(query)}</span>${cached}${escHtml(time)}`;

  // Result counts
  setText(wwCount,    label(data.woolworths.length));
  setText(colesCount, label(data.coles.length));
  setText(aldiCount,  label(data.aldi.length));

  // Best unit price banner
  const best = data.best_unit_price_product;
  if (best?.unit_price_display) {
    bestBanner.classList.remove('hidden');
    setText(bestName, best.name);
    setText(bestStore, storeName(best.store));
    setText(bestUnit, best.unit_price_display);
  } else {
    bestBanner.classList.add('hidden');
  }

  // Suggestions
  renderSuggestions(query, data);

  // Product lists
  renderList(wwList,    data.woolworths, best);
  renderList(colesList, data.coles,      best);
  renderList(aldiList,  data.aldi,       best);
}

/* ─── Render one store's product list ───────────────── */
function renderList(container, products, bestProduct) {
  container.innerHTML = '';
  if (!products.length) {
    const p = document.createElement('p');
    p.className = 'no-results';
    p.textContent = 'No results found at this store.';
    container.appendChild(p);
    return;
  }
  products.forEach(p => {
    const isBest = Boolean(
      bestProduct &&
      p.store === bestProduct.store &&
      p.price === bestProduct.price &&
      p.name  === bestProduct.name
    );
    container.appendChild(buildCard(p, isBest));
  });
}

/* ─── Build product card ────────────────────────────── */
function buildCard(product, isBest) {
  const card = document.createElement('div');
  const classes = ['product-card'];
  if (isBest)          classes.push('best-unit-card');
  if (product.on_sale) classes.push('on-sale-card');
  card.className = classes.join(' ');

  // ── Image ──
  if (product.image_url) {
    const img = document.createElement('img');
    img.className = 'product-img';
    img.src = product.image_url;
    img.alt = '';
    img.loading = 'lazy';
    card.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.className = 'product-img-placeholder';
    ph.setAttribute('aria-hidden', 'true');
    ph.textContent = '🛒';
    card.appendChild(ph);
  }

  // ── Info ──
  const info = document.createElement('div');
  info.className = 'product-info';

  // Name
  const name = document.createElement('div');
  name.className = 'product-name';
  setText(name, product.name);
  info.appendChild(name);

  // Badges
  if (product.on_sale || isBest) {
    const badges = document.createElement('div');
    badges.className = 'product-badges';
    if (product.on_sale) {
      const b = document.createElement('span');
      b.className = 'badge badge-sale';
      b.textContent = 'Sale';
      badges.appendChild(b);
    }
    if (isBest) {
      const b = document.createElement('span');
      b.className = 'badge badge-best';
      b.textContent = 'Best Value';
      badges.appendChild(b);
    }
    info.appendChild(badges);
  }

  // Price row
  const priceRow = document.createElement('div');
  priceRow.className = 'product-price-row';

  const price = document.createElement('span');
  price.className = 'product-price';
  setText(price, product.display_price);
  priceRow.appendChild(price);

  if (product.on_sale && product.was_price) {
    const was = document.createElement('span');
    was.className = 'product-was';
    setText(was, `$${product.was_price.toFixed(2)}`);
    priceRow.appendChild(was);
  }
  info.appendChild(priceRow);

  // Unit price
  if (product.unit_price_display) {
    const unit = document.createElement('div');
    unit.className = 'product-unit';
    setText(unit, product.unit_price_display);
    info.appendChild(unit);
  }

  // View link
  const link = document.createElement('a');
  link.className = 'product-link';
  link.href = product.product_url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.innerHTML = 'View product <svg width="10" height="10" viewBox="0 0 20 20" fill="none"><path d="M4 10h12M11 5l5 5-5 5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  info.appendChild(link);

  card.appendChild(info);
  return card;
}

/* ─── Suggestion section ────────────────────────────── */
function renderSuggestions(query, data) {
  suggSection.innerHTML = '';
  suggSection.classList.add('hidden');

  const suggestions = data.suggestions || [];
  const total = (data.woolworths?.length || 0) + (data.coles?.length || 0) + (data.aldi?.length || 0);

  if (total > 0 && !suggestions.length) return;

  suggSection.classList.remove('hidden');

  const msg = document.createElement('p');
  if (total === 0) {
    msg.textContent = suggestions.length
      ? `No results for "${query}". Did you mean:`
      : `No results for "${query}". Check your spelling and try again.`;
  } else {
    msg.textContent = 'Related searches:';
  }
  suggSection.appendChild(msg);

  const chipsWrap = document.createElement('div');
  chipsWrap.className = 'suggestion-chips';

  suggestions.forEach(term => {
    const chip = document.createElement('button');
    chip.className = 'sug-chip';
    setText(chip, term);
    chip.addEventListener('click', () => {
      searchInput.value = term;
      doSearch(term);
    });
    chipsWrap.appendChild(chip);
  });

  suggSection.appendChild(chipsWrap);
}

/* ─── Shopping list / basket ────────────────────────── */
basketAddBtn.addEventListener('click', addBasketItem);
basketInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); addBasketItem(); }
});
basketCompareBtn.addEventListener('click', doBasketCompare);

function addBasketItem() {
  const val = basketInput.value.trim();
  if (!val || basketItems.includes(val)) { basketInput.value = ''; return; }
  basketItems.push(val);
  basketInput.value = '';
  renderBasketChips();
}

function removeBasketItem(term) {
  basketItems = basketItems.filter(i => i !== term);
  renderBasketChips();
}

function renderBasketChips() {
  basketChips.innerHTML = '';
  basketItems.forEach(term => {
    const chip = document.createElement('span');
    chip.className = 'basket-chip';

    const lbl = document.createElement('span');
    setText(lbl, term);

    const rm = document.createElement('button');
    rm.className = 'basket-chip-remove';
    rm.textContent = '×';
    rm.setAttribute('aria-label', `Remove ${term}`);
    rm.addEventListener('click', () => removeBasketItem(term));

    chip.appendChild(lbl);
    chip.appendChild(rm);
    basketChips.appendChild(chip);
  });

  basketCompareBtn.classList.toggle('hidden', basketItems.length === 0);
}

async function doBasketCompare() {
  if (!basketItems.length || pending) return;
  pending = true;
  basketCompareBtn.disabled = true;
  setText(loadingMsg, `Comparing ${basketItems.length} item${basketItems.length !== 1 ? 's' : ''} across all three stores…`);
  showOnly(loadingState);

  try {
    const res = await fetch('/api/basket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(basketItems),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    const data = await res.json();
    renderBasket(data);
  } catch (err) {
    setText(errorMsg, err.message || 'Something went wrong. Please try again.');
    showOnly(errorState);
  } finally {
    pending = false;
    basketCompareBtn.disabled = false;
  }
}

/* ─── Render basket results ─────────────────────────── */
function renderBasket(data) {
  showOnly(basketResults);
  basketSummary.innerHTML = '';
  basketBreakdown.innerHTML = '';

  const storeKeys = ['woolworths', 'coles', 'aldi'];
  const storeData = data.basket;
  const cheapest  = data.cheapest_store;

  // Compute min total for savings message
  const completeTotals = storeKeys
    .filter(s => !storeData[s].missing.length)
    .map(s => storeData[s].total);
  const minTotal = completeTotals.length ? Math.min(...completeTotals) : null;

  // ── Summary cards ──
  storeKeys.forEach(store => {
    const d = storeData[store];
    const isWinner = store === cheapest;

    const card = document.createElement('div');
    card.className = 'basket-store-card' + (isWinner ? ' winner' : '');

    const sName = document.createElement('div');
    sName.className = 'basket-store-name';
    setText(sName, storeName(store));
    card.appendChild(sName);

    const total = document.createElement('div');
    total.className = 'basket-total';
    setText(total, `$${d.total.toFixed(2)}`);
    card.appendChild(total);

    const itemCount = document.createElement('div');
    itemCount.className = 'basket-items-count';
    setText(itemCount, `${d.items.length} item${d.items.length !== 1 ? 's' : ''} found`);
    card.appendChild(itemCount);

    if (d.missing.length) {
      const miss = document.createElement('div');
      miss.className = 'basket-missing';
      setText(miss, `${d.missing.length} item${d.missing.length !== 1 ? 's' : ''} not found`);
      card.appendChild(miss);
    }

    basketSummary.appendChild(card);
  });

  // ── Item breakdown table ──
  const h3 = document.createElement('h3');
  setText(h3, 'Item breakdown');
  basketBreakdown.appendChild(h3);

  const table = document.createElement('table');
  table.className = 'basket-table';

  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  ['Item', 'Woolworths', 'Coles', 'Aldi'].forEach(text => {
    const th = document.createElement('th');
    setText(th, text);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');

  // Collect all unique item queries
  const allItems = new Set([
    ...storeData.woolworths.items.map(i => i.query),
    ...storeData.coles.items.map(i => i.query),
    ...storeData.aldi.items.map(i => i.query),
    ...storeData.woolworths.missing,
    ...storeData.coles.missing,
    ...storeData.aldi.missing,
  ]);

  allItems.forEach(query => {
    const tr = document.createElement('tr');

    const nameTd = document.createElement('td');
    nameTd.className = 'item-name';
    setText(nameTd, query);
    tr.appendChild(nameTd);

    const prices = {};
    storeKeys.forEach(store => {
      const match = storeData[store].items.find(i => i.query === query);
      prices[store] = match ? { price: match.price, onSale: match.on_sale } : null;
    });

    const validPrices = storeKeys
      .map(s => prices[s]?.price)
      .filter(p => p !== null && p !== undefined);
    const minP = validPrices.length ? Math.min(...validPrices) : null;

    storeKeys.forEach(store => {
      const td = document.createElement('td');
      const p = prices[store];
      if (!p) {
        td.className = 'item-missing';
        td.textContent = 'N/A';
      } else {
        td.className = 'item-price' + (p.price === minP ? ' cheapest' : '');
        td.textContent = `$${p.price.toFixed(2)}`;
        if (p.onSale) {
          const saleEl = document.createElement('div');
          saleEl.className = 'item-on-sale';
          saleEl.textContent = 'On sale';
          td.appendChild(saleEl);
        }
      }
      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  basketBreakdown.appendChild(table);
}

/* ─── Utilities ─────────────────────────────────────── */
function showOnly(panel) {
  [loadingState, errorState, results, basketResults]
    .forEach(el => el.classList.add('hidden'));
  if (panel) panel.classList.remove('hidden');
}

function setText(el, text) { el.textContent = text; }

function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function label(n) { return `${n} result${n !== 1 ? 's' : ''}`; }

function storeName(store) {
  return { woolworths: 'Woolworths', coles: 'Coles', aldi: 'Aldi' }[store] || store;
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}
