const boardElement = document.querySelector('#board');
const rankLabels = document.querySelector('#rank-labels');
const fileLabels = document.querySelector('#file-labels');
const movesElement = document.querySelector('#moves');
const summaryElement = document.querySelector('#summary');
const backButton = document.querySelector('#back');
const flipButton = document.querySelector('#flip');
const variationElement = document.querySelector('#variation-list');
const apiBase = (window.OPENINGS_API_BASE || '').replace(/\/$/, '');
const assetBase = (window.OPENINGS_ASSET_BASE || '').replace(/\/$/, '');
const requestCache = new Map();
const path = [];
const variation = [];
let current = null, flipped = false, displayedBoard = null;
const name = { 1: 'rock', 2: 'paper', 3: 'scissors' };
const icon = { 1: 'rock', 2: 'paper', 3: 'scissors' };

function formatRating(ratings) { return ratings?.games ? `Avg ${Math.round(ratings.average)}` : 'Avg -'; }
function formatUpdated(value) {
  if (!value) return '-';
  const date = new Date(value), pad = number => String(number).padStart(2, '0');
  return `${pad(date.getUTCDate())}/${pad(date.getUTCMonth() + 1)}/${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} (UTC)`;
}
function apiUrl(path) { return apiBase ? `${apiBase}${path}` : path; }
function assetUrl(path) { return assetBase ? `${assetBase}${path}` : path; }
function request(path) {
  const url = apiUrl(path);
  if (requestCache.has(url)) return requestCache.get(url);
  const pending = fetch(url).then(async response => {
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(data?.message || data?.error || `Request failed (${response.status}).`);
    if (!data) throw new Error('API returned an invalid response.');
    return data;
  });
  requestCache.set(url, pending);
  if (requestCache.size > 180) requestCache.delete(requestCache.keys().next().value);
  pending.catch(() => { if (requestCache.get(url) === pending) requestCache.delete(url); });
  return pending;
}
function renderBoard(board) {
  displayedBoard = board; boardElement.innerHTML = '';
  const ranks = flipped ? [...Array(9).keys()] : [...Array(9).keys()].reverse();
  const files = flipped ? [...Array(9).keys()].reverse() : [...Array(9).keys()];
  rankLabels.innerHTML = ranks.map(rank => `<span>${rank + 1}</span>`).join('');
  fileLabels.innerHTML = files.map(file => `<span>${String.fromCharCode(65 + file)}</span>`).join('');
  for (const rank of ranks) for (const file of files) {
    const index = rank * 9 + file, value = board[index], square = document.createElement('div');
    square.className = `square ${(rank + file) % 2 ? 'dark' : 'light'}${index === 0 ? ' goal-red' : ''}${index === 80 ? ' goal-blue' : ''}`;
    if (value) {
      const piece = document.createElement('img'); piece.className = `piece-icon ${value > 0 ? 'blue' : 'red'}`;
      piece.src = assetUrl(`/assets/${icon[Math.abs(value)]}.svg`); piece.alt = `${value > 0 ? 'Blue' : 'Red'} ${name[Math.abs(value)]}`;
      square.append(piece);
    }
    boardElement.append(square);
  }
}
function clearMoveArrow() { boardElement.querySelector('.move-arrow')?.remove(); }
function drawMoveArrow(move) {
  const point = square => ({ x: (flipped ? 8 - (square.charCodeAt(0) - 65) : square.charCodeAt(0) - 65) + .5, y: (flipped ? Number(square[1]) - 1 : 9 - Number(square[1])) + .5 });
  const source = point(move.source), target = point(move.target);
  const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  arrow.setAttribute('class', 'move-arrow'); arrow.setAttribute('viewBox', '0 0 9 9'); arrow.setAttribute('aria-hidden', 'true');
  arrow.innerHTML = '<defs><marker id="move-arrowhead" markerWidth="4" markerHeight="4" refX="7" refY="5" viewBox="0 0 10 10" orient="auto" markerUnits="strokeWidth"><path d="M0 0L10 5L0 10Z"/></marker></defs>';
  arrow.insertAdjacentHTML('beforeend', `<line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" marker-end="url(#move-arrowhead)"/>`);
  clearMoveArrow(); boardElement.append(arrow);
}
function renderMoves(data) {
  movesElement.innerHTML = '';
  if (!data.moves.length) { movesElement.textContent = 'No continuations.'; return; }
  data.moves.forEach(move => {
    const wdl = move.wdl, total = move.resolved, button = document.createElement('button');
    const blue = total ? wdl.blue * 100 / total : 0, draws = total ? wdl.draws * 100 / total : 0, red = total ? wdl.red * 100 / total : 0;
    const result = total ? `<span class="wdl" title="Blue ${wdl.blue} - Draw ${wdl.draws} - Red ${wdl.red}"><span>${wdl.blue} / ${wdl.draws} / ${wdl.red}</span><span class="wdl-bars"><i class="wdl-blue" style="width:${blue}%"></i><i class="wdl-draw" style="width:${draws}%"></i><i class="wdl-red" style="width:${red}%"></i></span></span>` : '';
    button.className = 'move'; button.innerHTML = `<span class="move-info"><strong>${move.move}</strong><small>${move.games} - ${move.share}%</small><small>${formatRating(move.ratings)}</small></span><span class="move-results">${result}<i>&rarr;</i></span>`;
    button.onclick = () => navigateToMove(move);
    button.onpointerenter = () => drawMoveArrow(move); button.onpointerleave = clearMoveArrow;
    button.onfocus = () => drawMoveArrow(move); button.onblur = clearMoveArrow;
    movesElement.append(button);
  });
}
function applyMove(board, move) {
  const index = square => (Number(square[1]) - 1) * 9 + square.charCodeAt(0) - 65;
  const next = [...board], source = index(move.source), target = index(move.target);
  next[target] = next[source]; next[source] = 0; return next;
}
function navigateToMove(move) {
  path.push(current); variation.push(move); if (displayedBoard) renderBoard(applyMove(displayedBoard, move));
  renderVariation(); document.querySelector('#turn').textContent = 'Loading position...'; movesElement.textContent = 'Loading...';
  loadOpening(move.next).catch(() => { movesElement.textContent = 'Could not load this position.'; });
}
function prefetchMoves(moves) {
  moves.slice(0, 2).forEach((move, index) => window.setTimeout(() => request(`/api/opening?position=${encodeURIComponent(move.next)}`).catch(() => {}), 200 + index * 100));
}
function renderVariation() {
  variationElement.innerHTML = '';
  if (!variation.length) { variationElement.innerHTML = '<span class="variation-empty">Start position</span>'; return; }
  for (let ply = 0; ply < variation.length; ply += 2) {
    const blueMove = variation[ply], redMove = variation[ply + 1], turn = document.createElement('div');
    turn.className = 'variation-turn'; turn.innerHTML = `<small>${ply / 2 + 1}</small><div class="variation-moves"><strong>${blueMove.move}</strong>${redMove ? `<span>...</span><strong>${redMove.move}</strong>` : ''}</div>`;
    variationElement.append(turn);
  }
}
async function loadOpening(position) {
  current = position; const data = await request(`/api/opening?position=${encodeURIComponent(position)}`);
  renderBoard(data.board);
  document.querySelector('#position-title').textContent = `${data.total} games`;
  document.querySelector('#turn').textContent = `${data.side === 1 ? 'Blue' : 'Red'} to move · ${formatRating(data.ratings)}`;
  renderMoves(data); renderVariation(); backButton.hidden = !path.length; prefetchMoves(data.moves);
}
async function loadRoots(data = null) {
  data ||= await request('/api/roots');
  document.querySelector('#position-title').textContent = 'Starting positions'; document.querySelector('#turn').textContent = ''; movesElement.innerHTML = '';
  if (data.roots.length) renderBoard(data.roots[0].board); else boardElement.innerHTML = '';
  data.roots.forEach(root => {
    const button = document.createElement('button'); button.className = 'root';
    button.innerHTML = `<span>Start <small>#${root.id}</small></span><strong>→ ${root.games}</strong>`;
    button.onclick = () => { path.length = 0; variation.length = 0; loadOpening(root.id); }; movesElement.append(button);
  });
  if (!data.roots.length) movesElement.textContent = 'No games yet.'; renderVariation();
}
async function refresh() {
  const roots = await request('/api/roots');
  summaryElement.textContent = `${roots.summary.games} games · last updated ${formatUpdated(roots.summary.last_updated)}`;
  await loadRoots(roots);
}
function showLoadError(error) {
  console.error(error); summaryElement.textContent = 'Database unavailable'; movesElement.textContent = error.message || 'Could not load openings.'; boardElement.innerHTML = '';
}
flipButton.onclick = () => { flipped = !flipped; if (displayedBoard) renderBoard(displayedBoard); };
backButton.onclick = () => { variation.pop(); loadOpening(path.pop()); };
refresh().catch(showLoadError);
