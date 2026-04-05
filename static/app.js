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
const basketActions    = document.getElementById('basket-actions');
const basketCompareBtn = document.getElementById('basket-compare-btn');
const storePills       = document.getElementById('store-pills');
const modeSplit        = document.getElementById('mode-split');
const modeSingle       = document.getElementById('mode-single');

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
const storesGrid       = document.getElementById('stores-grid');

const savingsHero      = document.getElementById('savings-hero');
const strategySection  = document.getElementById('strategy-section');
const basketBreakdown  = document.getElementById('basket-breakdown');

/* ─── State ─────────────────────────────────────────── */
let pending        = false;
let lastQuery      = '';
let basketItems    = [];
let selectedStores = [];   // populated after /api/stores loads
let allStores      = [];   // full registry from server
let activeMode     = 'split';  // 'split' | 'single'
let lastBasketData = null;     // cached for mode switching

/* ─── Store registry ────────────────────────────────── */
(async () => {
  try {
    const res = await fetch('/api/stores');
    allStores = await res.json();
    selectedStores = allStores.map(s => s.key);  // default: all selected
    renderStorePills();
  } catch {
    storePills.innerHTML = '<span class="store-pill-error">Failed to load stores</span>';
  }
})();

function renderStorePills() {
  storePills.innerHTML = '';
  allStores.forEach(store => {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'store-pill' + (selectedStores.includes(store.key) ? ' active' : '');
    pill.dataset.key = store.key;
    pill.textContent = store.label;
    if (selectedStores.includes(store.key)) {
      pill.style.setProperty('--pill-color', store.color);
    }
    pill.addEventListener('click', () => toggleStore(store.key, store.color, pill));
    storePills.appendChild(pill);
  });
}

function toggleStore(key, color, pill) {
  if (selectedStores.includes(key)) {
    if (selectedStores.length === 1) return;  // must keep at least one
    selectedStores = selectedStores.filter(s => s !== key);
    pill.classList.remove('active');
    pill.style.removeProperty('--pill-color');
  } else {
    selectedStores.push(key);
    pill.classList.add('active');
    pill.style.setProperty('--pill-color', store => store);
    // Find color from allStores
    const meta = allStores.find(s => s.key === key);
    if (meta) pill.style.setProperty('--pill-color', meta.color);
  }
}

/* ─── Mode selector ─────────────────────────────────── */
[modeSplit, modeSingle].forEach(btn => {
  btn.addEventListener('click', () => {
    activeMode = btn.dataset.mode;
    modeSplit.classList.toggle('active', activeMode === 'split');
    modeSingle.classList.toggle('active', activeMode === 'single');
    // Re-render results if we already have data
    if (lastBasketData) renderBasket(lastBasketData);
  });
});

/* ─── Tab switching ─────────────────────────────────── */
[tabSearch, tabBasket].forEach(tab => {
  tab.addEventListener('click', () => {
    const isSearch = tab.dataset.tab === 'search';
    tabSearch.classList.toggle('active', isSearch);
    tabBasket.classList.toggle('active', !isSearch);
    tabSearch.setAttribute('aria-selected', isSearch);
    tabBasket.setAttribute('aria-selected', !isSearch);

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
  setText(loadingMsg, 'Searching stores simultaneously…');
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
  const cached = data.cached ? `<span class="meta-cached">⚡ Cached</span>` : '';
  const time = data.fetched_at ? ` · ${formatTime(data.fetched_at)}` : '';
  resultsMeta.innerHTML = `Results for <span class="meta-query">${escHtml(query)}</span>${cached}${escHtml(time)}`;

  // Best unit price banner
  const best = data.best_unit_price_product;
  if (best?.unit_price_display) {
    bestBanner.classList.remove('hidden');
    setText(bestName, best.name);
    setText(bestStore, getStoreLabel(best.store));
    setText(bestUnit, best.unit_price_display);
  } else {
    bestBanner.classList.add('hidden');
  }

  // Suggestions
  renderSuggestions(query, data);

  // Dynamic store columns (only search tab uses WW/Coles/Aldi fixed)
  storesGrid.innerHTML = '';
  const storeMap = [
    { key: 'woolworths', label: 'Woolworths', badge: 'W', cls: 'ww' },
    { key: 'coles',      label: 'Coles',      badge: 'C', cls: 'co' },
    { key: 'aldi',       label: 'Aldi',        badge: 'A', cls: 'al' },
  ];
  storeMap.forEach(({ key, label: storeLbl, badge, cls }) => {
    const products = data[key] || [];
    storesGrid.appendChild(buildStoreCol(key, storeLbl, badge, cls, products, best));
  });
}

function buildStoreCol(key, storeLbl, badge, cls, products, bestProduct) {
  const section = document.createElement('section');
  section.className = 'store-col';
  section.id = `${key}-col`;

  const header = document.createElement('div');
  header.className = `store-header ${cls}-header`;
  header.innerHTML = `
    <div class="store-badge-wrap">
      <div class="store-badge ${cls}-badge">${escHtml(badge)}</div>
      <div>
        <div class="store-name">${escHtml(storeLbl)}</div>
        <div class="result-count">${label(products.length)}</div>
      </div>
    </div>`;

  const list = document.createElement('div');
  list.className = 'product-list';

  if (!products.length) {
    const p = document.createElement('p');
    p.className = 'no-results';
    p.textContent = 'No results found at this store.';
    list.appendChild(p);
  } else {
    products.forEach(p => {
      const isBest = Boolean(
        bestProduct && p.store === bestProduct.store &&
        p.price === bestProduct.price && p.name === bestProduct.name
      );
      list.appendChild(buildCard(p, isBest));
    });
  }

  section.appendChild(header);
  section.appendChild(list);
  return section;
}

/* ─── Build product card ────────────────────────────── */
function buildCard(product, isBest) {
  const card = document.createElement('div');
  const classes = ['product-card'];
  if (isBest)          classes.push('best-unit-card');
  if (product.on_sale) classes.push('on-sale-card');
  card.className = classes.join(' ');

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

  const info = document.createElement('div');
  info.className = 'product-info';

  const name = document.createElement('div');
  name.className = 'product-name';
  setText(name, product.name);
  info.appendChild(name);

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

  if (product.unit_price_display) {
    const unit = document.createElement('div');
    unit.className = 'product-unit';
    setText(unit, product.unit_price_display);
    info.appendChild(unit);
  }

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

  basketActions.classList.toggle('hidden', basketItems.length === 0);
}

async function doBasketCompare() {
  if (!basketItems.length || pending) return;
  if (!selectedStores.length) {
    alert('Please select at least one store.');
    return;
  }
  pending = true;
  basketCompareBtn.disabled = true;
  setText(loadingMsg, `Searching ${selectedStores.length} store${selectedStores.length !== 1 ? 's' : ''} for ${basketItems.length} item${basketItems.length !== 1 ? 's' : ''}…`);
  showOnly(loadingState);

  try {
    const res = await fetch('/api/basket', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: basketItems, stores: selectedStores }),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    const data = await res.json();
    lastBasketData = data;
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
  savingsHero.innerHTML = '';
  strategySection.innerHTML = '';
  basketBreakdown.innerHTML = '';

  const { basket, cheapest_store, selected_stores, optimal_split, savings_summary } = data;

  // ── Savings hero banner ──
  if (savings_summary.total_saving > 0) {
    savingsHero.classList.remove('hidden');
    savingsHero.innerHTML = `
      <div class="savings-hero-inner">
        <div class="savings-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
        </div>
        <div class="savings-text">
          <span class="savings-label">You could save</span>
          <span class="savings-amount">$${savings_summary.total_saving.toFixed(2)}</span>
          <span class="savings-pct">(${savings_summary.saving_pct}% cheaper than buying everything at the costliest store)</span>
        </div>
      </div>`;
  } else {
    savingsHero.classList.add('hidden');
  }

  // ── Strategy content based on active mode ──
  if (activeMode === 'split') {
    renderSplitMode(data);
  } else {
    renderSingleMode(data);
  }

  // ── Full price comparison table ──
  renderBreakdownTable(basket, selected_stores, savings_summary);
}

/* ─── Split shopping mode ───────────────────────────── */
function renderSplitMode(data) {
  const { optimal_split, savings_summary, cheapest_store, basket, selected_stores } = data;

  // Header card
  const header = document.createElement('div');
  header.className = 'strategy-header';

  const titleDiv = document.createElement('div');
  titleDiv.className = 'strategy-title-block';

  const title = document.createElement('h2');
  title.className = 'strategy-title';
  title.textContent = 'Best Split Shopping Plan';
  titleDiv.appendChild(title);

  if (optimal_split.split_total > 0) {
    const sub = document.createElement('p');
    sub.className = 'strategy-subtitle';
    const parts = [`Total: $${optimal_split.split_total.toFixed(2)}`];
    if (optimal_split.savings_vs_single > 0) {
      parts.push(`saves $${optimal_split.savings_vs_single.toFixed(2)} vs single store`);
    }
    sub.textContent = parts.join(' — ');
    titleDiv.appendChild(sub);
  }

  header.appendChild(titleDiv);

  // Single-store comparison hint
  if (cheapest_store) {
    const hint = document.createElement('div');
    hint.className = 'strategy-hint';
    const singleTotal = basket[cheapest_store].total;
    hint.innerHTML = `<span>Single store alternative: <strong>${getStoreLabel(cheapest_store)} $${singleTotal.toFixed(2)}</strong></span>`;
    header.appendChild(hint);
  }

  strategySection.appendChild(header);

  // Store groups
  if (Object.keys(optimal_split.by_store).length === 0) {
    const empty = document.createElement('p');
    empty.className = 'strategy-empty';
    empty.textContent = 'No products found at the selected stores for any item.';
    strategySection.appendChild(empty);
    return;
  }

  const groups = document.createElement('div');
  groups.className = 'split-groups';

  selected_stores.forEach(storeKey => {
    const storeData = optimal_split.by_store[storeKey];
    if (!storeData || storeData.items.length === 0) return;

    const meta = allStores.find(s => s.key === storeKey);
    const color = meta?.color || '#888';

    const group = document.createElement('div');
    group.className = 'split-group';
    group.style.setProperty('--store-color', color);

    const groupHeader = document.createElement('div');
    groupHeader.className = 'split-group-header';

    const storeName = document.createElement('div');
    storeName.className = 'split-store-name';
    storeName.textContent = storeData.label;

    const itemCount = document.createElement('div');
    itemCount.className = 'split-item-count';
    itemCount.textContent = `${storeData.items.length} item${storeData.items.length !== 1 ? 's' : ''}`;

    const subtotal = document.createElement('div');
    subtotal.className = 'split-subtotal';
    subtotal.textContent = `$${storeData.subtotal.toFixed(2)}`;

    groupHeader.appendChild(storeName);
    groupHeader.appendChild(itemCount);
    groupHeader.appendChild(subtotal);
    group.appendChild(groupHeader);

    const itemList = document.createElement('ul');
    itemList.className = 'split-item-list';

    storeData.items.forEach(item => {
      const li = document.createElement('li');
      li.className = 'split-item';

      const itemName = document.createElement('span');
      itemName.className = 'split-item-name';

      // Show item name as link if product_url available
      if (item.product_url) {
        const a = document.createElement('a');
        a.href = item.product_url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        setText(a, item.name);
        itemName.appendChild(a);
      } else {
        setText(itemName, item.name);
      }

      const itemQuery = document.createElement('span');
      itemQuery.className = 'split-item-query';
      setText(itemQuery, `(${item.query})`);

      const itemPrice = document.createElement('span');
      itemPrice.className = 'split-item-price';
      setText(itemPrice, item.display_price);

      if (item.on_sale) {
        const saleBadge = document.createElement('span');
        saleBadge.className = 'badge badge-sale';
        saleBadge.textContent = 'Sale';
        itemPrice.appendChild(saleBadge);
      }

      // Show saving for this item
      const itemSaving = savings_summary.per_item?.[item.query];
      if (itemSaving && itemSaving.saving > 0) {
        const savingEl = document.createElement('span');
        savingEl.className = 'split-item-saving';
        setText(savingEl, `save $${itemSaving.saving.toFixed(2)}`);
        itemPrice.appendChild(savingEl);
      }

      li.appendChild(itemName);
      li.appendChild(itemQuery);
      li.appendChild(itemPrice);
      itemList.appendChild(li);
    });

    group.appendChild(itemList);
    groups.appendChild(group);
  });

  strategySection.appendChild(groups);

  // Unavailable items
  if (optimal_split.unavailable.length > 0) {
    const unavail = document.createElement('div');
    unavail.className = 'unavailable-block';
    const unavailTitle = document.createElement('p');
    unavailTitle.className = 'unavailable-title';
    setText(unavailTitle, `Not found at any selected store:`);
    const unavailList = document.createElement('p');
    unavailList.className = 'unavailable-list';
    setText(unavailList, optimal_split.unavailable.join(', '));
    unavail.appendChild(unavailTitle);
    unavail.appendChild(unavailList);
    strategySection.appendChild(unavail);
  }
}

/* ─── Single store mode ─────────────────────────────── */
function renderSingleMode(data) {
  const { basket, cheapest_store, selected_stores, optimal_split } = data;

  const header = document.createElement('div');
  header.className = 'strategy-header';

  const titleDiv = document.createElement('div');
  titleDiv.className = 'strategy-title-block';

  const title = document.createElement('h2');
  title.className = 'strategy-title';
  title.textContent = 'Best Single Store';
  titleDiv.appendChild(title);

  if (cheapest_store) {
    const sub = document.createElement('p');
    sub.className = 'strategy-subtitle';
    sub.textContent = `Go to ${getStoreLabel(cheapest_store)} for the cheapest complete basket`;
    titleDiv.appendChild(sub);
  } else {
    const sub = document.createElement('p');
    sub.className = 'strategy-subtitle';
    sub.textContent = 'No store has all items — see breakdown below';
    titleDiv.appendChild(sub);
  }

  header.appendChild(titleDiv);

  // Split hint
  if (optimal_split.savings_vs_single > 0) {
    const hint = document.createElement('div');
    hint.className = 'strategy-hint';
    hint.innerHTML = `<span>Split shopping saves <strong>$${optimal_split.savings_vs_single.toFixed(2)} more</strong></span>`;
    header.appendChild(hint);
  }

  strategySection.appendChild(header);

  // Store ranking cards
  const storeCards = document.createElement('div');
  storeCards.className = 'single-store-cards';

  // Sort stores: complete stores first (by price), then incomplete
  const complete = selected_stores.filter(s => basket[s] && !basket[s].missing.length);
  const incomplete = selected_stores.filter(s => !complete.includes(s));
  const sorted = [
    ...complete.sort((a, b) => basket[a].total - basket[b].total),
    ...incomplete,
  ];

  sorted.forEach((storeKey, idx) => {
    const d = basket[storeKey];
    if (!d) return;
    const isWinner = storeKey === cheapest_store;
    const meta = allStores.find(s => s.key === storeKey);
    const color = meta?.color || '#888';

    const card = document.createElement('div');
    card.className = 'single-store-card' + (isWinner ? ' winner' : '');
    card.style.setProperty('--store-color', color);

    const rank = document.createElement('div');
    rank.className = 'single-store-rank';
    rank.textContent = isWinner ? 'CHEAPEST' : `#${idx + 1}`;

    const name = document.createElement('div');
    name.className = 'single-store-name';
    setText(name, meta?.label || storeKey);

    const totalEl = document.createElement('div');
    totalEl.className = 'single-store-total';
    setText(totalEl, `$${d.total.toFixed(2)}`);

    card.appendChild(rank);
    card.appendChild(name);
    card.appendChild(totalEl);

    // Extra cost vs winner
    if (!isWinner && cheapest_store && complete.includes(storeKey)) {
      const extra = d.total - basket[cheapest_store].total;
      const extraEl = document.createElement('div');
      extraEl.className = 'single-store-extra';
      setText(extraEl, `+$${extra.toFixed(2)}`);
      card.appendChild(extraEl);
    }

    if (d.missing.length) {
      const missEl = document.createElement('div');
      missEl.className = 'single-store-missing';
      setText(missEl, `${d.missing.length} item${d.missing.length !== 1 ? 's' : ''} unavailable`);
      card.appendChild(missEl);
    }

    storeCards.appendChild(card);
  });

  strategySection.appendChild(storeCards);
}

/* ─── Full price comparison table ───────────────────── */
function renderBreakdownTable(basket, selected_stores, savings_summary) {
  const h3 = document.createElement('h3');
  setText(h3, 'Full Price Comparison');
  basketBreakdown.appendChild(h3);

  const table = document.createElement('table');
  table.className = 'basket-table';

  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  const headers = ['Item', ...selected_stores.map(s => getStoreLabel(s)), 'Your Saving'];
  headers.forEach(text => {
    const th = document.createElement('th');
    setText(th, text);
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');

  // Collect all unique item queries
  const allItems = new Set();
  selected_stores.forEach(s => {
    if (basket[s]) {
      basket[s].items.forEach(i => allItems.add(i.query));
      basket[s].missing.forEach(m => allItems.add(m));
    }
  });

  allItems.forEach(query => {
    const tr = document.createElement('tr');

    const nameTd = document.createElement('td');
    nameTd.className = 'item-name';
    setText(nameTd, query);
    tr.appendChild(nameTd);

    const prices = {};
    selected_stores.forEach(store => {
      if (!basket[store]) return;
      const match = basket[store].items.find(i => i.query === query);
      prices[store] = match ? { price: match.price, onSale: match.on_sale } : null;
    });

    const validPrices = selected_stores
      .map(s => prices[s]?.price)
      .filter(p => p !== null && p !== undefined);
    const minP = validPrices.length ? Math.min(...validPrices) : null;

    selected_stores.forEach(store => {
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

    // Saving column
    const savingTd = document.createElement('td');
    const itemSaving = savings_summary.per_item?.[query];
    if (itemSaving && itemSaving.saving > 0) {
      savingTd.className = 'item-saving';
      setText(savingTd, `$${itemSaving.saving.toFixed(2)}`);
    } else {
      savingTd.className = 'item-no-saving';
      savingTd.textContent = '—';
    }
    tr.appendChild(savingTd);

    tbody.appendChild(tr);
  });

  // Totals row
  const totalsRow = document.createElement('tr');
  totalsRow.className = 'totals-row';
  const totalLabel = document.createElement('td');
  totalLabel.className = 'totals-label';
  totalLabel.textContent = 'Total';
  totalsRow.appendChild(totalLabel);

  selected_stores.forEach(store => {
    const td = document.createElement('td');
    td.className = 'totals-cell';
    if (basket[store]) {
      setText(td, `$${basket[store].total.toFixed(2)}`);
    } else {
      td.textContent = '—';
    }
    totalsRow.appendChild(td);
  });

  // Total saving cell
  const totalSavingTd = document.createElement('td');
  totalSavingTd.className = 'item-saving totals-cell';
  setText(totalSavingTd, `$${savings_summary.total_saving.toFixed(2)}`);
  totalsRow.appendChild(totalSavingTd);

  tbody.appendChild(totalsRow);
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

function getStoreLabel(key) {
  const meta = allStores.find(s => s.key === key);
  return meta?.label || key;
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}
