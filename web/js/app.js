'use strict';

const state = {
  bootstrap: null,
  facets: { tags: [], sources: [] },
  works: { items: [], total: 0, page: 1, page_size: 24 },
  filters: { q: '', type: '', tag: '', sort: 'rating', min_rating: '' },
  chat: { sessions: [], current: null, messages: [], citations: [], streaming: false, work: null, coverage: null, precision: null },
  searchReturn: null,
  pendingSearchRestore: null,
  discoverReturn: null,
  pendingDiscoverRestore: null,
  jobsTimer: null,
};

let previousRoute = null;
let currentRoute = null;

const appEl = document.getElementById('app');
const modalRoot = document.getElementById('modal-root');
const toastRoot = document.getElementById('toast-root');

const TYPE_LABELS = { anime: '番剧', galgame: 'Galgame' };
const SOURCE_LABELS = { bangumi: 'Bangumi', demo: '演示' };
const DEFAULT_WEIGHTS = { relevance: 10, story: 5, characters: 5, atmosphere: 5, gameplay: 5, accessibility: 5, confidence: 3 };

const SEARCH_PRESETS = {
  fast: { label: '极速', screen_summary_chars: 0, screen_batch_works: 20, screen_batch_chars: 16000, screen_passes: 1, shortlist_works: 8, refine_reviews_per_work: 0, detail_works: 4, detail_reviews_per_work: 5, detail_review_chars: 240, max_context_chars: 16000, weights: Object.assign({}, DEFAULT_WEIGHTS) },
  balanced: { label: '均衡', screen_summary_chars: 80, screen_batch_works: 20, screen_batch_chars: 16000, screen_passes: 1, shortlist_works: 12, refine_reviews_per_work: 5, detail_works: 8, detail_reviews_per_work: 10, detail_review_chars: 360, max_context_chars: 30000, weights: Object.assign({}, DEFAULT_WEIGHTS) },
  fine: { label: '精细', screen_summary_chars: 160, screen_batch_works: 20, screen_batch_chars: 16000, screen_passes: 2, shortlist_works: 24, refine_reviews_per_work: 10, detail_works: 12, detail_reviews_per_work: 20, detail_review_chars: 600, max_context_chars: 60000, weights: Object.assign({}, DEFAULT_WEIGHTS) },
  max: { label: '极致', screen_summary_chars: 300, screen_batch_works: 20, screen_batch_chars: 24000, screen_passes: 3, shortlist_works: 36, refine_reviews_per_work: 15, detail_works: 20, detail_reviews_per_work: 30, detail_review_chars: 1200, max_context_chars: 120000, weights: Object.assign({}, DEFAULT_WEIGHTS) },
};

const PRECISION_KEYS = ['screen_summary_chars', 'screen_batch_works', 'screen_batch_chars', 'screen_passes', 'shortlist_works', 'refine_reviews_per_work', 'detail_works', 'detail_reviews_per_work', 'detail_review_chars', 'max_context_chars'];
const WEIGHT_KEYS = ['relevance', 'story', 'characters', 'atmosphere', 'gameplay', 'accessibility', 'confidence'];

function defaultPrecision() { return JSON.parse(JSON.stringify(SEARCH_PRESETS.balanced)); }

function loadPrecision() {
  try {
    const raw = JSON.parse(window.localStorage.getItem('acgSearchPrecision') || 'null');
    if (raw && typeof raw === 'object') {
      const value = defaultPrecision();
      PRECISION_KEYS.forEach(function (key) { if (raw[key] !== undefined) value[key] = Number(raw[key]); });
      if (raw.weights && typeof raw.weights === 'object') {
        WEIGHT_KEYS.forEach(function (key) { if (raw.weights[key] !== undefined) value.weights[key] = Number(raw.weights[key]); });
      }
      return value;
    }
  } catch (err) { /* ignore */ }
  return defaultPrecision();
}

function savePrecision(precision) {
  try { window.localStorage.setItem('acgSearchPrecision', JSON.stringify(precision)); } catch (err) { /* ignore */ }
}

function precisionName(precision) {
  for (const key of Object.keys(SEARCH_PRESETS)) {
    const preset = SEARCH_PRESETS[key];
    if (PRECISION_KEYS.every(function (name) { return Number(precision[name]) === Number(preset[name]); })
      && WEIGHT_KEYS.every(function (name) { return Number(precision.weights && precision.weights[name]) === Number(preset.weights[name]); })) return preset.label;
  }
  return '自定义';
}

const PRESETS = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  moonshot: { base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  siliconflow: { base_url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct' },
  ollama: { base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b' },
  custom: { base_url: '', model: '' },
};

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  const options = attrs || {};
  Object.keys(options).forEach(function (key) {
    const value = options[key];
    if (value === undefined || value === null || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.slice(0, 2) === 'on' && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'checked' || key === 'disabled' || key === 'selected') node[key] = Boolean(value);
    else node.setAttribute(key, value);
  });
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  (children || []).forEach(function (child) {
    if (child === null || child === undefined) return;
    if (Array.isArray(child)) appendChildren(node, child);
    else if (child instanceof Node) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  });
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function text(value) { return document.createTextNode(value == null ? '' : String(value)); }

function toast(message, type) {
  const item = h('div', { class: 'toast ' + (type || '') }, message);
  toastRoot.appendChild(item);
  window.setTimeout(function () {
    item.style.opacity = '0';
    item.style.transform = 'translateY(6px)';
    item.style.transition = 'opacity .2s ease, transform .2s ease';
    window.setTimeout(function () { item.remove(); }, 220);
  }, type === 'bad' ? 5200 : 3400);
}

function apiErrorText(err) {
  if (!err) return '未知错误';
  return err.message || String(err);
}

async function api(path, options) {
  const opts = Object.assign({ method: 'GET' }, options || {});
  const headers = Object.assign({}, opts.headers || {});
  if (opts.body && typeof opts.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  if (opts.method !== 'GET' && state.bootstrap && state.bootstrap.csrf_token) {
    headers['X-CSRF-Token'] = state.bootstrap.csrf_token;
  }
  opts.headers = headers;
  const response = await fetch(path, opts);
  const contentType = response.headers.get('Content-Type') || '';
  let data = null;
  if (contentType.indexOf('application/json') >= 0) {
    try { data = await response.json(); } catch (e) { data = null; }
  } else {
    data = { error: { message: await response.text() } };
  }
  if (!response.ok) {
    const problem = (data && data.error) || {};
    const err = new Error(problem.message || ('请求失败（HTTP ' + response.status + '）'));
    err.code = problem.code || 'HTTP_' + response.status;
    err.status = response.status;
    throw err;
  }
  return data;
}

function sourceLabel(source) { return SOURCE_LABELS[source] || source || '未知'; }
function typeLabel(type) { return TYPE_LABELS[type] || type || ''; }

function chatCoverageLabel(coverage) {
  if (!coverage) return '';
  if (coverage.mode === 'work') {
    return '作品：评论摘要 + ' + String(coverage.reviews || 0) + ' 条评论' + (coverage.truncated ? '（受上下文限制已截断）' : '');
  }
  const total = coverage.works_total || coverage.works || 0;
  const gals = coverage.galgame_total ? '（' + coverage.galgame_total + ' 部 Galgame）' : '';
  const batch = coverage.batches > 1 ? '，分 ' + coverage.batches + ' 批评分' : '';
  const tokens = coverage.estimated_tokens ? ' · 约 ' + Math.round(coverage.estimated_tokens / 1000) + 'k token' : '';
  return '全库 ' + total + ' 部作品' + gals + '，完整目录' + batch + ' + ' + String(coverage.detail_works || 0) + ' 部详情' + (coverage.truncated_details ? '（详情已截断）' : '') + tokens;
}

function sourceBadge(source) {
  return h('span', { class: 'badge' }, sourceLabel(source));
}

function scoreText(score) {
  if (score === null || score === undefined || isNaN(Number(score))) return '暂无评分';
  return Number(score).toFixed(1);
}

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function coverNode(work, className) {
  const wrap = h('div', { class: 'cover ' + (className || '') });
  if (work && work.cover_url) {
    const img = h('img', {
      src: work.cover_url,
      alt: work.title + ' 封面',
      loading: 'lazy',
      referrerpolicy: 'no-referrer',
    });
    img.addEventListener('error', function () {
      img.remove();
      wrap.appendChild(coverFallback(work));
    });
    wrap.appendChild(img);
  } else {
    wrap.appendChild(coverFallback(work));
  }
  const badges = h('div', { class: 'cover-badges' });
  if (work && work.type) badges.appendChild(h('span', { class: 'badge' }, typeLabel(work.type)));
  if (work && work.year) badges.appendChild(h('span', { class: 'badge' }, String(work.year)));
  if (work && work.rating_score) badges.appendChild(h('span', { class: 'badge accent' }, scoreText(work.rating_score)));
  wrap.appendChild(badges);
  return wrap;
}

function coverFallback(work) {
  const title = (work && work.title) || '?';
  return h('div', { class: 'cover-fallback' },
    h('div', { class: 'glyph', 'aria-hidden': 'true' }, '✦'),
    h('span', null, title)
  );
}

function workCard(work) {
  const card = h('a', { class: 'work-card', href: '#/work/' + work.id });
  card.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    saveDiscoverReturn();
  });
  card.appendChild(coverNode(work));
  const body = h('div', { class: 'work-card-body' });
  body.appendChild(h('div', { class: 'work-card-title' }, work.title));
  const meta = h('div', { class: 'work-card-meta' });
  meta.appendChild(h('span', null, typeLabel(work.type)));
  if (work.year) meta.appendChild(h('span', null, String(work.year)));
  meta.appendChild(h('span', null, (work.review_count || 0) + ' 条评论'));
  body.appendChild(meta);
  const tags = h('div', { class: 'work-card-tags' });
  (work.tags || []).slice(0, 3).forEach(function (tag) { tags.appendChild(h('span', { class: 'mini-tag' }, tag)); });
  body.appendChild(tags);
  card.appendChild(body);
  return card;
}

function emptyState(title, message, buttonText, onClick) {
  const box = h('div', { class: 'empty' },
    h('div', { class: 'empty-icon', 'aria-hidden': 'true' }, '✧'),
    h('h3', null, title),
    h('p', null, message)
  );
  if (buttonText) box.appendChild(h('button', { class: 'btn btn-primary', onclick: onClick }, buttonText));
  return box;
}

function sectionTitle(title, action) {
  return h('div', { class: 'section-head' }, h('h2', null, title), action || null);
}

function setNav(route) {
  document.querySelectorAll('#main-nav a').forEach(function (link) {
    link.classList.toggle('active', link.dataset.route === route);
  });
}

function navigate(path) {
  if (location.hash === '#' + path) renderRoute();
  else location.hash = '#' + path;
}

function closeModal() {
  clear(modalRoot);
  document.body.classList.remove('modal-open');
}

function openModal(options) {
  const opts = options || {};
  clear(modalRoot);
  const backdrop = h('div', { class: 'modal-backdrop' });
  const modal = h('div', { class: 'modal ' + (opts.wide ? 'wide' : '') + (opts.narrow ? 'narrow' : '') });
  const head = h('div', { class: 'modal-head' },
    h('div', null, h('h2', null, opts.title || ''), opts.subtitle ? h('p', { class: 'hint' }, opts.subtitle) : null),
    h('button', { class: 'close-btn', 'aria-label': '关闭', onclick: closeModal }, '✕')
  );
  modal.appendChild(head);
  const body = h('div', { class: 'modal-body' });
  appendChildren(body, [opts.body || null]);
  modal.appendChild(body);
  if (opts.footer) {
    const foot = h('div', { class: 'modal-foot' });
    appendChildren(foot, opts.footer);
    modal.appendChild(foot);
  }
  backdrop.appendChild(modal);
  backdrop.addEventListener('click', function (event) { if (event.target === backdrop) closeModal(); });
  modalRoot.appendChild(backdrop);
  document.body.classList.add('modal-open');
  const focusable = modal.querySelector('input, textarea, select, button');
  if (focusable) window.setTimeout(function () { focusable.focus(); }, 30);
  return { modal: modal, body: body, head: head };
}

async function bootstrap() {
  try {
    const data = await fetch('/api/bootstrap').then(function (r) { return r.json(); });
    state.bootstrap = data;
    document.getElementById('footer-version').textContent = 'v' + data.version;
    document.getElementById('demo-pill').hidden = !data.has_demo;
    if (data.has_demo) {
      document.getElementById('demo-pill').addEventListener('click', function () {
        toast('当前含 6 部带“演示”标识的虚构作品和合成评论，可在设置中查看说明。');
      });
    }
    await loadFacets();
    document.getElementById('add-work-btn').addEventListener('click', openAddModal);
    window.addEventListener('hashchange', function () { renderRoute(); });
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape') closeModal(); });
    await renderRoute();
  } catch (err) {
    clear(appEl);
    appEl.appendChild(h('div', { class: 'page' },
      emptyState('无法连接本地服务', apiErrorText(err), '重试', function () { location.reload(); })
    ));
  }
}

async function loadFacets() {
  try {
    state.facets = await api('/api/facets');
  } catch (err) {
    state.facets = { tags: [], sources: [] };
  }
}

function getSearchReturn() {
  if (state.searchReturn) return state.searchReturn;
  try {
    const raw = window.sessionStorage.getItem('acgSearchReturn');
    if (raw) return JSON.parse(raw);
  } catch (err) { /* ignore */ }
  return null;
}

function saveSearchReturn(snapshot) {
  state.searchReturn = snapshot;
  clearDiscoverReturn();
  try { window.sessionStorage.setItem('acgSearchReturn', JSON.stringify(snapshot)); } catch (err) { /* ignore */ }
}

function clearSearchReturn() {
  state.searchReturn = null;
  try { window.sessionStorage.removeItem('acgSearchReturn'); } catch (err) { /* ignore */ }
}

function getDiscoverReturn() {
  if (state.discoverReturn) return state.discoverReturn;
  try {
    const raw = window.sessionStorage.getItem('acgDiscoverReturn');
    if (raw) return JSON.parse(raw);
  } catch (err) { /* ignore */ }
  return null;
}

function saveDiscoverReturn() {
  const context = {
    filters: Object.assign({}, state.filters),
    works: {
      items: (state.works.items || []).slice(),
      page: state.works.page || 1,
      total: state.works.total || 0,
      page_size: state.works.page_size || 24,
    },
    scrollY: window.scrollY || 0,
  };
  state.discoverReturn = context;
  state.searchReturn = null;
  try {
    window.sessionStorage.removeItem('acgSearchReturn');
    window.sessionStorage.setItem('acgDiscoverReturn', JSON.stringify(context));
  } catch (err) { /* ignore */ }
}

function clearDiscoverReturn() {
  state.discoverReturn = null;
  try { window.sessionStorage.removeItem('acgDiscoverReturn'); } catch (err) { /* ignore */ }
}

function goBack(workId) {
  const restore = getSearchReturn();
  if (restore && String(restore.workId) === String(workId)) {
    clearSearchReturn();
    state.pendingSearchRestore = restore;
    navigate('/discover');
    return;
  }
  const discover = getDiscoverReturn();
  if (discover) {
    clearDiscoverReturn();
    state.pendingDiscoverRestore = discover;
    navigate('/discover');
    return;
  }
  if (previousRoute && previousRoute !== currentRoute) navigate(previousRoute);
  else navigate('/discover');
}

function routeInfo() {
  const raw = (location.hash || '#/discover').replace(/^#/, '') || '/discover';
  const parts = raw.split('?');
  const path = parts[0] || '/discover';
  return { path: path, params: new URLSearchParams(parts[1] || '') };
}

async function renderRoute() {
  const route = routeInfo();
  const routeKey = (location.hash || '#/discover').replace(/^#/, '') || '/discover';
  if (currentRoute && currentRoute !== routeKey) previousRoute = currentRoute;
  currentRoute = routeKey;
  const segments = route.path.split('/').filter(Boolean);
  const root = segments[0] || 'discover';
  setNav(root);
  if (state.jobsTimer) { window.clearTimeout(state.jobsTimer); state.jobsTimer = null; }
  closeModal();
  if (root === 'discover' && !state.pendingSearchRestore) {
    const restore = getSearchReturn();
    if (restore) {
      clearSearchReturn();
      state.pendingSearchRestore = restore;
    }
  }
  const discoverRestore = state.pendingDiscoverRestore;
  state.pendingDiscoverRestore = null;
  try {
    if (root === 'discover') await renderDiscover(discoverRestore);
    else if (root === 'work' && segments[1]) await renderWork(segments[1], route.params);
    else if (root === 'chat') await renderChat(route.params);
    else if (root === 'jobs') await renderJobs();
    else if (root === 'settings') await renderSettings();
    else navigate('/discover');
  } catch (err) {
    clear(appEl);
    appEl.appendChild(h('div', { class: 'page' }, emptyState('页面加载失败', apiErrorText(err), '返回发现', function () { navigate('/discover'); })));
  }
  if (state.pendingSearchRestore) {
    const restore = state.pendingSearchRestore;
    state.pendingSearchRestore = null;
    openAddModal(restore);
  }
}

async function renderDiscover(restore) {
  if (restore) {
    state.filters = Object.assign({}, restore.filters || {});
    state.works = {
      items: (restore.works && restore.works.items ? restore.works.items : []).slice(),
      page: (restore.works && restore.works.page) || 1,
      total: (restore.works && restore.works.total) || 0,
      page_size: (restore.works && restore.works.page_size) || 24,
    };
  }
  clear(appEl);
  const page = h('div', { class: 'page' });
  const counts = (state.bootstrap && state.bootstrap.counts) || {};
  const hero = h('section', { class: 'hero' },
    h('div', { class: 'eyebrow' }, 'REVIEW-FIRST RECOMMENDATION'),
    h('h1', null, '先听评论，再决定看什么。'),
    h('p', { class: 'page-sub' }, '把 Bangumi 的口碑收进自己的资料库，再用你的 AI API 做有出处的分析。'),
    h('div', { class: 'hero-actions' },
      h('button', { class: 'btn btn-primary', onclick: openAddModal }, '＋ 添加作品'),
      h('a', { class: 'btn btn-ghost', href: '#/chat' }, '开始对话')
    ),
    h('div', { class: 'hero-stats' },
      h('div', { class: 'hero-stat' }, h('strong', null, String(counts.works || 0)), h('span', null, '作品')),
      h('div', { class: 'hero-stat' }, h('strong', null, String(counts.reviews || 0)), h('span', null, '评论')),
      h('div', { class: 'hero-stat' }, h('strong', null, String((state.facets.tags || []).length)), h('span', null, '标签'))
    )
  );
  page.appendChild(hero);
  if (state.bootstrap && state.bootstrap.has_demo) {
    page.appendChild(h('div', { class: 'demo-banner' }, '当前库内含演示作品与合成评论，仅用于展示流程；可在设置中查看清理方式。'));
  }
  const toolbar = h('div', { class: 'toolbar' });
  const search = h('input', { class: 'input', placeholder: '搜索库内作品…', value: state.filters.q });
  search.addEventListener('keydown', function (event) { if (event.key === 'Enter') { state.filters.q = search.value.trim(); loadWorks(1); } });
  search.addEventListener('change', function () { state.filters.q = search.value.trim(); });
  toolbar.appendChild(search);
  const type = h('select', { class: 'select' }, h('option', { value: '' }, '全部类型'), h('option', { value: 'anime' }, '番剧'), h('option', { value: 'galgame' }, 'Galgame'));
  type.value = state.filters.type;
  type.addEventListener('change', function () { state.filters.type = type.value; loadWorks(1); });
  toolbar.appendChild(type);
  const tag = h('select', { class: 'select' }, h('option', { value: '' }, '全部标签'));
  (state.facets.tags || []).forEach(function (item) {
    tag.appendChild(h('option', { value: item.tag }, item.tag + ' (' + item.count + ')'));
  });
  tag.value = state.filters.tag;
  tag.addEventListener('change', function () { state.filters.tag = tag.value; loadWorks(1); });
  toolbar.appendChild(tag);
  const sort = h('select', { class: 'select' },
    h('option', { value: 'rating' }, '综合评分'), h('option', { value: 'reviews' }, '评论最多'),
    h('option', { value: 'newest' }, '最近更新'), h('option', { value: 'year' }, '年份')
  );
  sort.value = state.filters.sort;
  sort.addEventListener('change', function () { state.filters.sort = sort.value; loadWorks(1); });
  toolbar.appendChild(sort);
  const searchBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: function () { state.filters.q = search.value.trim(); loadWorks(1); } }, '筛选');
  toolbar.appendChild(searchBtn);
  page.appendChild(toolbar);
  const gridWrap = h('div', { id: 'work-grid-wrap' });
  page.appendChild(gridWrap);
  appEl.appendChild(page);
  if (restore) {
    renderWorkGrid(gridWrap);
    window.scrollTo(0, restore.scrollY || 0);
    window.requestAnimationFrame(function () { window.scrollTo(0, restore.scrollY || 0); });
  } else {
    renderWorkGrid(gridWrap);
    loadWorks(1);
  }
}

function renderWorkGrid(container) {
  clear(container);
  if (!state.works.items.length) {
    container.appendChild(emptyState(
      state.bootstrap && state.bootstrap.first_run ? '雷达还没有收到信号' : '没有匹配的作品',
      state.bootstrap && state.bootstrap.first_run ? '先从 Bangumi 添加一部作品，抓取评论后再来。' : '试试换一个关键词、标签或类型。',
      '添加作品', openAddModal
    ));
    return;
  }
  const grid = h('div', { class: 'work-grid' });
  state.works.items.forEach(function (work) { grid.appendChild(workCard(work)); });
  container.appendChild(grid);
  if (state.works.items.length < state.works.total) {
    container.appendChild(pagerNode());
  }
}

function pagerNode() {
  return h('div', { class: 'pager' },
    h('button', { class: 'btn btn-ghost', onclick: function () { loadWorks(state.works.page + 1); } }, '加载更多')
  );
}

function appendWorkCards(container, items) {
  const grid = container.querySelector('.work-grid');
  if (!grid) { renderWorkGrid(container); return; }
  items.forEach(function (work) { grid.appendChild(workCard(work)); });
  const pager = container.querySelector('.pager');
  if (pager) pager.remove();
  if (state.works.items.length < state.works.total) container.appendChild(pagerNode());
}

async function loadWorks(page) {
  const wrap = document.getElementById('work-grid-wrap');
  if (!wrap) return;
  if (page === 1) {
    clear(wrap);
    const skeleton = h('div', { class: 'work-grid' });
    for (let i = 0; i < 5; i += 1) skeleton.appendChild(h('div', { class: 'skeleton skeleton-card' }));
    wrap.appendChild(skeleton);
  }
  const params = new URLSearchParams();
  params.set('page', String(page || 1));
  params.set('page_size', '24');
  if (state.filters.q) params.set('q', state.filters.q);
  if (state.filters.type) params.set('type', state.filters.type);
  if (state.filters.tag) params.set('tag', state.filters.tag);
  if (state.filters.sort) params.set('sort', state.filters.sort);
  try {
    const data = await api('/api/works?' + params.toString());
    if (page > 1) {
      const scrollY = window.scrollY;
      state.works.items = state.works.items.concat(data.items);
      state.works.page = data.page;
      state.works.total = data.total;
      appendWorkCards(wrap, data.items || []);
      window.scrollTo(0, scrollY);
      window.requestAnimationFrame(function () { window.scrollTo(0, scrollY); });
    } else {
      state.works = data;
      renderWorkGrid(wrap);
    }
  } catch (err) {
    clear(wrap);
    wrap.appendChild(emptyState('加载作品失败', apiErrorText(err), '重试', function () { loadWorks(page); }));
  }
}

function openAddModal(restore) {
  const modal = openModal({ title: '添加作品', subtitle: '从公开 API 搜索作品，导入后自动抓取评论。', wide: true });
  const tabs = h('div', { class: 'tabs' });
  const searchTab = h('button', { class: 'tab active' }, '搜索');
  const linkTab = h('button', { class: 'tab' }, '链接 / ID');
  tabs.appendChild(searchTab); tabs.appendChild(linkTab);
  modal.body.appendChild(tabs);
  const r18Toggle = h('input', { type: 'checkbox', checked: Boolean(state.bootstrap.settings.crawler.allow_nsfw) });
  const r18Hint = h('span', { class: 'hint' });
  function updateR18Hint() {
    const authorized = Boolean(state.bootstrap.settings.bangumi && state.bootstrap.settings.bangumi.token_configured);
    r18Hint.textContent = r18Toggle.checked && !authorized
      ? 'R18 需要先在设置中填写 Bangumi Access Token，否则 Bangumi 会隐藏 R18 条目。'
      : '搜索和导入时生效；开启后可能出现成人内容。';
    if (r18Toggle.checked && !authorized) {
      r18Hint.appendChild(document.createTextNode(' '));
      r18Hint.appendChild(h('a', { href: 'https://next.bgm.tv/demo/access-token', target: '_blank', rel: 'noopener noreferrer' }, '申请 Token ↗'));
    }
  }
  r18Toggle.addEventListener('change', updateR18Hint);
  updateR18Hint();
  modal.body.appendChild(h('div', { class: 'r18-row' },
    h('label', { class: 'check' }, r18Toggle, '包含 R18 / NSFW 内容'),
    r18Hint
  ));
  const panel = h('div');
  modal.body.appendChild(panel);
  let mode = 'search';
  let nsfwPromise = null;

  function ensureNsfw() {
    const desired = Boolean(r18Toggle.checked);
    if (Boolean(state.bootstrap.settings.crawler.allow_nsfw) === desired) return Promise.resolve(true);
    if (!nsfwPromise) {
      nsfwPromise = api('/api/settings', { method: 'POST', body: { crawler: { allow_nsfw: desired } } })
        .then(function (data) { state.bootstrap.settings = data; return true; })
        .catch(function (err) {
          toast('保存 R18 开关失败：' + apiErrorText(err), 'bad');
          r18Toggle.checked = Boolean(state.bootstrap.settings.crawler.allow_nsfw);
          return false;
        })
        .finally(function () { nsfwPromise = null; });
    }
    return nsfwPromise;
  }

  function setMode(next) {
    mode = next;
    searchTab.classList.toggle('active', mode === 'search');
    linkTab.classList.toggle('active', mode === 'link');
    clear(panel);
    if (mode === 'search') renderSearchPanel();
    else renderLinkPanel();
  }

  function renderSearchPanel(restore) {
    let allResults = restore ? (restore.allResults || []).slice() : [];
    let filteredResults = [];
    let selectedKeys = new Set(restore ? (restore.selectedKeys || []) : []);
    let importedKeys = new Set(restore ? (restore.importedKeys || []) : []);
    let searchErrors = restore ? (restore.searchErrors || []).slice() : [];
    let importing = false;
    let rowRefs = new Map();
    let currentPage = restore ? (restore.currentPage || 1) : 1;
    let hasMore = restore ? Boolean(restore.hasMore) : false;
    let loadingMore = false;
    let lastParams = restore && restore.lastParams ? new URLSearchParams(restore.lastParams) : null;

    const form = h('div', { class: 'soft-panel', style: 'padding:16px' });
    const query = h('input', { class: 'input', placeholder: '输入 Bangumi 名称；留空则按年份/标签浏览' });
    const type = h('select', { class: 'select' }, h('option', { value: 'all' }, '全部'), h('option', { value: 'anime' }, '番剧'), h('option', { value: 'galgame' }, 'Galgame'));
    type.value = 'all';
    const yearFrom = h('input', { class: 'input', type: 'number', min: '1900', max: '2100', placeholder: '不限' });
    const yearTo = h('input', { class: 'input', type: 'number', min: '1900', max: '2100', placeholder: '不限' });
    const minRating = h('input', { class: 'input', type: 'number', min: '0', max: '10', step: '0.1', placeholder: '如 8.0' });
    const tagFilter = h('input', { class: 'input', list: 'search-tag-list', placeholder: '如：治愈、悬疑（逗号分隔）' });
    const tagMatch = h('select', { class: 'select' }, h('option', { value: 'all' }, '匹配全部'), h('option', { value: 'any' }, '匹配任一'));
    const tagList = h('datalist', { id: 'search-tag-list' });
    (state.facets.tags || []).forEach(function (item) { tagList.appendChild(h('option', { value: item.tag })); });
    const crawlLimit = h('input', { class: 'input', type: 'number', min: '1', max: '500', step: '1', value: '120' });
    if (restore) {
      if (restore.r18 !== undefined) r18Toggle.checked = Boolean(restore.r18);
      query.value = restore.query || '';
      type.value = restore.type || 'all';
      yearFrom.value = restore.yearFrom || '';
      yearTo.value = restore.yearTo || '';
      minRating.value = restore.minRating || '';
      tagFilter.value = restore.tagFilter || '';
      tagMatch.value = restore.tagMatch || 'all';
      crawlLimit.value = restore.crawlLimit || '120';
    }
    const searchBtn = h('button', { class: 'btn btn-primary', onclick: doSearch }, '搜索 / 浏览');
    const resetBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: function () {
      yearFrom.value = '';
      yearTo.value = '';
      minRating.value = '';
      tagFilter.value = '';
      tagMatch.value = 'all';
      applyFilters();
    } }, '重置筛选');
    form.appendChild(h('div', { class: 'row' }, h('div', { class: 'grow' }, query), h('div', { style: 'width:130px' }, type), searchBtn));
    form.appendChild(h('div', { class: 'search-filters' },
      h('div', { class: 'field' }, h('label', null, '年份从'), yearFrom),
      h('div', { class: 'field' }, h('label', null, '年份到'), yearTo),
      h('div', { class: 'field' }, h('label', null, '评分 ≥'), minRating),
      h('div', { class: 'field' }, h('label', null, '标签'), tagFilter),
      h('div', { class: 'field' }, h('label', null, '标签匹配'), tagMatch),
      h('div', { class: 'field' }, h('label', null, '单个作品抓取评论数'), crawlLimit)
    ));
    form.appendChild(h('div', { class: 'row', style: 'margin-top:10px;justify-content:space-between' },
      h('span', { class: 'hint' }, '留空名称可按年份/标签/评分浏览；每个作品按最新优先抓取。'),
      resetBtn
    ));
    panel.appendChild(form);
    panel.appendChild(tagList);
    const resultBox = h('div', { class: 'search-results' });
    panel.appendChild(resultBox);
    query.addEventListener('keydown', function (event) { if (event.key === 'Enter') doSearch(); });
    yearFrom.addEventListener('change', applyFilters);
    yearTo.addEventListener('change', applyFilters);
    minRating.addEventListener('change', applyFilters);
    tagFilter.addEventListener('input', applyFilters);
    tagMatch.addEventListener('change', applyFilters);
    if (restore) {
      filteredResults = allResults.filter(matchesFilters);
      renderResults();
    }

    function crawlLimitValue() {
      return Math.max(1, Math.min(500, parseInt(crawlLimit.value, 10) || 120));
    }

    function keyOf(candidate) { return candidate.source + ':' + candidate.source_id; }
    function normTag(value) { return String(value || '').toLowerCase().replace(/\s+/g, ''); }
    function candidateTags(candidate) {
      return (candidate.tags || []).map(function (tag) { return typeof tag === 'string' ? tag : (tag.name || ''); }).filter(Boolean);
    }

    function matchesFilters(candidate) {
      const from = parseInt(yearFrom.value, 10);
      const to = parseInt(yearTo.value, 10);
      const rating = parseFloat(minRating.value);
      if (from && (!candidate.year || Number(candidate.year) < from)) return false;
      if (to && (!candidate.year || Number(candidate.year) > to)) return false;
      if (!isNaN(rating) && (candidate.rating == null || Number(candidate.rating) < rating)) return false;
      if (query.value.trim()) {
        const wanted = tagFilter.value.split(/[,，、\s]+/).filter(Boolean).map(normTag);
        if (wanted.length) {
          const matchAll = tagMatch.value === 'all';
          const tags = candidateTags(candidate).map(normTag);
          const hit = wanted.filter(function (needle) { return tags.some(function (tag) { return tag.indexOf(needle) >= 0; }); }).length;
          if (matchAll ? hit !== wanted.length : hit === 0) return false;
        }
      }
      return true;
    }

    function applyFilters() {
      if (importing) return;
      filteredResults = allResults.filter(matchesFilters);
      selectedKeys = new Set(filteredResults.filter(function (candidate) { return !importedKeys.has(keyOf(candidate)); }).map(keyOf));
      renderResults();
    }

    async function doSearch() {
      if (importing) return;
      if (!(await ensureNsfw())) return;
      const q = query.value.trim();
      if (!q && tagMatch.value === 'any') tagMatch.value = 'all';
      const hasFilters = Boolean(yearFrom.value || yearTo.value || minRating.value || tagFilter.value.trim());
      if (!q && !hasFilters) { toast('请输入名称，或设置年份/标签/评分后浏览', 'bad'); return; }
      searchBtn.disabled = true;
      searchBtn.textContent = '查询中…';
      clear(resultBox);
      resultBox.appendChild(h('div', { class: 'loading-steps' }, h('span', { class: 'spinner' }), q ? '正在搜索 Bangumi…' : '正在按年份/标签发现作品…'));
      const params = new URLSearchParams({ q: q, type: type.value, limit: '20', page: '1' });
      if (yearFrom.value) params.set('year_from', yearFrom.value);
      if (yearTo.value) params.set('year_to', yearTo.value);
      if (minRating.value) params.set('min_rating', minRating.value);
      if (tagFilter.value.trim()) params.set('tags', tagFilter.value.trim());
      try {
        const data = await api('/api/search?' + params.toString());
        allResults = data.results || [];
        searchErrors = data.errors || [];
        importedKeys = new Set();
        currentPage = data.page || 1;
        hasMore = Boolean(data.has_more);
        loadingMore = false;
        lastParams = new URLSearchParams(params.toString());
        applyFilters();
      } catch (err) {
        allResults = [];
        filteredResults = [];
        searchErrors = [];
        hasMore = false;
        lastParams = null;
        clear(resultBox);
        resultBox.appendChild(h('div', { class: 'banner error' }, apiErrorText(err)));
      } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = '搜索 / 浏览';
      }
    }

    async function loadMore() {
      if (loadingMore || !hasMore || !lastParams || importing) return;
      loadingMore = true;
      const scroller = modal.body;
      const scrollTop = scroller ? scroller.scrollTop : 0;
      const pageScrollY = window.scrollY || 0;
      const loadButton = resultBox.querySelector('.load-more button');
      if (loadButton) { loadButton.disabled = true; loadButton.textContent = '加载中…'; }
      const params = new URLSearchParams(lastParams.toString());
      params.set('page', String(currentPage + 1));
      params.set('limit', '20');
      try {
        const data = await api('/api/search?' + params.toString());
        const seen = new Set(allResults.map(keyOf));
        (data.results || []).forEach(function (candidate) {
          const key = keyOf(candidate);
          if (seen.has(key)) return;
          seen.add(key);
          allResults.push(candidate);
          if (matchesFilters(candidate) && !importedKeys.has(key)) selectedKeys.add(key);
        });
        (data.errors || []).forEach(function (item) {
          if (!searchErrors.some(function (existing) { return existing.source === item.source && existing.message === item.message; })) searchErrors.push(item);
        });
        currentPage = data.page || (currentPage + 1);
        hasMore = Boolean(data.has_more);
        filteredResults = allResults.filter(matchesFilters);
        loadingMore = false;
        renderResults();
        if (scroller) scroller.scrollTop = scrollTop;
        window.scrollTo(0, pageScrollY);
        window.requestAnimationFrame(function () {
          if (scroller) scroller.scrollTop = scrollTop;
          window.scrollTo(0, pageScrollY);
        });
      } catch (err) {
        loadingMore = false;
        renderResults();
        if (scroller) scroller.scrollTop = scrollTop;
        window.scrollTo(0, pageScrollY);
        toast('继续加载失败：' + apiErrorText(err), 'bad');
      }
    }

    function appendLoadMore() {
      if (!hasMore) return;
      const wrap = h('div', { class: 'load-more' });
      wrap.appendChild(h('button', { class: 'btn btn-ghost', disabled: loadingMore, onclick: loadMore },
        loadingMore ? '加载中…' : '继续加载更多（已加载 ' + allResults.length + ' 部）'));
      resultBox.appendChild(wrap);
    }

    function searchSnapshot() {
      return {
        mode: mode,
        r18: r18Toggle.checked,
        modalScrollTop: modal.body ? modal.body.scrollTop : 0,
        pageScrollY: window.scrollY || 0,
        query: query.value,
        type: type.value,
        yearFrom: yearFrom.value,
        yearTo: yearTo.value,
        minRating: minRating.value,
        tagFilter: tagFilter.value,
        tagMatch: tagMatch.value,
        crawlLimit: crawlLimit.value,
        allResults: allResults.slice(),
        selectedKeys: Array.from(selectedKeys),
        importedKeys: Array.from(importedKeys),
        searchErrors: searchErrors.slice(),
        currentPage: currentPage,
        hasMore: hasMore,
        lastParams: lastParams ? lastParams.toString() : null,
      };
    }

    function pendingCandidates() {
      return filteredResults.filter(function (candidate) { return selectedKeys.has(keyOf(candidate)) && !importedKeys.has(keyOf(candidate)); });
    }

    function updateBulkButton(bulkBtn) {
      const pending = pendingCandidates().length;
      bulkBtn.disabled = importing || pending === 0;
      bulkBtn.textContent = importing ? '导入中…' : (pending ? '一键导入选中 (' + pending + ')' : '已全部导入');
    }

    function renderResults() {
      clear(resultBox);
      searchErrors.forEach(function (item) {
        resultBox.appendChild(h('div', { class: 'banner error' }, sourceLabel(item.source) + '：' + item.message));
      });
      if (!allResults.length) {
        resultBox.appendChild(h('div', { class: 'empty', style: 'padding:28px' },
          h('h3', null, '没有找到结果'),
          h('p', null, '可以粘贴 Bangumi 链接，或调整年份/标签后重试。')
        ));
        return;
      }
      const pendingAll = filteredResults.filter(function (candidate) { return !importedKeys.has(keyOf(candidate)); });
      const allBox = h('input', { type: 'checkbox', checked: pendingAll.length > 0 && pendingAll.every(function (candidate) { return selectedKeys.has(keyOf(candidate)); }) });
      allBox.addEventListener('change', function () {
        filteredResults.forEach(function (candidate) {
          const key = keyOf(candidate);
          if (importedKeys.has(key)) return;
          if (allBox.checked) selectedKeys.add(key);
          else selectedKeys.delete(key);
        });
        renderResults();
      });
      const bulkBtn = h('button', { class: 'btn btn-primary btn-sm', onclick: bulkImport }, '');
      updateBulkButton(bulkBtn);
      const progressWrap = h('div', { class: 'import-progress', hidden: true },
        h('div', { class: 'row' }, h('span', { class: 'spinner' }), h('span', { class: 'progress-label' }, '准备导入…')),
        h('div', { class: 'progress' }, h('i', { style: 'width:0%' }))
      );
      const finishBox = h('div', { class: 'banner bulk-finish', hidden: true });
      const bar = h('div', { class: 'bulk-bar' },
        h('div', { class: 'row' },
          h('label', { class: 'check' }, allBox, '全选匹配'),
          h('span', { class: 'muted small' }, '匹配 ' + filteredResults.length + ' 部 / 共 ' + allResults.length + ' 部')
        ),
        h('div', { class: 'row' }, h('span', { class: 'hint' }, '年份和标签可组合筛选'), bulkBtn)
      );
      resultBox.appendChild(bar);
      resultBox.appendChild(progressWrap);
      resultBox.appendChild(finishBox);
      rowRefs = new Map();
      if (!filteredResults.length) {
        resultBox.appendChild(h('div', { class: 'empty', style: 'padding:28px' },
          h('h3', null, '没有符合筛选的结果'),
          h('p', null, '调整年份或标签，或点“重置筛选”。')
        ));
        appendLoadMore();
        return;
      }
      filteredResults.forEach(function (candidate) { resultBox.appendChild(rowFor(candidate, bulkBtn)); });
      appendLoadMore();
    }

    function openCandidateDetail(candidate) {
      const imported = importedKeys.has(keyOf(candidate));
      const cover = h('div', { class: 'result-cover candidate-cover' });
      if (candidate.cover_url) {
        const image = h('img', { src: candidate.cover_url, referrerpolicy: 'no-referrer', alt: candidate.title });
        image.addEventListener('error', function () { image.remove(); cover.appendChild(coverFallback(candidate)); });
        cover.appendChild(image);
      } else {
        cover.appendChild(coverFallback(candidate));
      }
      const tags = candidateTags(candidate);
      const info = h('div', { class: 'candidate-info' },
        h('div', { class: 'eyebrow' }, sourceLabel(candidate.source) + ' · ' + typeLabel(candidate.type) + (candidate.nsfw ? ' · R18' : '')),
        h('h2', { class: 'candidate-title' }, candidate.title),
        h('div', { class: 'candidate-meta' },
          h('span', null, candidate.year ? String(candidate.year) : '年份未知'),
          h('span', null, candidate.rating ? '评分 ' + scoreText(candidate.rating) + (candidate.rating_votes ? '（' + candidate.rating_votes + ' 票）' : '') : '暂无评分')
        ),
        tags.length ? h('div', { class: 'candidate-tags' }, tags.map(function (tag) { return h('span', { class: 'chip' }, tag); })) : null
      );
      const body = h('div', { class: 'candidate-detail' },
        h('div', { class: 'candidate-head' }, cover, info),
        h('h3', { class: 'candidate-section-title' }, '简介'),
        h('p', { class: 'candidate-synopsis' }, candidate.synopsis || '暂无简介。')
      );
      const importBtn = h('button', { class: 'btn btn-primary', disabled: imported }, imported ? '已导入' : '导入并抓取');
      importBtn.addEventListener('click', async function () {
        importBtn.disabled = true;
        importBtn.textContent = '导入中…';
        const result = await importCandidate(candidate, true);
        if (!result) { importBtn.disabled = false; importBtn.textContent = '导入并抓取'; }
      });
      const returnState = searchSnapshot();
      const footer = [h('button', { class: 'btn btn-ghost', onclick: function () {
        closeModal();
        openAddModal(returnState);
      } }, '← 返回搜索')];
      if (candidate.url) footer.push(h('a', { class: 'btn btn-ghost', href: candidate.url, target: '_blank', rel: 'noopener noreferrer' }, '打开 Bangumi ↗'));
      footer.push(importBtn);
      openModal({ title: '作品详情', subtitle: candidate.title, wide: true, body: body, footer: footer });
    }

    function rowFor(candidate, bulkBtn) {
      const key = keyOf(candidate);
      const imported = importedKeys.has(key);
      const checkbox = h('input', { type: 'checkbox', checked: selectedKeys.has(key) && !imported, disabled: imported || importing });
      const status = h('span', { class: 'result-status' }, imported ? '已导入' : '');
      const button = h('button', { class: 'btn btn-soft btn-sm', disabled: imported || importing }, imported ? '已导入' : '导入');
      const cover = h('div', { class: 'result-cover' });
      if (candidate.cover_url) {
        const image = h('img', { src: candidate.cover_url, referrerpolicy: 'no-referrer', loading: 'lazy', alt: candidate.title });
        image.addEventListener('error', function () { image.remove(); cover.appendChild(coverFallback(candidate)); });
        cover.appendChild(image);
      } else {
        cover.appendChild(coverFallback(candidate));
      }
      const tagText = candidateTags(candidate).slice(0, 4).join(' / ');
      const info = h('div', { class: 'result-info' },
        h('div', { class: 'result-title' }, candidate.title),
        h('div', { class: 'result-meta' }, sourceLabel(candidate.source) + ' · ' + typeLabel(candidate.type) + (candidate.year ? ' · ' + candidate.year : '') + (candidate.rating ? ' · ' + scoreText(candidate.rating) : '') + (tagText ? ' · ' + tagText : ''))
      );
      const row = h('div', { class: 'result-row' + (imported ? ' imported' : ''), tabindex: '0', role: 'button', title: '查看详情' }, checkbox, cover, info, status, button);
      rowRefs.set(key, { checkbox: checkbox, status: status, button: button, row: row });
      checkbox.addEventListener('change', function () {
        if (checkbox.checked) selectedKeys.add(key);
        else selectedKeys.delete(key);
        updateBulkButton(bulkBtn);
      });
      button.addEventListener('click', async function () { await importCandidate(candidate, true); });
      row.addEventListener('click', function (event) {
        if (event.target && event.target.closest && event.target.closest('button, input, a, label')) return;
        openCandidateDetail(candidate);
      });
      row.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCandidateDetail(candidate); }
      });
      return row;
    }

    async function importCandidate(candidate, navigateAfter) {
      if (!(await ensureNsfw())) return null;
      const key = keyOf(candidate);
      const refs = rowRefs.get(key);
      if (refs) {
        refs.button.disabled = true;
        refs.button.textContent = '导入中…';
        refs.status.textContent = '';
      }
      try {
        const result = await api('/api/import', { method: 'POST', body: { candidate: candidate, review_limit: crawlLimitValue() } });
        importedKeys.add(key);
        selectedKeys.delete(key);
        if (refs) {
          refs.status.textContent = '';
          refs.button.textContent = '已导入';
          refs.button.disabled = true;
          refs.checkbox.checked = false;
          refs.checkbox.disabled = true;
          refs.row.classList.add('imported');
        }
        if (navigateAfter) {
          const snapshot = searchSnapshot();
          snapshot.workId = result.work.id;
          saveSearchReturn(snapshot);
          closeModal();
          toast('已导入《' + result.work.title + '》' + (result.job ? '，评论抓取已开始。' : '。'), 'good');
          navigate('/work/' + result.work.id);
        }
        return result;
      } catch (err) {
        if (refs) {
          refs.status.textContent = '失败';
          refs.button.textContent = '重试';
          refs.button.disabled = false;
        }
        toast('《' + candidate.title + '》导入失败：' + apiErrorText(err), 'bad');
        return null;
      }
    }

    async function bulkImport() {
      if (importing) return;
      const pending = pendingCandidates();
      if (!pending.length) { toast('请先勾选要导入的作品', 'bad'); return; }
      importing = true;
      const progressWrap = resultBox.querySelector('.import-progress');
      const finishBox = resultBox.querySelector('.bulk-finish');
      const bulkBtn = resultBox.querySelector('.bulk-bar .btn-primary');
      const label = progressWrap ? progressWrap.querySelector('.progress-label') : null;
      const bar = progressWrap ? progressWrap.querySelector('.progress i') : null;
      if (progressWrap) progressWrap.hidden = false;
      if (bulkBtn) { bulkBtn.disabled = true; bulkBtn.textContent = '导入中…'; }
      rowRefs.forEach(function (refs, key) {
        if (!importedKeys.has(key)) {
          refs.checkbox.disabled = true;
          refs.button.disabled = true;
        }
      });
      let cursor = 0;
      let completed = 0;
      let ok = 0;
      let failed = 0;
      const total = pending.length;
      function refreshProgress() {
        if (label) label.textContent = '正在导入 ' + completed + ' / ' + total + ' …';
        if (bar) bar.style.width = Math.round((completed / total) * 100) + '%';
      }
      refreshProgress();
      toast('开始导入 ' + total + ' 部作品…');
      try {
        async function worker() {
          while (true) {
            const index = cursor;
            cursor += 1;
            if (index >= total) return;
            const result = await importCandidate(pending[index], false);
            if (result) ok += 1;
            else failed += 1;
            completed += 1;
            refreshProgress();
          }
        }
        const workers = [];
        for (let i = 0; i < Math.min(3, total); i += 1) workers.push(worker());
        await Promise.all(workers);
        if (finishBox) {
          clear(finishBox);
          finishBox.hidden = false;
          finishBox.appendChild(h('span', null, '已导入 ' + ok + ' 部' + (failed ? '，' + failed + ' 部失败（可重试）' : '') + '，评论抓取任务已创建。'));
          finishBox.appendChild(h('button', { class: 'btn btn-ghost btn-sm', onclick: function () { closeModal(); navigate('/jobs'); } }, '查看任务 →'));
        }
        toast('批量导入完成：成功 ' + ok + ' 部' + (failed ? '，失败 ' + failed + ' 部' : ''), failed ? 'bad' : 'good');
      } catch (err) {
        if (finishBox) {
          clear(finishBox);
          finishBox.hidden = false;
          finishBox.appendChild(h('span', null, '导入中断：' + apiErrorText(err)));
        }
        toast('批量导入中断：' + apiErrorText(err), 'bad');
      } finally {
        importing = false;
        if (progressWrap) progressWrap.hidden = true;
        if (bulkBtn) updateBulkButton(bulkBtn);
        rowRefs.forEach(function (refs, key) {
          if (!importedKeys.has(key)) {
            refs.checkbox.disabled = false;
            refs.button.disabled = false;
          }
        });
      }
    }
  }

  function renderLinkPanel() {
    const form = h('div', { class: 'soft-panel', style: 'padding:16px' });
    const url = h('input', { class: 'input', placeholder: 'https://bgm.tv/subject/253' });
    const id = h('input', { class: 'input', placeholder: '或直接输入 Bangumi 条目 ID' });
    const linkLimit = h('input', { class: 'input', type: 'number', min: '1', max: '500', step: '1', value: '120' });
    const submit = h('button', { class: 'btn btn-primary' }, '导入并抓取');
    form.appendChild(h('div', { class: 'field' }, h('label', null, '作品链接'), url));
    form.appendChild(h('div', { class: 'row', style: 'margin-top:12px' }, h('div', { class: 'grow' }, id), submit));
    form.appendChild(h('div', { class: 'field', style: 'margin-top:12px' }, h('label', null, '单个作品抓取评论数'), linkLimit));
    form.appendChild(h('p', { class: 'hint', style: 'margin:10px 0 0' }, '粘贴 Bangumi 条目链接或 ID；每个作品按最新优先抓取。'));
    panel.appendChild(form);
    submit.addEventListener('click', async function () {
      if (!url.value.trim() && !id.value.trim()) { toast('请填写链接或 ID', 'bad'); return; }
      if (!(await ensureNsfw())) return;
      submit.disabled = true;
      submit.textContent = '导入中…';
      try {
        const body = { review_limit: Math.max(1, Math.min(500, parseInt(linkLimit.value, 10) || 120)) };
        if (url.value.trim()) body.url = url.value.trim();
        else { body.source = 'bangumi'; body.source_id = id.value.trim(); }
        const result = await api('/api/import', { method: 'POST', body: body });
        closeModal();
        toast('已导入《' + result.work.title + '》' + (result.job ? '，评论抓取已开始。' : '。'), 'good');
        navigate('/work/' + result.work.id);
      } catch (err) {
        submit.disabled = false;
        submit.textContent = '导入并抓取';
        toast(apiErrorText(err), 'bad');
      }
    });
  }

  searchTab.addEventListener('click', function () { setMode('search'); });
  linkTab.addEventListener('click', function () { setMode('link'); });
  renderSearchPanel(restore);
  if (restore) {
    window.requestAnimationFrame(function () {
      modal.body.scrollTop = Number(restore.modalScrollTop || 0);
      window.scrollTo(0, Number(restore.pageScrollY || 0));
    });
  }
}

async function renderWork(id) {
  clear(appEl);
  const page = h('div', { class: 'page' });
  page.appendChild(h('div', { class: 'detail-back' },
    h('button', { class: 'btn btn-ghost btn-sm', onclick: function () { goBack(id); } }, '← 返回')
  ));
  page.appendChild(h('div', { class: 'skeleton', style: 'height:220px;border-radius:22px' }));
  appEl.appendChild(page);
  const data = await api('/api/works/' + id);
  clear(page);
  const work = data.work;
  page.appendChild(h('div', { class: 'detail-back' },
    h('button', { class: 'btn btn-ghost btn-sm', onclick: function () { goBack(work.id); } }, (getSearchReturn() && String(getSearchReturn().workId) === String(work.id)) || getDiscoverReturn() ? '← 返回搜索' : '← 返回')
  ));
  const hero = h('section', { class: 'detail-hero' });
  hero.appendChild(coverNode(work));
  const info = h('div');
  info.appendChild(h('div', { class: 'eyebrow' }, typeLabel(work.type) + (work.year ? ' · ' + work.year : '')));
  info.appendChild(h('h1', { class: 'detail-title' }, work.title));
  const alts = (work.titles || []).filter(function (item) { return item.title !== work.title; }).slice(0, 3);
  if (alts.length) info.appendChild(h('div', { class: 'detail-alt' }, alts.map(function (item) { return item.title; }).join(' / ')));
  const meta = h('div', { class: 'detail-meta' });
  meta.appendChild(h('span', { class: 'score' }, scoreText(work.rating_score), h('small', null, '/10')));
  meta.appendChild(h('span', { class: 'chip' }, (work.review_count || 0) + ' 条评论'));
  if (work.nsfw) meta.appendChild(h('span', { class: 'badge accent' }, 'NSFW'));
  info.appendChild(meta);
  const tags = h('div', { class: 'tags' });
  (work.tags || []).forEach(function (tag) { tags.appendChild(h('span', { class: 'tag' }, tag.tag)); });
  info.appendChild(tags);
  const actions = h('div', { class: 'detail-actions' });
  actions.appendChild(h('button', { class: 'btn btn-primary', onclick: function () {
    if (!work.review_count) { toast('这部作品还没有评论，请先重新抓取。', 'bad'); return; }
    navigate('/chat?work=' + work.id);
  } }, '问 AI 这部作品'));
  actions.appendChild(h('button', { class: 'btn btn-ghost', onclick: function () { recrawlWork(work); } }, '重新抓取'));
  actions.appendChild(h('button', { class: 'btn btn-ghost', onclick: function () { openMergeModal(work); } }, '合并重复'));
  actions.appendChild(h('button', { class: 'btn btn-danger', onclick: function () { confirmDeleteWork(work); } }, '删除'));
  info.appendChild(actions);
  if (work.synopsis) info.appendChild(h('p', { class: 'page-sub' }, work.synopsis));
  hero.appendChild(info);
  page.appendChild(hero);

  const layout = h('div', { class: 'detail-grid' });
  const main = h('div');
  const side = h('div', { class: 'side-stack' });
  layout.appendChild(main); layout.appendChild(side);
  page.appendChild(layout);
  appEl.appendChild(page);

  const reviewsHead = sectionTitle('评论与口碑');
  main.appendChild(reviewsHead);
  const toolbar = h('div', { class: 'review-toolbar' });
  const sentiment = h('select', { class: 'select' }, h('option', { value: '' }, '全部观点'), h('option', { value: 'positive' }, '正面'), h('option', { value: 'negative' }, '负面'), h('option', { value: 'mixed' }, '中性'));
  const spoiler = h('select', { class: 'select' }, h('option', { value: '' }, '剧透不限'), h('option', { value: '0' }, '隐藏剧透'), h('option', { value: '1' }, '只看剧透'));
  const sort = h('select', { class: 'select' }, h('option', { value: 'newest' }, '最新'), h('option', { value: 'likes' }, '最有用'));
  const kind = h('select', { class: 'select' }, h('option', { value: '' }, '全部类型'), h('option', { value: '吐槽' }, '吐槽'), h('option', { value: '评论' }, '评论'));
  toolbar.appendChild(kind);
  toolbar.appendChild(sentiment); toolbar.appendChild(spoiler); toolbar.appendChild(sort);
  main.appendChild(toolbar);
  const reviewBox = h('div', { class: 'review-list' });
  main.appendChild(reviewBox);
  const moreWrap = h('div', { class: 'load-more' });
  main.appendChild(moreWrap);
  let reviewState = { page: 1, items: [], total: 0, page_size: 20 };

  async function loadReviews(reset) {
    if (reset) reviewState = { page: 1, items: [], total: 0, page_size: 20 };
    const params = new URLSearchParams({ page: String(reviewState.page), page_size: '20', sort: sort.value });
    if (kind.value) params.set('kind', kind.value);
    if (sentiment.value) params.set('sentiment', sentiment.value);
    if (spoiler.value !== '') params.set('spoiler', spoiler.value);
    const result = await api('/api/works/' + work.id + '/reviews?' + params.toString());
    reviewState.items = reset ? result.items : reviewState.items.concat(result.items);
    reviewState.total = result.total;
    reviewState.page = result.page;
    renderReviews();
  }

  function renderReviews() {
    clear(reviewBox);
    if (!reviewState.items.length) {
      reviewBox.appendChild(h('div', { class: 'empty', style: 'padding:38px' },
        h('h3', null, '还没有评论'),
        h('p', null, '可以在任务页查看抓取进度，或点上方“重新抓取”。')
      ));
    }
    reviewState.items.forEach(function (review) { reviewBox.appendChild(reviewCard(review)); });
    clear(moreWrap);
    if (reviewState.items.length < reviewState.total) {
      moreWrap.appendChild(h('button', { class: 'btn btn-ghost', onclick: function () { reviewState.page += 1; loadReviews(false); } }, '加载更多评论'));
    }
  }

  [kind, sentiment, spoiler, sort].forEach(function (select) { select.addEventListener('change', function () { loadReviews(true); }); });
  await loadReviews(true);

  side.appendChild(h('div', { class: 'panel panel-body' },
    h('h3', { style: 'margin-bottom:8px' }, '口碑拆解'),
    (work.ratings || []).length ? work.ratings.map(function (rating) {
      const normalized = rating.score == null ? null : (rating.score / (rating.scale || 10)) * 10;
      return h('div', null,
        h('div', { class: 'stat-row' }, h('span', null, sourceLabel(rating.source) + (rating.summary ? ' · ' + rating.summary : '')), h('strong', null, (normalized == null ? '暂无' : scoreText(normalized)) + ' / 10')),
        h('div', { class: 'rating-bar' }, h('div', { class: 'bar' }, h('i', { style: 'width:' + Math.max(0, Math.min(100, (normalized || 0) * 10)) + '%' })), h('span', { class: 'tiny faint' }, String(rating.vote_count || 0) + ' 票'))
      );
    }) : h('p', { class: 'hint' }, '暂无来源评分。'),
    h('div', { class: 'divider' }),
    h('div', { class: 'stat-row' }, h('span', null, '评论总数'), h('strong', null, String(work.review_count || 0))),
    h('div', { class: 'stat-row' }, h('span', null, '最近更新'), h('strong', null, formatDate(work.updated_at)))
  ));
  const sourcePanel = h('div', { class: 'panel panel-body' }, h('h3', { style: 'margin-bottom:8px' }, '来源'));
  (work.sources || []).forEach(function (item) {
    const row = h('div', { class: 'source-link' }, h('span', null, sourceLabel(item.source)), item.url ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer' }, '打开原站 ↗') : h('span', { class: 'faint' }, '本地演示'));
    sourcePanel.appendChild(row);
  });
  side.appendChild(sourcePanel);
}

function reviewCard(review) {
  const card = h('article', { class: 'review-card' });
  const head = h('div', { class: 'review-head' });
  head.appendChild(sourceBadge(review.source));
  if (review.kind) head.appendChild(h('span', { class: 'badge accent' }, review.kind));
  if (review.author) head.appendChild(h('span', null, review.author));
  if (review.posted_at) head.appendChild(h('span', null, formatDate(review.posted_at)));
  if (review.likes) head.appendChild(h('span', null, '👍 ' + review.likes));
  if (review.spoiler) head.appendChild(h('span', { class: 'badge' }, '含剧透'));
  head.appendChild(h('span', { class: 'review-score score' }, review.score != null ? scoreText(review.score) : (review.sentiment === 'positive' ? '正面' : review.sentiment === 'negative' ? '负面' : '中立')));
  card.appendChild(head);
  const textNode = h('div', { class: 'review-text' + (review.spoiler ? ' spoiler' : '') }, review.excerpt || '');
  card.appendChild(textNode);
  const actions = h('div', { class: 'review-actions' });
  if (review.spoiler) {
    actions.appendChild(h('button', { class: 'btn btn-xs btn-ghost', onclick: function () { textNode.classList.remove('spoiler'); this.remove(); } }, '显示剧透'));
  }
  actions.appendChild(h('button', { class: 'btn btn-xs btn-ghost', onclick: function () { openReview(review.id); } }, '查看全文'));
  if (review.url) actions.appendChild(h('a', { class: 'btn btn-xs btn-ghost', href: review.url, target: '_blank', rel: 'noopener noreferrer' }, '原站 ↗'));
  card.appendChild(actions);
  return card;
}

async function openReview(id) {
  try {
    const review = await api('/api/reviews/' + id);
    openModal({
      title: '评论全文',
      subtitle: sourceLabel(review.source) + (review.kind ? ' · ' + review.kind : '') + (review.author ? ' · ' + review.author : '') + (review.posted_at ? ' · ' + formatDate(review.posted_at) : ''),
      wide: true,
      body: [
        h('div', { class: 'row', style: 'margin-bottom:12px' },
          h('span', { class: 'score' }, review.score != null ? scoreText(review.score) : (review.sentiment === 'positive' ? '正面' : review.sentiment === 'negative' ? '负面' : '中立')),
          review.kind ? h('span', { class: 'badge accent' }, review.kind) : null,
          review.spoiler ? h('span', { class: 'badge accent' }, '含剧透') : null,
          h('span', { class: 'chip' }, review.work_title || '')
        ),
        h('div', { class: 'review-text' }, review.text || ''),
        review.url ? h('p', { style: 'margin-top:16px' }, h('a', { class: 'btn btn-ghost btn-sm', href: review.url, target: '_blank', rel: 'noopener noreferrer' }, '打开原站 ↗')) : null
      ]
    });
  } catch (err) {
    toast(apiErrorText(err), 'bad');
  }
}

function recrawlWork(work) {
  openModal({
    title: '重新抓取评论',
    narrow: true,
    body: [h('p', { class: 'hint', style: 'margin-top:0' }, '会按当前设置的上限重新抓取 Bangumi；已存在的评论会更新而不是重复插入。')],
    footer: [
      h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
      h('button', { class: 'btn btn-primary', onclick: async function () {
        try {
          const result = await api('/api/works/' + work.id + '/crawl', { method: 'POST', body: { source: 'bangumi' } });
          closeModal();
          toast('抓取任务已创建 #' + result.job_id, 'good');
          navigate('/jobs');
        } catch (err) { toast(apiErrorText(err), 'bad'); }
      } }, '开始抓取')
    ]
  });
}

async function openMergeModal(work) {
  let candidates = [];
  try {
    const data = await api('/api/works?page_size=50&type=' + encodeURIComponent(work.type));
    candidates = (data.items || []).filter(function (item) { return item.id !== work.id; });
  } catch (err) { toast(apiErrorText(err), 'bad'); return; }
  const select = h('select', { class: 'select' }, candidates.map(function (item) { return h('option', { value: item.id }, item.title + ' (' + item.review_count + ' 条评论)'); }));
  openModal({
    title: '合并重复作品',
    subtitle: '把另一个条目的评论、标签和评分并入当前作品。',
    narrow: true,
    body: [
      h('p', { class: 'hint', style: 'margin-top:0' }, '当前作品：《' + work.title + '》'),
      candidates.length ? h('div', { class: 'field' }, h('label', null, '要并入的作品'), select) : h('p', { class: 'hint' }, '没有同类型的其他作品。'),
    ],
    footer: [
      h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
      h('button', { class: 'btn btn-danger', disabled: !candidates.length, onclick: async function () {
        if (!candidates.length) return;
        try {
          await api('/api/works/merge', { method: 'POST', body: { source_work_id: Number(select.value), target_work_id: work.id } });
          closeModal();
          toast('作品已合并', 'good');
          navigate('/work/' + work.id);
        } catch (err) { toast(apiErrorText(err), 'bad'); }
      } }, '确认合并')
    ]
  });
}

function confirmDeleteWork(work) {
  openModal({
    title: '删除作品？',
    subtitle: '这会删除本地保存的来源映射、评论和分块索引。原站内容不受影响。',
    narrow: true,
    body: [h('p', { class: 'hint', style: 'margin-top:0' }, '《' + work.title + '》将被永久删除。')],
    footer: [
      h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
      h('button', { class: 'btn btn-danger', onclick: async function () {
        try {
          await api('/api/works/' + work.id, { method: 'DELETE' });
          closeModal();
          toast('已删除', 'good');
          navigate('/discover');
        } catch (err) { toast(apiErrorText(err), 'bad'); }
      } }, '删除')
    ]
  });
}

async function renderChat(params) {
  clear(appEl);
  const page = h('div', { class: 'page' });
  const workId = params.get('work') ? Number(params.get('work')) : null;
  let sessionId = params.get('session') ? Number(params.get('session')) : null;
  const draft = params.get('prompt') || '';
  const sessionsData = await api('/api/chat/sessions');
  state.chat.sessions = sessionsData.items || [];
  state.chat.messages = [];
  state.chat.citations = [];
  state.chat.current = null;
  state.chat.work = null;
  state.chat.coverage = null;
  if (sessionId) {
    const data = await api('/api/chat/sessions/' + sessionId);
    state.chat.current = data.session;
    state.chat.messages = data.messages || [];
    state.chat.work = data.session.work_id ? { id: data.session.work_id, title: data.session.work_title || '已绑定作品' } : null;
    sessionId = data.session.id;
  } else if (workId) {
    try { state.chat.work = (await api('/api/works/' + workId)).work; } catch (err) { state.chat.work = { id: workId, title: '作品 #' + workId }; }
  }
  state.chat.precision = state.chat.work ? null : loadPrecision();
  const layout = h('div', { class: 'chat-layout' });
  const sidebar = h('aside', { class: 'chat-sidebar' });
  const newBtn = h('button', { class: 'btn btn-primary btn-block', onclick: function () { navigate('/chat' + (state.chat.work ? '?work=' + state.chat.work.id : '')); } }, '＋ 新对话');
  sidebar.appendChild(newBtn);
  const list = h('div', { class: 'session-list' });
  sidebar.appendChild(list);
  const main = h('section', { class: 'chat-main' });
  layout.appendChild(sidebar);
  layout.appendChild(main);
  page.appendChild(layout);
  appEl.appendChild(page);

  function renderSessionList() {
    clear(list);
    if (!state.chat.sessions.length) {
      list.appendChild(h('p', { class: 'hint' }, '还没有对话。随手问一句吧。'));
      return;
    }
    state.chat.sessions.forEach(function (session) {
      const button = h('button', { class: 'session-item' + (sessionId === session.id ? ' active' : '') },
        h('span', { class: 's-title' }, session.title || '新对话'),
        h('span', { class: 's-meta' }, (session.work_title ? session.work_title + ' · ' : '') + (session.message_count || 0) + ' 条')
      );
      button.addEventListener('click', function () { navigate('/chat?session=' + session.id); });
      const remove = h('button', { class: 'session-delete', title: '删除这个对话', 'aria-label': '删除对话' }, '删除');
      remove.addEventListener('click', function (event) {
        event.stopPropagation();
        confirmDeleteSession(session);
      });
      list.appendChild(h('div', { class: 'session-row' }, button, remove));
    });
  }

  function confirmDeleteSession(session) {
    openModal({
      title: '删除对话？',
      subtitle: '会从本地数据库删除该对话及全部消息，不能恢复。',
      narrow: true,
      body: [h('p', { class: 'hint', style: 'margin-top:0' }, '《' + (session.title || '新对话') + '》将永久删除。')],
      footer: [
        h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
        h('button', { class: 'btn btn-danger', onclick: async function () {
          try {
            await api('/api/chat/sessions/' + session.id, { method: 'DELETE' });
            closeModal();
            state.chat.sessions = state.chat.sessions.filter(function (item) { return item.id !== session.id; });
            if (sessionId === session.id) navigate('/chat');
            else { renderSessionList(); refreshSessions(); }
            toast('对话已删除', 'good');
          } catch (err) { toast(apiErrorText(err), 'bad'); }
        } }, '删除')
      ]
    });
  }

  let coverageNode = null;
  let precisionButton = null;

  function openPrecisionModal() {
    let draft = Object.assign({}, state.chat.precision || defaultPrecision());
    const controls = {};
    const estimateNode = h('p', { class: 'hint', style: 'margin:10px 0 0' }, '正在估算…');

    function makeNumber(key, min, max, step) {
      const input = h('input', { class: 'input', type: 'number', min: String(min), max: String(max), step: String(step), value: String(draft[key]) });
      function apply() {
        let value = Number(input.value);
        if (isNaN(value)) value = draft[key];
        value = Math.max(min, Math.min(max, value));
        draft[key] = value;
        input.value = String(value);
        refreshEstimate();
      }
      input.addEventListener('change', apply);
      controls[key] = input;
      return input;
    }

    async function refreshEstimate() {
      estimateNode.textContent = '正在估算…';
      try {
        const data = await api('/api/chat/estimate', { method: 'POST', body: { precision: draft } });
        const warning = data.estimated_total_tokens > 60000 ? '。成本较高，请确认上下文和预算。' : '';
        estimateNode.textContent = '当前 ' + data.works_total + ' 部作品（' + data.galgame_total + ' 部 Galgame），预计约 '
          + Math.round(data.estimated_total_tokens / 1000) + 'k token，约 ' + data.estimated_calls + ' 次 AI 调用' + warning + '。';
      } catch (err) {
        estimateNode.textContent = '估算失败：' + apiErrorText(err);
      }
    }

    function applyPreset(name) {
      const preset = SEARCH_PRESETS[name];
      if (!preset) return;
      draft = JSON.parse(JSON.stringify(preset));
      PRECISION_KEYS.forEach(function (key) {
        if (controls[key]) controls[key].value = String(draft[key]);
      });
      WEIGHT_KEYS.forEach(function (key) {
        if (controls['weight_' + key]) controls['weight_' + key].value = String(draft.weights[key]);
      });
      refreshEstimate();
    }

    const summary = makeNumber('screen_summary_chars', 0, 2000, 50);
    const batchWorks = makeNumber('screen_batch_works', 5, 40, 1);
    const passes = makeNumber('screen_passes', 1, 3, 1);
    const shortlist = makeNumber('shortlist_works', 4, 100, 1);
    const refineReviews = makeNumber('refine_reviews_per_work', 0, 20, 1);
    const details = makeNumber('detail_works', 0, 50, 1);
    const reviews = makeNumber('detail_reviews_per_work', 0, 100, 1);
    const reviewChars = makeNumber('detail_review_chars', 100, 5000, 50);
    const context = makeNumber('max_context_chars', 4000, 500000, 1000);
    const batch = makeNumber('screen_batch_chars', 4000, 100000, 1000);
    const weightInputs = {};
    WEIGHT_KEYS.forEach(function (key) {
      const input = h('input', { class: 'input', type: 'number', min: '0', max: '10', step: '1', value: String(draft.weights[key]) });
      input.addEventListener('change', function () {
        let value = Number(input.value);
        if (isNaN(value)) value = draft.weights[key];
        value = Math.max(0, Math.min(10, value));
        draft.weights[key] = value;
        input.value = String(value);
        refreshEstimate();
      });
      controls['weight_' + key] = input;
      weightInputs[key] = input;
    });
    const presetRow = h('div', { class: 'row', style: 'flex-wrap:wrap;margin-bottom:14px' },
      Object.keys(SEARCH_PRESETS).map(function (key) {
        return h('button', { class: 'btn btn-soft btn-sm', onclick: function () { applyPreset(key); } }, SEARCH_PRESETS[key].label);
      })
    );
    const grid = h('div', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', null, '全量摘要（0–2000 字符）'), summary),
      h('div', { class: 'field' }, h('label', null, '每批作品数（5–40）'), batchWorks),
      h('div', { class: 'field' }, h('label', null, '评分轮数（1–3）'), passes),
      h('div', { class: 'field' }, h('label', null, '精排候选（4–100 部）'), shortlist),
      h('div', { class: 'field' }, h('label', null, '精排每部评论（0–20）'), refineReviews),
      h('div', { class: 'field' }, h('label', null, '详情作品（0–50 部）'), details),
      h('div', { class: 'field' }, h('label', null, '详情每部评论（0–100）'), reviews),
      h('div', { class: 'field' }, h('label', null, '单条详情长度（100–5000）'), reviewChars),
      h('div', { class: 'field' }, h('label', null, '本次上下文上限（字符）'), context),
      h('div', { class: 'field' }, h('label', null, '评分批字符（4000–100000）'), batch)
    );
    const weightGrid = h('div', { class: 'form-grid', style: 'margin-top:12px' },
      h('div', { class: 'field' }, h('label', null, '相关性权重'), weightInputs.relevance),
      h('div', { class: 'field' }, h('label', null, '剧情权重'), weightInputs.story),
      h('div', { class: 'field' }, h('label', null, '人物权重'), weightInputs.characters),
      h('div', { class: 'field' }, h('label', null, '氛围权重'), weightInputs.atmosphere),
      h('div', { class: 'field' }, h('label', null, '玩法权重'), weightInputs.gameplay),
      h('div', { class: 'field' }, h('label', null, '上手权重'), weightInputs.accessibility),
      h('div', { class: 'field' }, h('label', null, '置信度权重'), weightInputs.confidence)
    );
    openModal({
      title: '搜索精度',
      subtitle: '目录始终包含全部作品；调低只会减少详情、摘要和 token。',
      wide: true,
      body: [presetRow, grid, h('div', { class: 'label', style: 'margin-top:14px' }, '评分权重（0–10）'), weightGrid, estimateNode, h('p', { class: 'hint' }, '目录始终包含全部作品；预估按字符数/1.7 近似，实际费用取决于模型 tokenizer 和服务商价格。')],
      footer: [
        h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
        h('button', { class: 'btn btn-primary', onclick: function () {
          state.chat.precision = Object.assign({}, draft);
          savePrecision(state.chat.precision);
          if (precisionButton) precisionButton.textContent = '精度：' + precisionName(state.chat.precision);
          closeModal();
          toast('搜索精度已保存，下次发送生效', 'good');
        } }, '保存精度')
      ]
    });
    refreshEstimate();
  }

  async function openDeepDivePicker() {
    let data;
    try { data = await api('/api/works?page_size=100&sort=reviews'); }
    catch (err) { toast(apiErrorText(err), 'bad'); return; }
    const works = (data.items || []).filter(function (item) { return item.review_count > 0; });
    if (!works.length) { toast('还没有可深挖的作品，请先导入并抓取评论。', 'bad'); return; }
    const select = h('select', { class: 'select' }, works.map(function (item) {
      return h('option', { value: item.id }, item.title + '（' + item.review_count + ' 条评论）');
    }));
    openModal({
      title: '深挖一部作品',
      subtitle: '将创建一部作品范围的对话，最多展开 120 条评论和作品摘要。',
      narrow: true,
      body: [h('div', { class: 'field' }, h('label', null, '选择作品'), select)],
      footer: [
        h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
        h('button', { class: 'btn btn-primary', onclick: function () {
          const workId = Number(select.value);
          if (!workId) return;
          closeModal();
          navigate('/chat?work=' + workId);
        } }, '开始深挖')
      ]
    });
  }

  function renderChatHead() {
    clear(main);
    const head = h('div', { class: 'chat-head' });
    const left = h('div', { class: 'row' });
    if (state.chat.work) {
      left.appendChild(h('span', { class: 'scope-chip' }, '作品范围：' + state.chat.work.title));
    } else {
      left.appendChild(h('span', { class: 'scope-chip' }, '全库问答'));
      precisionButton = h('button', { class: 'btn btn-ghost btn-sm', onclick: openPrecisionModal }, '精度：' + precisionName(state.chat.precision));
      left.appendChild(precisionButton);
      left.appendChild(h('button', { class: 'btn btn-ghost btn-sm', onclick: openDeepDivePicker }, '深挖作品'));
    }
    coverageNode = h('span', { class: 'hint' }, chatCoverageLabel(state.chat.coverage));
    left.appendChild(coverageNode);
    left.appendChild(h('span', { class: 'hint' }, '回答只基于已抓取的评论'));
    head.appendChild(left);
    head.appendChild(h('a', { class: 'btn btn-ghost btn-sm', href: '#/settings' }, 'AI 设置'));
    main.appendChild(head);
    const configured = Boolean(state.bootstrap.settings.ai.configured || state.bootstrap.settings.ai.key_configured);
    if (!configured) {
      const banner = h('div', { class: 'banner', style: 'margin:12px 14px 0' },
        h('span', null, '尚未配置 AI API。'),
        h('a', { href: '#/settings' }, '去设置 →')
      );
      main.appendChild(banner);
    }
    const scroll = h('div', { class: 'chat-scroll' });
    main.appendChild(scroll);
    if (!state.chat.messages.length) {
      const empty = h('div', { class: 'chat-empty' },
        h('h2', null, state.chat.work ? '聊聊《' + state.chat.work.title + '》' : '从评论里找答案'),
        h('p', null, state.chat.work ? '问口碑、问适合谁、问争议点。回答会标注引用评论，点开就能看原文。' : '问一个跨作品的问题，或先在作品详情页选择“问 AI 这部作品”。')
      );
      const suggestions = state.chat.work ? [
        '这部作品的口碑如何？主要优点和缺点是什么？',
        '它适合什么类型的观众？有没有明显的劝退点？',
        '评论里对结局的评价一致吗？',
      ] : [
        '库内评分最高的 galgame 有什么共同点？',
        '想找一部适合周末看的治愈番，有什么推荐？',
        '评论里最常被提到的缺点是什么？',
      ];
      const sugg = h('div', { class: 'suggestions' });
      suggestions.forEach(function (item) {
        sugg.appendChild(h('button', { class: 'suggestion', onclick: function () { composer.value = item; composer.focus(); } }, item));
      });
      empty.appendChild(sugg);
      scroll.appendChild(empty);
    } else {
      renderMessages(scroll);
    }
    const composerWrap = h('div', { class: 'chat-composer' });
    const box = h('div', { class: 'composer-box' });
    const composer = h('textarea', { placeholder: configured ? '问点什么…（Enter 发送，Shift+Enter 换行）' : '配置 AI API 后即可对话', disabled: !configured });
    composer.value = draft;
    const actions = h('div', { class: 'composer-actions' });
    const stop = h('button', { class: 'btn btn-ghost btn-sm', hidden: true }, '停止');
    const send = h('button', { class: 'btn btn-primary btn-sm', disabled: !configured }, '发送');
    actions.appendChild(stop);
    actions.appendChild(send);
    box.appendChild(composer);
    box.appendChild(actions);
    composerWrap.appendChild(box);
    composerWrap.appendChild(h('div', { class: 'composer-hint' },
      h('span', null, '评论来源会保留在引用中；不会发送作者昵称。'),
      h('span', null, 'Ctrl/⌘ + Enter 也可发送')
    ));
    main.appendChild(composerWrap);

    composer.addEventListener('input', function () {
      composer.style.height = 'auto';
      composer.style.height = Math.min(160, composer.scrollHeight) + 'px';
    });
    composer.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });
    send.addEventListener('click', sendMessage);
    stop.addEventListener('click', function () {
      if (state.chat.controller) state.chat.controller.abort();
    });

    function currentCitations() { return state.chat.citations || []; }

    function renderMessages(scrollNode) {
      clear(scrollNode);
      state.chat.messages.forEach(function (message) {
        const isUser = message.role === 'user';
        const row = h('div', { class: 'msg ' + (isUser ? 'user' : 'assistant') });
        row.appendChild(h('div', { class: 'msg-avatar' }, isUser ? '你' : '雷'));
        const bubble = h('div', { class: 'msg-bubble' });
        const rich = renderRich(message.content || '', message.citations || []);
        bubble.appendChild(rich);
        if (!isUser && state.chat.streaming && message === state.chat.messages[state.chat.messages.length - 1]) {
          rich.appendChild(h('span', { class: 'typing' }));
        }
        const citations = message.citations || [];
        const hasInline = /\[R\d+\]/.test(message.content || '');
        if (citations.length && !hasInline) {
          const rowCitations = h('div', { class: 'citation-row' });
          citations.forEach(function (citation) {
            rowCitations.appendChild(citationButton(citation));
          });
          bubble.appendChild(rowCitations);
        }
        if (!isUser && message.usage && message.usage.total_tokens) {
          const usage = message.usage;
          bubble.appendChild(h('div', { class: 'msg-usage' },
            '本次消耗 ' + String(usage.total_tokens) + ' token（输入 ' + String(usage.prompt_tokens || 0) + ' / 输出 ' + String(usage.completion_tokens || 0) + '）'
          ));
        }
        bubble.appendChild(h('div', { class: 'msg-time' }, isUser ? '你' : 'AI 分析'));
        row.appendChild(bubble);
        scrollNode.appendChild(row);
      });
      scrollNode.scrollTop = scrollNode.scrollHeight;
    }

    function renderRich(content, citations) {
      const wrap = h('div', { class: 'msg-text' });
      const parts = String(content || '').split(/(\[R\d+\])/g);
      parts.forEach(function (part) {
        const match = part.match(/^\[R(\d+)\]$/);
        if (match) {
          const id = Number(match[1]);
          const citation = citations.find(function (item) { return Number(item.id) === id; });
          if (citation) {
            wrap.appendChild(citationButton(citation));
            return;
          }
        }
        appendBold(wrap, part);
      });
      return wrap;
    }

    function citationButton(citation) {
      const button = h('button', { class: 'citation', title: citation.work_title || '' }, '[' + sourceLabel(citation.source) + ' R' + citation.id + ']');
      button.addEventListener('click', function () { openReview(citation.id); });
      return button;
    }

    async function sendMessage() {
      const value = composer.value.trim();
      if (!value || state.chat.streaming) return;
      if (!configured) { toast('请先在设置中配置 AI API', 'bad'); return; }
      composer.value = '';
      composer.style.height = 'auto';
      state.chat.messages.push({ role: 'user', content: value, citations: [] });
      const assistant = { role: 'assistant', content: '', citations: [], usage: null };
      state.chat.messages.push(assistant);
      state.chat.streaming = true;
      state.chat.citations = [];
      send.disabled = true;
      stop.hidden = false;
      renderMessages(scroll);
      const controller = new AbortController();
      state.chat.controller = controller;
      try {
        const payload = { session_id: sessionId, work_id: state.chat.work ? state.chat.work.id : null, message: value };
        if (!state.chat.work && state.chat.precision) payload.precision = state.chat.precision;
        await streamChat(payload, {
          meta: function (data) {
            if (data.session_id && !sessionId) {
              sessionId = data.session_id;
              history.replaceState(null, '', '#/chat?session=' + sessionId);
            }
            state.chat.citations = data.citations || assistant.citations || [];
            assistant.citations = data.citations || [];
            if (data.coverage) {
              state.chat.coverage = data.coverage;
              if (coverageNode) coverageNode.textContent = chatCoverageLabel(data.coverage);
            }
            refreshSessions();
          },
          delta: function (data) {
            assistant.content += data.text || '';
            renderMessages(scroll);
          },
          done: function (data) {
            assistant.usage = data.usage || assistant.usage || null;
            assistant.citations = data.citations || assistant.citations || [];
            state.chat.citations = assistant.citations;
            state.chat.streaming = false;
            state.chat.controller = null;
            refreshSessions();
            renderMessages(scroll);
          },
          error: function (data) {
            assistant.content = (assistant.content ? assistant.content + '\n\n' : '') + '⚠ ' + (data.message || '生成失败');
            state.chat.streaming = false;
            state.chat.controller = null;
            renderMessages(scroll);
            toast(data.message || '生成失败', 'bad');
          }
        }, controller.signal);
      } catch (err) {
        if (err.name !== 'AbortError') {
          assistant.content = assistant.content || ('⚠ ' + apiErrorText(err));
          toast(apiErrorText(err), 'bad');
        }
        state.chat.streaming = false;
        state.chat.controller = null;
        renderMessages(scroll);
      } finally {
        state.chat.streaming = false;
        state.chat.controller = null;
        stop.hidden = true;
        send.disabled = false;
        refreshSessions();
      }
    }
  }

  async function refreshSessions() {
    try {
      const data = await api('/api/chat/sessions');
      state.chat.sessions = data.items || [];
      renderSessionList();
    } catch (err) { /* 静默 */ }
  }

  renderSessionList();
  renderChatHead();
}

async function streamChat(payload, handlers, signal) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.bootstrap.csrf_token },
    body: JSON.stringify(payload),
    signal: signal,
  });
  if (!response.ok) {
    let message = '对话请求失败（HTTP ' + response.status + '）';
    try {
      const data = await response.json();
      message = (data.error && data.error.message) || message;
    } catch (err) { /* ignore */ }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    let index = buffer.indexOf('\n\n');
    while (index >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      let event = 'message';
      let data = '';
      frame.split('\n').forEach(function (line) {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
      });
      if (data) {
        try {
          const parsed = JSON.parse(data);
          if (handlers[event]) handlers[event](parsed);
        } catch (err) { /* ignore malformed frame */ }
      }
      index = buffer.indexOf('\n\n');
    }
  }
}

function appendBold(container, value) {
  const parts = String(value || '').split(/(\*\*[^*]+\*\*)/g);
  parts.forEach(function (part) {
    if (part.length > 4 && part.slice(0, 2) === '**' && part.slice(-2) === '**') {
      container.appendChild(h('strong', null, part.slice(2, -2)));
    } else if (part) {
      container.appendChild(text(part));
    }
  });
}

async function renderJobs() {
  clear(appEl);
  const page = h('div', { class: 'page' });
  page.appendChild(h('div', { class: 'eyebrow' }, 'CRAWL QUEUE'));
  page.appendChild(h('h1', null, '抓取任务'));
  page.appendChild(h('p', { class: 'page-sub' }, '评论抓取在后台进行；失败的任务可以重试，已抓到的内容不会丢。'));
  const list = h('div', { class: 'job-list', style: 'margin-top:22px' });
  page.appendChild(list);
  appEl.appendChild(page);

  async function refresh() {
    try {
      const data = await api('/api/jobs');
      clear(list);
      const items = data.items || [];
      if (!items.length) {
        list.appendChild(emptyState('还没有任务', '添加作品后，评论抓取任务会出现在这里。', '添加作品', openAddModal));
        return;
      }
      items.forEach(function (job) { list.appendChild(jobRow(job)); });
      const active = items.filter(function (job) { return ['queued', 'running', 'cancelling'].indexOf(job.status) >= 0; }).length;
      const badge = document.getElementById('job-badge');
      badge.hidden = !active;
      badge.textContent = String(active);
      if (active) state.jobsTimer = window.setTimeout(refresh, 1500);
    } catch (err) {
      clear(list);
      list.appendChild(emptyState('任务加载失败', apiErrorText(err), '重试', refresh));
    }
  }

  function confirmDeleteJob(job) {
    openModal({
      title: '删除任务记录？',
      subtitle: '只删除本地任务历史，不会删除已抓取的作品和评论。',
      narrow: true,
      body: [h('p', { class: 'hint', style: 'margin-top:0' }, '任务 #' + job.id + ' 的记录将被永久删除。')],
      footer: [
        h('button', { class: 'btn btn-ghost', onclick: closeModal }, '取消'),
        h('button', { class: 'btn btn-danger', onclick: async function () {
          try {
            await api('/api/jobs/' + job.id, { method: 'DELETE' });
            closeModal();
            toast('任务记录已删除', 'good');
            refresh();
          } catch (err) { toast(apiErrorText(err), 'bad'); }
        } }, '删除')
      ]
    });
  }

  function jobRow(job) {
    let progress = {};
    try { progress = JSON.parse(job.progress_json || '{}'); } catch (err) { progress = {}; }
    const fetched = progress.fetched || 0;
    const total = Math.max(job.total || 0, fetched, 1);
    const pct = Math.min(100, Math.round((fetched / total) * 100));
    const statusLabels = { queued: '排队中', running: '抓取中', cancelling: '正在取消', cancelled: '已取消', success: '完成', failed: '失败', interrupted: '已中断' };
    const card = h('div', { class: 'job' });
    const info = h('div');
    info.appendChild(h('div', { class: 'job-title' }, (job.work_title || '作品 #' + job.work_id) + ' · ' + sourceLabel(job.source)));
    info.appendChild(h('div', { class: 'job-meta' }, '#' + job.id + ' · ' + (job.message || '等待开始') + (job.error ? ' · ' + job.error : '')));
    const bar = h('div', { class: 'progress' }, h('i', { style: 'width:' + pct + '%' }));
    info.appendChild(bar);
    info.appendChild(h('div', { class: 'job-meta' }, '已抓 ' + fetched + ' 条 · 新增 ' + (progress.inserted || 0) + ' · 更新 ' + (progress.updated || 0)));
    card.appendChild(info);
    card.appendChild(h('div', { class: 'status ' + job.status }, statusLabels[job.status] || job.status));
    const actions = h('div', { class: 'job-actions' });
    actions.appendChild(h('a', { class: 'btn btn-ghost btn-xs', href: '#/work/' + job.work_id }, '作品'));
    if (['queued', 'running'].indexOf(job.status) >= 0) {
      actions.appendChild(h('button', { class: 'btn btn-ghost btn-xs', onclick: async function () {
        try { await api('/api/jobs/' + job.id + '/cancel', { method: 'POST', body: {} }); toast('已请求取消', 'good'); refresh(); }
        catch (err) { toast(apiErrorText(err), 'bad'); }
      } }, '取消'));
    }
    if (['failed', 'cancelled', 'interrupted', 'success'].indexOf(job.status) >= 0) {
      actions.appendChild(h('button', { class: 'btn btn-ghost btn-xs', onclick: async function () {
        try { await api('/api/jobs/' + job.id + '/retry', { method: 'POST', body: {} }); toast('已重新排队', 'good'); refresh(); }
        catch (err) { toast(apiErrorText(err), 'bad'); }
      } }, '重试'));
      actions.appendChild(h('button', { class: 'btn btn-danger btn-xs', onclick: function () { confirmDeleteJob(job); } }, '删除'));
    }
    card.appendChild(actions);
    return card;
  }

  await refresh();
}

async function renderSettings() {
  clear(appEl);
  const settings = await api('/api/settings');
  const page = h('div', { class: 'page' });
  page.appendChild(h('div', { class: 'eyebrow' }, 'CONTROL ROOM'));
  page.appendChild(h('h1', null, '设置'));
  page.appendChild(h('p', { class: 'page-sub' }, 'AI Key 只保存在本机 secrets 文件；所有请求都从本地后端转发。'));
  const grid = h('div', { class: 'settings-grid' });
  page.appendChild(grid);
  appEl.appendChild(page);

  const aiPanel = h('section', { class: 'panel panel-body settings-section' });
  aiPanel.appendChild(h('h2', null, 'AI 接口（OpenAI 兼容）'));
  aiPanel.appendChild(h('p', null, '支持 DeepSeek、OpenAI、Moonshot、SiliconFlow、Ollama 或任何兼容 /chat/completions 的服务。'));
  const preset = h('select', { class: 'select' },
    h('option', { value: 'deepseek' }, 'DeepSeek'), h('option', { value: 'openai' }, 'OpenAI'),
    h('option', { value: 'moonshot' }, 'Moonshot'), h('option', { value: 'siliconflow' }, 'SiliconFlow'),
    h('option', { value: 'ollama' }, 'Ollama（本地）'), h('option', { value: 'custom' }, '自定义')
  );
  const baseUrl = h('input', { class: 'input', value: settings.ai.base_url || '', placeholder: 'https://api.example.com/v1' });
  const model = h('input', { class: 'input', value: settings.ai.model || '', placeholder: '模型名称' });
  const key = h('input', { class: 'input', type: 'password', placeholder: settings.ai.key_configured ? '已保存：' + settings.ai.key_hint + '（输入新值可替换）' : 'API Key（本地 Ollama 可留空）' });
  const keyState = h('p', { class: 'hint' }, settings.ai.key_configured ? '当前已配置（' + settings.ai.key_source + '）' : (settings.ai.configured ? '本地接口，无需 Key' : '尚未配置 Key'));
  const temp = h('input', { class: 'input', type: 'number', step: '0.1', min: '0', max: '2', value: String(settings.ai.temperature) });
  const maxContext = h('input', { class: 'input', type: 'number', step: '1000', min: '4000', max: '200000', value: String(settings.ai.max_context_chars) });
  const testBtn = h('button', { class: 'btn btn-ghost' }, '测试连接');
  const saveAi = h('button', { class: 'btn btn-primary' }, '保存 AI 设置');
  const clearKey = h('button', { class: 'btn btn-danger btn-sm' }, '清除 Key');
  aiPanel.appendChild(h('div', { class: 'field', style: 'margin-bottom:12px' }, h('label', null, '预设'), preset));
  aiPanel.appendChild(h('div', { class: 'form-grid' },
    h('div', { class: 'field full' }, h('label', null, 'Base URL'), baseUrl),
    h('div', { class: 'field' }, h('label', null, '模型'), model),
    h('div', { class: 'field' }, h('label', null, 'API Key'), key),
    h('div', { class: 'field' }, h('label', null, '温度（0–2）'), temp),
    h('div', { class: 'field' }, h('label', null, '上下文上限（字符）'), maxContext)
  ));
  aiPanel.appendChild(keyState);
  const aiActions = h('div', { class: 'settings-actions' }, saveAi, testBtn, clearKey);
  aiPanel.appendChild(aiActions);
  grid.appendChild(aiPanel);

  const crawlerPanel = h('section', { class: 'panel panel-body settings-section' });
  crawlerPanel.appendChild(h('h2', null, '抓取设置'));
  crawlerPanel.appendChild(h('p', null, '只从 Bangumi 抓取吐槽与评论。评论上限越大，任务越久。'));
  const reviewLimit = h('input', { class: 'input', type: 'number', min: '1', max: '500', value: String(settings.crawler.review_limit) });
  const interval = h('input', { class: 'input', type: 'number', min: '0.5', max: '10', step: '0.1', value: String(settings.crawler.request_interval) });
  const nsfw = h('input', { type: 'checkbox', checked: Boolean(settings.crawler.allow_nsfw) });
  crawlerPanel.appendChild(h('div', { class: 'form-grid' },
    h('div', { class: 'field' }, h('label', null, '每来源评论上限'), reviewLimit),
    h('div', { class: 'field' }, h('label', null, '请求间隔（秒）'), interval)
  ));
  crawlerPanel.appendChild(h('label', { class: 'check', style: 'margin-top:12px' }, nsfw, '包含 R18 / NSFW 内容（搜索和导入时生效）'));
  const proxy = h('input', { class: 'input', value: settings.network.proxy || '', placeholder: 'http://127.0.0.1:7890（可选）' });
  crawlerPanel.appendChild(h('div', { class: 'field', style: 'margin-top:14px' }, h('label', null, 'HTTP/HTTPS 代理（抓取与 AI 共用）'), proxy));
  const saveCrawler = h('button', { class: 'btn btn-primary', style: 'margin-top:16px' }, '保存抓取设置');
  crawlerPanel.appendChild(saveCrawler);
  grid.appendChild(crawlerPanel);

  const bangumiPanel = h('section', { class: 'panel panel-body settings-section' });
  bangumiPanel.appendChild(h('h2', null, 'Bangumi 授权（R18 必需）'));
  bangumiPanel.appendChild(h('p', null, 'Bangumi 只向已授权用户返回 R18 条目。请在 Bangumi 创建 Access Token 后填入；Cookie 用于抓取 R18 的吐槽和长评。'));
  const tokenInput = h('input', { class: 'input', type: 'password', placeholder: settings.bangumi.token_configured ? '已保存：' + settings.bangumi.token_hint + '（输入新值可替换）' : 'Bangumi Access Token' });
  const cookieInput = h('textarea', { class: 'input', rows: '2', placeholder: settings.bangumi.cookie_configured ? '已保存 Cookie（输入新值可替换）' : 'Bangumi Cookie（可选，仅 R18 评论/长评需要）' });
  const bangumiState = h('p', { class: 'hint' }, (settings.bangumi.token_configured ? 'Token 已配置' : 'Token 未配置，R18 搜索结果会被 Bangumi 隐藏') + (settings.bangumi.cookie_configured ? '；Cookie 已配置' : ''));
  bangumiPanel.appendChild(h('div', { class: 'field', style: 'margin-bottom:12px' }, h('label', null, 'Access Token'), tokenInput));
  bangumiPanel.appendChild(h('div', { class: 'field' }, h('label', null, 'Cookie'), cookieInput));
  bangumiPanel.appendChild(h('p', { class: 'hint', style: 'margin-top:8px' }, 'Access Token 申请地址：', h('a', { href: 'https://next.bgm.tv/demo/access-token', target: '_blank', rel: 'noopener noreferrer' }, 'next.bgm.tv/demo/access-token'), '。Cookie 可从已登录 Bangumi 的浏览器开发者工具中复制。'));
  bangumiPanel.appendChild(bangumiState);
  const saveBangumi = h('button', { class: 'btn btn-primary', style: 'margin-top:12px' }, '保存 Bangumi 凭据');
  const clearBangumi = h('button', { class: 'btn btn-danger btn-sm', style: 'margin-top:12px' }, '清除凭据');
  const checkBangumi = h('button', { class: 'btn btn-ghost btn-sm', style: 'margin-top:12px' }, '检查连接');
  const checkResult = h('div', { class: 'check-results', hidden: true });
  bangumiPanel.appendChild(h('div', { class: 'settings-actions' }, saveBangumi, clearBangumi, checkBangumi));
  bangumiPanel.appendChild(checkResult);
  grid.appendChild(bangumiPanel);

  const dataPanel = h('section', { class: 'panel panel-body settings-section' });
  dataPanel.appendChild(h('h2', null, '数据与说明'));
  const counts = state.bootstrap.counts || {};
  dataPanel.appendChild(h('div', { class: 'kv' }, h('span', null, '作品 / 评论'), h('strong', null, (counts.works || 0) + ' / ' + (counts.reviews || 0))));
  dataPanel.appendChild(h('div', { class: 'kv' }, h('span', null, '数据库'), h('strong', { class: 'tiny' }, 'data/app.db')));
  dataPanel.appendChild(h('a', { class: 'btn btn-ghost btn-sm', href: '/api/export', style: 'margin-top:14px' }, '导出 JSON（不含 Key）'));
  const clearAll = h('button', { class: 'btn btn-danger btn-sm', style: 'margin-top:14px;margin-left:8px' }, '一键清除全部凭据');
  dataPanel.appendChild(clearAll);
  dataPanel.appendChild(h('p', { class: 'hint', style: 'margin-top:12px' }, '演示数据为虚构作品与合成评论，仅用于离线验收。要清除演示数据，请停止服务后运行：python app.py --clear-demo'));
  dataPanel.appendChild(h('p', { class: 'hint' }, '发送给 AI 的只有评论正文与作品元数据，不包含作者昵称；请选择可信的 AI 服务商。评论版权归原作者，保留原站链接，仅作个人研究使用。'));
  grid.appendChild(dataPanel);

  const aboutPanel = h('section', { class: 'panel panel-body settings-section' });
  aboutPanel.appendChild(h('h2', null, '关于'));
  aboutPanel.appendChild(h('div', { class: 'prose' },
    h('p', null, 'ACG Radar 是一个本地优先的评论分析工具：先抓评论，再让 AI 基于评论回答。'),
    h('p', null, '数据来源：Bangumi。抓取公开条目页面的吐槽与评论，按作品导入，不绕过登录或验证码。'),
    h('p', null, '版本 ' + state.bootstrap.version + ' · 仅监听 127.0.0.1')
  ));
  grid.appendChild(aboutPanel);

  preset.addEventListener('change', function () {
    const selected = PRESETS[preset.value];
    if (selected) { baseUrl.value = selected.base_url; model.value = selected.model; }
  });

  async function saveAiSettings(showToast) {
    saveAi.disabled = true;
    saveAi.textContent = '保存中…';
    try {
      const body = {
        ai: {
          base_url: baseUrl.value.trim(),
          model: model.value.trim(),
          temperature: Number(temp.value || 0.4),
          max_context_chars: Number(maxContext.value || 30000),
        },
      };
      if (key.value.trim()) body.api_key = key.value.trim();
      const data = await api('/api/settings', { method: 'POST', body: body });
      state.bootstrap.settings = data;
      key.value = '';
      keyState.textContent = data.ai.key_configured ? '当前已配置（' + data.ai.key_source + '）' : '尚未配置 Key';
      if (showToast) toast('AI 设置已保存', 'good');
      return true;
    } catch (err) {
      toast(apiErrorText(err), 'bad');
      return false;
    } finally {
      saveAi.disabled = false;
      saveAi.textContent = '保存 AI 设置';
    }
  }

  saveAi.addEventListener('click', function () { saveAiSettings(true); });
  testBtn.addEventListener('click', async function () {
    testBtn.disabled = true;
    testBtn.textContent = '测试中…';
    try {
      const ok = await saveAiSettings(false);
      if (!ok) return;
      const result = await api('/api/ai/test', { method: 'POST', body: {} });
      toast('连接成功：' + (result.model || '') + ' · ' + (result.message || 'OK'), 'good');
    } catch (err) {
      toast(apiErrorText(err), 'bad');
    } finally {
      testBtn.disabled = false;
      testBtn.textContent = '测试连接';
    }
  });
  clearKey.addEventListener('click', async function () {
    try {
      const data = await api('/api/settings', { method: 'POST', body: { clear_key: true } });
      state.bootstrap.settings = data;
      keyState.textContent = '尚未配置 Key';
      toast('Key 已清除', 'good');
    } catch (err) { toast(apiErrorText(err), 'bad'); }
  });

  saveBangumi.addEventListener('click', async function () {
    const body = {};
    if (tokenInput.value.trim()) body.bangumi_token = tokenInput.value.trim();
    if (cookieInput.value.trim()) body.bangumi_cookie = cookieInput.value.trim();
    if (!Object.keys(body).length) { toast('请先填写 Token 或 Cookie', 'bad'); return; }
    saveBangumi.disabled = true;
    saveBangumi.textContent = '保存中…';
    try {
      const data = await api('/api/settings', { method: 'POST', body: body });
      state.bootstrap.settings = data;
      tokenInput.value = '';
      cookieInput.value = '';
      bangumiState.textContent = (data.bangumi.token_configured ? 'Token 已配置' : 'Token 未配置，R18 搜索结果会被 Bangumi 隐藏') + (data.bangumi.cookie_configured ? '；Cookie 已配置' : '');
      toast('Bangumi 凭据已保存', 'good');
    } catch (err) {
      toast(apiErrorText(err), 'bad');
    } finally {
      saveBangumi.disabled = false;
      saveBangumi.textContent = '保存 Bangumi 凭据';
    }
  });

  clearAll.addEventListener('click', async function () {
    try {
      const data = await api('/api/settings', { method: 'POST', body: { clear_key: true, clear_bangumi_token: true, clear_bangumi_cookie: true } });
      state.bootstrap.settings = data;
      key.value = '';
      keyState.textContent = '尚未配置 Key';
      tokenInput.value = '';
      cookieInput.value = '';
      tokenInput.placeholder = 'Bangumi Access Token';
      cookieInput.placeholder = 'Bangumi Cookie（可选，仅 R18 评论/长评需要）';
      bangumiState.textContent = 'Token 未配置，R18 搜索结果会被 Bangumi 隐藏';
      toast('已清除 API Key、Bangumi Token 和 Cookie', 'good');
    } catch (err) { toast(apiErrorText(err), 'bad'); }
  });

  checkBangumi.addEventListener('click', async function () {
    checkBangumi.disabled = true;
    checkBangumi.textContent = '检查中…';
    checkResult.hidden = false;
    clear(checkResult);
    checkResult.appendChild(h('p', { class: 'hint' }, '正在检查 Bangumi API、R18 API 和 R18 评论页…'));
    try {
      const data = await api('/api/bangumi/check', { method: 'POST', body: {} });
      clear(checkResult);
      function row(label, item) {
        const ok = Boolean(item && item.ok);
        return h('div', { class: 'check-row' },
          h('span', { class: 'check-label' }, label),
          h('span', { class: 'check-state ' + (ok ? 'ok' : 'bad') }, ok ? '正常' : '异常'),
          h('span', { class: 'check-message' }, (item && item.message ? item.message : '') + (item && item.latency_ms ? ' · ' + item.latency_ms + 'ms' : ''))
        );
      }
      checkResult.appendChild(row('Bangumi API', data.api));
      checkResult.appendChild(row('R18 API', data.r18_api));
      checkResult.appendChild(row('R18 评论页', data.r18_html));
      checkResult.appendChild(h('p', { class: 'hint' }, data.overall ? '全部检查通过。' : '存在异常；上方会说明是 Token、Cookie 还是网络问题。'));
    } catch (err) {
      clear(checkResult);
      checkResult.appendChild(h('div', { class: 'banner error' }, apiErrorText(err)));
    } finally {
      checkBangumi.disabled = false;
      checkBangumi.textContent = '检查连接';
    }
  });

  clearBangumi.addEventListener('click', async function () {
    try {
      const data = await api('/api/settings', { method: 'POST', body: { clear_bangumi_token: true, clear_bangumi_cookie: true } });
      state.bootstrap.settings = data;
      tokenInput.value = '';
      cookieInput.value = '';
      tokenInput.placeholder = 'Bangumi Access Token';
      cookieInput.placeholder = 'Bangumi Cookie（可选，仅 R18 评论/长评需要）';
      bangumiState.textContent = 'Token 未配置，R18 搜索结果会被 Bangumi 隐藏';
      toast('Bangumi 凭据已清除', 'good');
    } catch (err) { toast(apiErrorText(err), 'bad'); }
  });

  saveCrawler.addEventListener('click', async function () {
    saveCrawler.disabled = true;
    saveCrawler.textContent = '保存中…';
    try {
      const body = {
        crawler: {
          review_limit: Number(reviewLimit.value || 120),
          request_interval: Number(interval.value || 1),
          allow_nsfw: nsfw.checked,
        },
        network: { proxy: proxy.value.trim() },
      };
      const data = await api('/api/settings', { method: 'POST', body: body });
      state.bootstrap.settings = data;
      await loadFacets();
      toast('抓取设置已保存', 'good');
    } catch (err) { toast(apiErrorText(err), 'bad'); }
    finally {
      saveCrawler.disabled = false;
      saveCrawler.textContent = '保存抓取设置';
    }
  });
}

bootstrap();

