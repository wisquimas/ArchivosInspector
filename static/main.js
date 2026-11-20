import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { squarify } from "/treemap.js";

const COLORS = {
  video: "#3b82f6",
  image: "#22c55e",
  audio: "#a855f7",
  document: "#eab308",
  code: "#14b8a6",
  archive: "#f97316",
  executable: "#ef4444",
  other: "#64748b",
  folder: "#7f8ea1",
  aggregate: "#8b95a3",
  dup: "#ffd166",
};

const LABELS = {
  video: "Video",
  image: "Imagen",
  audio: "Audio",
  document: "Documento",
  code: "Código",
  archive: "Comprimido",
  executable: "Ejecutable",
  other: "Otro",
  folder: "Carpeta",
};

const WORLD = 100;
const STREET = 0.78;
const CITY_MIN = 1.1;
const PLATFORM_H = 0.22;
const PLATFORM_COLOR = "#5e7082";

const state = {
  result: null,
  current: null,
  selected: null,
  filter: { kind: "all", days: 365, q: "" },
  height: "age",
  view: "orbit",
  curve: { min: 0.45, max: 14.45, linearity: 0, horizon: 2920 },
  mapTree: null,
  byId: new Map(),
};

const pathInput = document.querySelector("#path");
const scanBtn = document.querySelector("#scanBtn");
const demoBtn = document.querySelector("#demoBtn");
const cancelBtn = document.querySelector("#cancelBtn");
const statusEl = document.querySelector("#status");
const quickEl = document.querySelector("#quick");
const dirsEl = document.querySelector("#dirs");
const browseUp = document.querySelector("#browseUp");
const summaryEl = document.querySelector("#summary");
const listEl = document.querySelector("#list");
const topEl = document.querySelector("#top");
const stage = document.querySelector("#stage");
const canvas = document.querySelector("#view");
const crumbEl = document.querySelector("#crumb");
const tooltip = document.querySelector("#tooltip");
const crosshair = document.querySelector("#crosshair");
const hint = document.querySelector("#hint");
const propsEl = document.querySelector("#props");
const actionsEl = document.querySelector("#actions");
const map = document.querySelector("#map");
const legendEl = document.querySelector("#legend");
const diskEl = document.querySelector("#disk");
const noteEl = document.querySelector("#note");
const treeEl = document.querySelector("#tree");
const ageLabel = document.querySelector("#ageLabel");
const ageDays = document.querySelector("#ageDays");
const queryInput = document.querySelector("#query");
const curveBtn = document.querySelector("#curveBtn");
const curvePanel = document.querySelector("#curvePanel");
const curveClose = document.querySelector("#curveClose");
const curveMin = document.querySelector("#curveMin");
const curveMax = document.querySelector("#curveMax");
const curveLine = document.querySelector("#curveLine");
const curveLineNum = document.querySelector("#curveLineNum");
const curveHorizon = document.querySelector("#curveHorizon");
const curveErrorEl = document.querySelector("#curveError");
const curveModeNote = document.querySelector("#curveModeNote");
const curveReset = document.querySelector("#curveReset");
const mapCtx = map.getContext("2d");

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1318);
scene.fog = new THREE.Fog(0x0e1318, 90, 240);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 800);
camera.position.set(128, 92, 128);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(50, 0, 50);
controls.maxPolarAngle = Math.PI / 2.08;
controls.minDistance = 4;
controls.maxDistance = 260;
controls.keyPanSpeed = 16;
controls.listenToKeyEvents(canvas);
canvas.tabIndex = 0;
controls.update();

scene.add(new THREE.AmbientLight(0xffffff, 0.62));
const sun = new THREE.DirectionalLight(0xfff4e0, 1.25);
sun.position.set(30, 80, 20);
scene.add(sun);
const fill = new THREE.DirectionalLight(0x9db4ff, 0.38);
fill.position.set(-40, 30, -20);
scene.add(fill);

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(220, 220),
  new THREE.MeshLambertMaterial({ color: 0x121920 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.set(50, -0.05, 50);
scene.add(ground);
const grid = new THREE.GridHelper(120, 24, 0x314052, 0x1c2733);
grid.position.set(50, 0, 50);
scene.add(grid);

const boxGeo = new THREE.BoxGeometry(1, 1, 1);
const city = new THREE.Group();
scene.add(city);
const highlight = new THREE.LineSegments(
  new THREE.EdgesGeometry(boxGeo),
  new THREE.LineBasicMaterial({ color: 0xffe7a3 })
);
highlight.visible = false;
scene.add(highlight);

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let footprints = [];
let pollTimer = 0;
let browsePath = "";

const player = { x: 50, z: 108, yaw: 0, pitch: -0.12, fly: 0 };
const keys = {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let value = n / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = value >= 10 ? 1 : 2;
  return `${value.toLocaleString("es-MX", { maximumFractionDigits: digits })} ${units[index]}`;
}

function formatDate(mtime) {
  if (!mtime) return "sin fecha";
  return new Date(mtime * 1000).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function formatCount(n) {
  return Number(n || 0).toLocaleString("es-MX");
}

async function readJson(response) {
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text || response.statusText };
  }
  if (!response.ok) throw new Error(data.error || "Error del servidor");
  return data;
}

function typingTarget() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function setPressed(container, attr, value) {
  for (const button of container.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset[attr] === value));
  }
}

function indexTree(node) {
  state.byId.set(node.id, node);
  for (const child of node.children || []) indexTree(child);
}

function linkParents(node, parent) {
  node.parent = parent || null;
  for (const child of node.children || []) linkParents(child, node);
}

function chain(node) {
  const out = [];
  let current = node;
  while (current) {
    out.push(current);
    current = current.parent;
  }
  return out.reverse();
}

function matchesFile(node, forceName) {
  if (node.dir) return false;
  const query = state.filter.q;
  if (query && !forceName && !node.name.toLowerCase().includes(query)) return false;
  const kind = state.filter.kind;
  if (kind === "all") return true;
  if (kind === "dup") return Boolean(node.dup);
  if (kind === "old") {
    if (!node.mtime) return false;
    return (Date.now() / 1000 - node.mtime) / 86400 >= state.filter.days;
  }
  return node.category === kind;
}

function layoutSize(node, forceName = false) {
  const query = state.filter.q;
  const nameForce = forceName || Boolean(query && node.name.toLowerCase().includes(query));
  if (!node.dir) return matchesFile(node, nameForce) ? node.size : 0;
  let sum = 0;
  for (const child of node.children || []) sum += layoutSize(child, nameForce);
  if (sum > 0) return sum;
  if (state.filter.kind === "all" && !query) return node.size;
  if (nameForce && state.filter.kind === "all") return node.size;
  return 0;
}

function visibleEntries(node) {
  const entries = [];
  for (const child of node.children || []) {
    const size = layoutSize(child);
    if (size > 0) entries.push({ node: child, size });
  }
  return entries;
}

function colorFor(node) {
  if (!node) return COLORS.other;
  if (node.aggregate) return COLORS.aggregate;
  if (node.dup) return COLORS.dup;
  if (node.dir) return COLORS.folder;
  return COLORS[node.category] || COLORS.other;
}

function descendantCount(node) {
  if (node._desc != null) return node._desc;
  let count;
  if (!node.dir || !node.children?.length) count = Math.max(1, node.count || 1);
  else {
    count = 0;
    for (const child of node.children) count += descendantCount(child);
    if (count <= 0) count = Math.max(1, node.count || 1);
  }
  node._desc = count;
  return count;
}

function oldestFileTime(node) {
  if (node._oldest !== undefined) return node._oldest;
  let oldest = 0;
  const consider = (stamp) => {
    if (stamp > 0 && (oldest === 0 || stamp < oldest)) oldest = stamp;
  };
  if (!node.dir) consider(node.mtime);
  else if (!node.children?.length) consider(node.oldest);
  else for (const child of node.children) consider(oldestFileTime(child));
  if (!oldest) consider(node.oldest || node.mtime);
  node._oldest = oldest || 0;
  return node._oldest;
}

function blockHeight(node) {
  if (state.height === "flat") return node.dir ? 0.8 : 1.2;
  if (state.height === "count") {
    if (!node.dir) return 1.15;
    const count = descendantCount(node);
    return 0.6 + Math.min(12, Math.log10(count + 1) * 3.4);
  }
  const stamp = node.dir ? oldestFileTime(node) : node.mtime;
  if (!stamp) return node.dir ? 0.55 : 1;
  const days = Math.max(0, (Date.now() / 1000 - stamp) / 86400);
  const { min, max, linearity, horizon } = state.curve;
  const tLog = Math.min(1, Math.log10(days + 1) / Math.log10(horizon + 1));
  const tLine = Math.min(1, days / horizon);
  const t = (1 - linearity) * tLog + linearity * tLine;
  return min + t * (max - min);
}

const CURVE_STORAGE_KEY = "archivosInspector.ageCurve";
const CURVE_DEFAULTS = { min: 0.45, max: 14.45, linearity: 0, horizon: 2920 };

function curveProblems(curve) {
  if (!curve || ![curve.min, curve.max, curve.linearity, curve.horizon].every(Number.isFinite)) {
    return ["Todos los campos tienen que ser números."];
  }
  const problems = [];
  if (!(curve.max > curve.min)) problems.push("La altura máxima tiene que ser mayor que la mínima.");
  if (curve.horizon < 1) problems.push("El horizonte tiene que ser de al menos 1 día.");
  if (curve.linearity < 0 || curve.linearity > 1) problems.push("La linealidad tiene que estar entre 0 y 1.");
  return problems;
}

function loadCurve() {
  try {
    const raw = localStorage.getItem(CURVE_STORAGE_KEY);
    if (!raw) return { ...CURVE_DEFAULTS };
    const data = JSON.parse(raw);
    const curve = {
      min: Number(data.min),
      max: Number(data.max),
      linearity: Number(data.linearity),
      horizon: Number(data.horizon),
    };
    if (curveProblems(curve).length) return { ...CURVE_DEFAULTS };
    return curve;
  } catch {
    return { ...CURVE_DEFAULTS };
  }
}

function saveCurve(curve) {
  try {
    localStorage.setItem(CURVE_STORAGE_KEY, JSON.stringify({
      min: curve.min,
      max: curve.max,
      linearity: curve.linearity,
      horizon: curve.horizon,
    }));
  } catch {
    /* la sesión conserva la curva aunque el navegador bloquee el almacenamiento */
  }
}

function fillCurveForm(curve) {
  curveMin.value = String(curve.min);
  curveMax.value = String(curve.max);
  curveLine.value = String(curve.linearity);
  curveLineNum.value = String(curve.linearity);
  curveHorizon.value = String(curve.horizon);
  curveErrorEl.hidden = true;
  curveErrorEl.textContent = "";
}

function updateCurveModeNote() {
  curveModeNote.textContent = state.height === "age"
    ? "Activa en esta vista: más alto significa más antiguo."
    : "Esta curva aplica al modo antigüedad. Ahora la altura usa otro modo; al volver a antigüedad se usan estos valores.";
}

function draftCurve() {
  return {
    min: Number(curveMin.value),
    max: Number(curveMax.value),
    linearity: Number(curveLineNum.value),
    horizon: Number(curveHorizon.value),
  };
}

function sameCurve(a, b) {
  return a.min === b.min && a.max === b.max && a.linearity === b.linearity && a.horizon === b.horizon;
}

function commitCurveDraft() {
  const draft = draftCurve();
  const problems = curveProblems(draft);
  if (problems.length) {
    curveErrorEl.hidden = false;
    curveErrorEl.textContent = problems.join(" ");
    return;
  }
  curveErrorEl.hidden = true;
  curveErrorEl.textContent = "";
  if (sameCurve(state.curve, draft)) return;
  state.curve = draft;
  saveCurve(draft);
  if (state.result && state.height === "age") render({ frame: false });
}

function setCurvePanel(open) {
  curvePanel.hidden = !open;
  curveBtn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    updateCurveModeNote();
    curveMin.focus();
  }
}

function resetCurve() {
  const next = { ...CURVE_DEFAULTS };
  fillCurveForm(next);
  const changed = !sameCurve(state.curve, next);
  state.curve = next;
  saveCurve(next);
  if (changed && state.result && state.height === "age") render({ frame: false });
}

function styleKey(node) {
  if (node.aggregate) return "aggregate";
  if (node.dup) return "dup";
  if (node.dir) return "folder";
  return node.category || "other";
}

function applyResult(result) {
  linkParents(result.root, null);
  state.byId = new Map();
  indexTree(result.root);
  state.result = result;
  state.current = result.root;
  state.selected = null;
  resetTree();
  noteEl.textContent = result.note || "";
  diskEl.textContent = result.disk
    ? `Disco: ${formatBytes(result.disk.free)} libres de ${formatBytes(result.disk.total)}.`
    : "";
  if (!result.demo && result.root_path) {
    pathInput.value = result.root_path;
    loadBrowse(result.root_path);
  }
  const where = result.demo ? "Ejemplo cargado." : `Listo: ${result.root_path}.`;
  statusEl.textContent = `${where} ${formatBytes(result.bytes)} en ${formatCount(result.files_seen)} archivos.`;
  render({ frame: true });
}

function setBusy(busy) {
  scanBtn.disabled = busy;
  demoBtn.disabled = busy;
  cancelBtn.disabled = !busy;
}

async function loadBrowse(path) {
  const data = await readJson(await fetch(`/api/browse?path=${encodeURIComponent(path || "")}`));
  browsePath = data.path;
  if (!state.result || state.result.demo) pathInput.value = data.path;
  browseUp.disabled = !data.parent;
  browseUp.dataset.path = data.parent || "";
  quickEl.innerHTML = data.shortcuts
    .map((item) => `<button type="button" data-path="${esc(item.path)}">${esc(item.label)}</button>`)
    .concat(data.drives.map((drive) => `<button type="button" data-path="${esc(drive)}">${esc(drive)}</button>`))
    .join("");
  const extra = data.truncated ? `<p class="muted">Hay más carpetas; se muestran 500.</p>` : "";
  dirsEl.innerHTML =
    data.dirs
      .map((dir) => `<button class="dirbtn" type="button" data-path="${esc(dir.path)}">${esc(dir.name)}</button>`)
      .join("") + extra;
}

async function scanPath(path) {
  setBusy(true);
  statusEl.textContent = "Analizando…";
  try {
    await readJson(await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }));
  } catch (error) {
    setBusy(false);
    statusEl.textContent = error.message;
    return;
  }
  poll();
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    const scan = await readJson(await fetch("/api/scan"));
    if (scan.state === "running") {
      const where = scan.current ? ` · ${scan.current}` : "";
      statusEl.textContent = `Analizando ${formatCount(scan.files)} archivos, ${formatBytes(scan.bytes)}${where}`;
      pollTimer = setTimeout(poll, 400);
      return;
    }
    setBusy(false);
    if (scan.state === "done") {
      applyResult(await readJson(await fetch("/api/result")));
      return;
    }
    if (scan.state === "cancelled") statusEl.textContent = "Análisis cancelado.";
    else if (scan.state === "error") statusEl.textContent = scan.error || "No se pudo analizar la carpeta.";
  } catch (error) {
    setBusy(false);
    statusEl.textContent = error.message;
  }
}

function focusNode(node) {
  if (!node || !state.result) return;
  if (node === state.current) {
    state.selected = null;
    render({ frame: true });
    return;
  }
  if (node.dir) {
    state.current = node.parent || node;
    state.selected = node.parent ? node : null;
  } else {
    state.current = node.parent || state.result.root;
    state.selected = node;
  }
  render({ frame: true });
}

function enter(node) {
  if (!node?.dir) return;
  if (node.collapsed && node.path) {
    scanPath(node.path);
    return;
  }
  state.current = node;
  state.selected = null;
  render({ frame: true });
}

function ascend() {
  const parent = state.current?.parent;
  if (!parent) return;
  const leaving = state.current;
  state.current = parent;
  state.selected = leaving;
  render({ frame: true });
}

async function openNode(node, reveal) {
  if (!node?.path) {
    statusEl.textContent = "Este bloque no apunta a un archivo real.";
    return;
  }
  try {
    await readJson(await fetch(reveal ? "/api/reveal" : "/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: node.path }),
    }));
    statusEl.textContent = reveal ? "Abierto en el Explorador." : "Archivo abierto.";
  } catch (error) {
    statusEl.textContent = error.message;
  }
}

function collectFiles(node, out) {
  if (!node.dir && !node.aggregate && node.size > 0) out.push(node);
  for (const child of node.children || []) collectFiles(child, out);
}

const treeOpen = new Set();
let treeFollow = null;

function resetTree() {
  treeOpen.clear();
  treeFollow = null;
}

function treeBranch(node) {
  return (node.children || []).filter((child) => child.dir || child.aggregate);
}

function syncTreeToCurrent() {
  if (!state.current) return;
  const changed = treeFollow !== state.current.id;
  let node = state.current.parent;
  while (node) {
    treeOpen.add(node.id);
    node = node.parent;
  }
  if (changed) {
    treeOpen.add(state.current.id);
    treeFollow = state.current.id;
  }
}

function drawTree() {
  if (!state.result || !state.current) {
    treeEl.innerHTML = `<p class="muted">Todavía no hay un análisis.</p>`;
    return;
  }
  const changed = treeFollow !== state.current.id;
  syncTreeToCurrent();
  treeEl.innerHTML = renderTreeNode(state.result.root);
  if (changed) {
    treeEl.querySelector(".tree-current > .tree-row > .tree-name")?.scrollIntoView({ block: "nearest" });
  }
}

function renderTreeNode(node) {
  const kids = node.dir ? treeBranch(node) : [];
  const expandable = kids.length > 0;
  const open = expandable && treeOpen.has(node.id);
  const current = node === state.current;
  const classes = ["tree-item"];
  if (current) classes.push("tree-current");
  if (node === state.selected && !current) classes.push("tree-selected");
  if (node.aggregate) classes.push("tree-aggregate");
  const twist = expandable
    ? `<button type="button" class="tree-twist" data-twist="${node.id}" aria-expanded="${open}" aria-label="${open ? "Contraer" : "Expandir"} ${esc(node.name)}">${open ? "▾" : "▸"}</button>`
    : `<span class="tree-twist" aria-hidden="true"></span>`;
  const kidsHtml = open
    ? `<div class="tree-kids" role="group">${kids.map(renderTreeNode).join("")}</div>`
    : "";
  return `<div class="${classes.join(" ")}" role="treeitem" aria-expanded="${expandable ? String(open) : "false"}"${current ? ' aria-current="true"' : ""}>
    <div class="tree-row">
      ${twist}
      <button type="button" class="tree-name" data-tree="${node.id}" title="${esc(node.name)}">${esc(node.name)}</button>
    </div>
    ${kidsHtml}
  </div>`;
}

function render(options = {}) {
  if (!state.current) return;
  const entries = visibleEntries(state.current);
  drawCrumb();
  drawTree();
  drawList(entries);
  drawTop();
  drawProps();
  drawLegend();
  buildCity(entries);
  drawMap();
  if (options.frame) {
    if (state.selected?._box && state.view !== "walk") frameSelection();
    else frameCity();
  }
  placeHighlight();
}

function drawCrumb() {
  crumbEl.innerHTML = chain(state.current)
    .map((node) => `<button type="button" data-id="${node.id}">${esc(node.name)}</button>`)
    .join("");
}

function drawList(entries) {
  const total = layoutSize(state.current) || state.current.size || 1;
  const rows = (state.current.children || [])
    .filter((node) => {
      if (layoutSize(node) > 0) return true;
      if (state.filter.kind === "all" && !state.filter.q) return true;
      return Boolean(state.filter.q && node.name.toLowerCase().includes(state.filter.q));
    })
    .sort((a, b) => b.size - a.size);
  const shown = rows.slice(0, 300);
  summaryEl.textContent = `${shown.length === rows.length ? rows.length : `${shown.length} de ${rows.length}`} elementos · ${formatBytes(total)} visibles · ${formatCount(state.current.count)} archivos en esta rama.`;
  listEl.innerHTML = shown
    .map((node) => {
      const pct = total ? (layoutSize(node) || node.size) / total * 100 : 0;
      const action = node.dir
        ? `<button type="button" data-enter="${node.id}">${node.collapsed ? "Analizar" : "Entrar"}</button>`
        : "";
      return `<div class="row${state.selected === node ? " selected" : ""}" data-id="${node.id}">
        <span><b>${esc(node.name)}</b><span class="sub">${esc(node.dir ? "Carpeta" : LABELS[node.category] || "Archivo")}${node.dup ? " · duplicado" : ""}</span></span>
        <span class="size">${formatBytes(node.size)} · ${pct.toFixed(1)}%${action ? `<br>${action}` : ""}</span>
      </div>`;
    })
    .join("");
  const selectedRow = listEl.querySelector(".row.selected");
  selectedRow?.scrollIntoView({ block: "nearest" });
}

function drawTop() {
  const files = [];
  collectFiles(state.result.root, files);
  files.sort((a, b) => b.size - a.size);
  topEl.innerHTML = files.slice(0, 12).map((node) => (
    `<button class="toprow" type="button" data-id="${node.id}"><b>${esc(node.name)}</b><span class="size">${formatBytes(node.size)}</span></button>`
  )).join("");
}

function drawProps() {
  const node = state.selected;
  if (!node) {
    propsEl.innerHTML = "<p class='muted'>Seleccioná un bloque del mapa, de la lista o de la ciudad.</p>";
    actionsEl.innerHTML = state.current?.parent ? `<button type="button" id="upBtn">Subir</button>` : "";
    return;
  }
  const parentSize = node.parent?.size || state.current.size || node.size || 1;
  const pct = parentSize ? (node.size / parentSize) * 100 : 100;
  const kind = node.aggregate
    ? "Grupo de archivos chicos"
    : node.dir
      ? (node.collapsed ? "Carpeta (contenido sin abrir)" : "Carpeta")
      : (LABELS[node.category] || "Archivo");
  const dup = node.dup
    ? `<p><strong>Duplicado:</strong> ${node.dup_count} copias por ${esc(node.dup_kind || "coincidencia")}.</p>`
    : "";
  const oldest = node.dir ? oldestFileTime(node) : 0;
  const oldLine = node.dir && oldest && Math.abs(oldest - (node.mtime || 0)) > 1
    ? `<p><strong>Archivo más antiguo:</strong> ${formatDate(oldest)}</p>`
    : "";
  propsEl.innerHTML = `
    <p><strong>Nombre:</strong> ${esc(node.name)}</p>
    <p><strong>Tipo:</strong> ${esc(kind)}</p>
    <p><strong>Tamaño:</strong> ${formatBytes(node.size)} (${pct.toFixed(1)}% del nivel)</p>
    <p><strong>Archivos:</strong> ${formatCount(node.count)}</p>
    <p><strong>Modificado:</strong> ${formatDate(node.mtime)}</p>
    ${oldLine}
    <p><strong>Ruta:</strong> ${esc(node.path || (state.result.demo ? "Ejemplo, sin archivo real" : "—"))}</p>
    ${dup}
  `;
  const buttons = [];
  if (node.dir) buttons.push(`<button type="button" id="enterBtn">${node.collapsed ? "Analizar esta carpeta" : "Entrar"}</button>`);
  if (state.current?.parent) buttons.push(`<button type="button" id="upBtn">Subir</button>`);
  if (node.path && !node.dir) buttons.push(`<button type="button" id="openBtn">Abrir</button>`);
  if (node.path) buttons.push(`<button type="button" id="revealBtn">Mostrar en el Explorador</button>`);
  actionsEl.innerHTML = buttons.join("");
}

function drawLegend() {
  const totals = new Map();
  const walk = (node) => {
    if (!node.dir) {
      const key = node.aggregate ? "other" : node.category || "other";
      totals.set(key, (totals.get(key) || 0) + node.size);
      return;
    }
    if (!node.children?.length) {
      totals.set("folder", (totals.get("folder") || 0) + node.size);
      return;
    }
    for (const child of node.children) walk(child);
  };
  walk(state.current);
  const sum = [...totals.values()].reduce((acc, n) => acc + n, 0) || 1;
  legendEl.innerHTML = [...totals.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, bytes]) => {
      const pct = bytes / sum * 100;
      const color = COLORS[key] || COLORS.other;
      const label = LABELS[key] || key;
      return `<div class="swatch"><i style="background:${color}"></i><span>${esc(label)}</span><div class="bar"><span style="width:${pct}%;background:${color}"></span></div><span class="size">${formatBytes(bytes)}</span></div>`;
    })
    .join("");
}

function clearBoxes(node) {
  if (!node) return;
  node._box = null;
  for (const child of node.children || []) clearBoxes(child);
}

function innerStreet(w, d) {
  const side = Math.min(w, d);
  if (!(side > 0)) return 1;
  const gap = Math.min(1.25, side * 0.2);
  return Math.max(0.72, 1 - gap / side);
}

function insetStreet(x, z, w, d, street) {
  return {
    x: x + (w * (1 - street)) / 2,
    z: z + (d * (1 - street)) / 2,
    w: w * street,
    d: d * street,
  };
}

function pushRecord(groups, key, record) {
  if (!groups.has(key)) groups.set(key, []);
  groups.get(key).push(record);
}

function placeNode(node, box, height, groups, solid, depth) {
  const lift = solid ? 0 : depth * 0.03;
  const record = { node, x: box.x, z: box.z, w: box.w, d: box.d, height, lift, solid };
  node._box = {
    cx: box.x + box.w / 2,
    cz: box.z + box.d / 2,
    w: box.w,
    d: box.d,
    h: height,
    lift,
  };
  if (solid) footprints.push(record);
  pushRecord(groups, solid ? styleKey(node) : "platform", record);
}

function layoutNode(node, x, z, w, d, depth, groups) {
  const street = depth === 0 ? STREET : innerStreet(w, d);
  const box = insetStreet(x, z, w, d, street);
  const entries = node.dir ? visibleEntries(node) : [];
  const readable = w >= CITY_MIN && d >= CITY_MIN && box.w >= 0.55 && box.d >= 0.55;
  if (depth > 64 || !entries.length || !readable) {
    placeNode(node, box, blockHeight(node), groups, true, depth);
    return;
  }
  placeNode(node, box, PLATFORM_H, groups, false, depth);
  const laid = squarify(entries, box.x, box.z, box.w, box.d);
  for (const item of laid) layoutNode(item.node, item.x, item.y, item.w, item.h, depth + 1, groups);
}

function buildCity(entries) {
  for (const mesh of [...city.children]) {
    city.remove(mesh);
    mesh.material?.dispose();
    mesh.dispose();
  }
  footprints = [];
  highlight.visible = false;
  if (state.result) clearBoxes(state.result.root);
  const groups = new Map();
  const laid = squarify(entries, 0, 0, WORLD, WORLD);
  for (const item of laid) layoutNode(item.node, item.x, item.y, item.w, item.h, 0, groups);
  const keys = [...groups.keys()].sort((a, b) => (a === "platform" ? -1 : b === "platform" ? 1 : 0));
  for (const key of keys) {
    const records = groups.get(key);
    const color = key === "platform" ? PLATFORM_COLOR : colorFor(records[0].node);
    const material = new THREE.MeshLambertMaterial({ color: new THREE.Color(color) });
    const mesh = new THREE.InstancedMesh(boxGeo, material, records.length);
    mesh.name = key;
    mesh.userData.records = records;
    const dummy = new THREE.Object3D();
    records.forEach((record, index) => {
      dummy.position.set(record.x + record.w / 2, record.lift + record.height / 2, record.z + record.d / 2);
      dummy.scale.set(Math.max(record.w, 0.04), record.height, Math.max(record.d, 0.04));
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
    city.add(mesh);
  }
}

function drawMap() {
  const width = map.clientWidth || 300;
  const height = map.clientHeight || 220;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  map.width = Math.max(1, Math.floor(width * dpr));
  map.height = Math.max(1, Math.floor(height * dpr));
  mapCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  mapCtx.clearRect(0, 0, width, height);
  state.mapTree = mapTree(state.current, 0, 0, width, height);
  paintMap(state.mapTree);
}

function mapTree(node, x, y, w, h) {
  const rect = { node, x, y, w, h, kids: [] };
  const entries = node.dir ? visibleEntries(node) : [];
  if (!entries.length || w < 3 || h < 3) return rect;
  const laid = squarify(entries, x + 1, y + 1, Math.max(0, w - 2), Math.max(0, h - 2));
  for (const item of laid) rect.kids.push(mapTree(item.node, item.x, item.y, item.w, item.h));
  return rect;
}

function paintMap(rect) {
  if (rect.kids.length) {
    mapCtx.fillStyle = "#10161d";
    mapCtx.fillRect(rect.x, rect.y, rect.w, rect.h);
    for (const kid of rect.kids) paintMap(kid);
  } else {
    mapCtx.fillStyle = colorFor(rect.node.dir ? rect.node : rect.node);
    if (!rect.node.dir || !rect.node.children?.length) mapCtx.fillStyle = colorFor(rect.node);
    mapCtx.fillRect(rect.x, rect.y, Math.max(rect.w, 0.5), Math.max(rect.h, 0.5));
  }
  if (rect.node === state.selected) {
    mapCtx.strokeStyle = "#ffe7a3";
    mapCtx.lineWidth = 2;
    mapCtx.strokeRect(rect.x + 0.5, rect.y + 0.5, Math.max(0, rect.w - 1), Math.max(0, rect.h - 1));
  }
}

function hitMap(tree, x, y) {
  if (!tree || x < tree.x || y < tree.y || x > tree.x + tree.w || y > tree.y + tree.h) return null;
  for (let i = tree.kids.length - 1; i >= 0; i -= 1) {
    const found = hitMap(tree.kids[i], x, y);
    if (found) return found;
  }
  return tree.node;
}

function nodeAtMap(ev) {
  if (!state.mapTree) return null;
  const bounds = map.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const x = ((ev.clientX - bounds.left) / bounds.width) * state.mapTree.w;
  const y = ((ev.clientY - bounds.top) / bounds.height) * state.mapTree.h;
  return hitMap(state.mapTree, x, y);
}

function repaintMap() {
  if (!state.mapTree) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  mapCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  mapCtx.clearRect(0, 0, state.mapTree.w, state.mapTree.h);
  paintMap(state.mapTree);
}

function selectInPlace(node) {
  if (!node || !state.result || !state.current) return;
  const panel = map.closest(".panel");
  const beforeTop = map.getBoundingClientRect().top;
  const panelScroll = panel ? panel.scrollTop : 0;
  state.selected = node;
  drawList(visibleEntries(state.current));
  drawTree();
  drawProps();
  let shift = map.getBoundingClientRect().top - beforeTop;
  if (panel && shift) {
    const max = panel.scrollHeight - panel.clientHeight;
    if (max > 0) {
      const next = Math.min(max, Math.max(0, panelScroll + shift));
      panel.scrollTop = next;
      shift -= next - panelScroll;
    }
  }
  if (Math.abs(shift) > 0.5) window.scrollBy(0, shift);
  repaintMap();
  placeHighlight();
}

function showHover(node, ev) {
  tooltip.hidden = false;
  tooltip.textContent = `${node.name} · ${formatBytes(node.size)}`;
  const pad = 14;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  let left = ev.clientX + pad;
  let top = ev.clientY + pad;
  if (left + width > window.innerWidth - 8) left = ev.clientX - width - pad;
  if (top + height > window.innerHeight - 8) top = ev.clientY - height - pad;
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${Math.max(8, top)}px`;
}

function placeHighlight() {
  const box = state.selected?._box;
  highlight.visible = Boolean(box);
  if (!box) return;
  highlight.scale.set(Math.max(box.w * 1.04, 0.08), Math.max(box.h * 1.02, 0.08), Math.max(box.d * 1.04, 0.08));
  highlight.position.set(box.cx, (box.lift || 0) + box.h / 2, box.cz);
}

function frameCity() {
  controls.target.set(50, 0, 50);
  if (state.view === "top") camera.position.set(50, 175, 50.2);
  else camera.position.set(148, 108, 148);
  camera.lookAt(50, 0, 50);
  controls.update();
}

function frameSelection() {
  const box = state.selected._box;
  controls.target.set(box.cx, Math.min(box.h * 0.25, 6), box.cz);
  const dist = Math.max(14, Math.max(box.w, box.d) * 2.8);
  if (state.view === "top") camera.position.set(box.cx, dist + 20, box.cz + 0.2);
  else camera.position.set(box.cx + dist, dist * 0.72, box.cz + dist);
  camera.lookAt(box.cx, 0, box.cz);
  controls.update();
}

function resizeRenderer() {
  const width = stage.clientWidth || 1;
  const height = stage.clientHeight || 1;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height, false);
}

function pickAt(ndcX, ndcY) {
  pointer.set(ndcX, ndcY);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(city.children, false);
  if (!hits.length || hits[0].instanceId === undefined) return null;
  return hits[0].object.userData.records[hits[0].instanceId]?.node || null;
}

function pickEvent(ev) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  const y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  return pickAt(x, y);
}

function pickWalk(ev) {
  if (document.pointerLockElement === canvas) return pickAt(0, 0);
  return pickEvent(ev);
}

const WALK_DBL_MS = 400;
let walkClick = { time: 0, id: null };
let suppressWalkDbl = false;

function handleWalkPointer(ev) {
  const node = pickWalk(ev);
  if (!node || node === state.current) return;
  const now = performance.now();
  const dbl = walkClick.id === node.id && now - walkClick.time < WALK_DBL_MS;
  walkClick = { time: now, id: node.id };
  if (dbl) {
    suppressWalkDbl = true;
    if (node.dir) enter(node);
    else if (node.path) openNode(node, false);
    else selectInPlace(node);
    return;
  }
  selectInPlace(node);
}

function updateWalkHover() {
  if (state.view !== "walk" || document.pointerLockElement !== canvas) return;
  const node = pickAt(0, 0);
  if (!node || node === state.current) {
    tooltip.hidden = true;
    return;
  }
  const rect = canvas.getBoundingClientRect();
  showHover(node, {
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height * 0.42,
  });
}

function setView(mode) {
  state.view = mode;
  setPressed(document.querySelector("#views"), "view", mode);
  const walking = mode === "walk";
  controls.enabled = !walking;
  controls.enableRotate = mode === "orbit";
  controls.enablePan = !walking;
  controls.keys = mode === "orbit"
    ? { LEFT: "ArrowLeft", UP: "ArrowUp", RIGHT: "ArrowRight", BOTTOM: "ArrowDown" }
    : { LEFT: "", UP: "", RIGHT: "", BOTTOM: "" };
  crosshair.hidden = true;
  if (mode === "top") {
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = 0.02;
    const target = controls.target;
    camera.position.set(target.x, 150, target.z + 0.2);
    camera.lookAt(target);
    hint.textContent = "Vista cenital. Rueda acerca. Arrastrar desplaza. El mapa 2D sigue al lado.";
  } else if (mode === "orbit") {
    controls.minPolarAngle = 0.08;
    controls.maxPolarAngle = Math.PI / 2.08;
    hint.textContent = "Clic selecciona. Doble clic entra. Rueda acerca. Flechas desplazan si el mapa tiene el foco. Arrastrar gira.";
  } else {
    player.x = controls.target.x;
    player.z = Math.min(112, controls.target.z + 16);
    player.yaw = 0;
    player.pitch = -0.12;
    player.fly = 0;
    hint.textContent = "WASD camina. Mouse mira. Clic selecciona (mira). Doble clic entra. Espacio sube. Ctrl baja. Esc suelta el mouse.";
    walkClick = { time: 0, id: null };
    canvas.requestPointerLock();
  }
}

function blocked(x, z) {
  const radius = 0.45;
  for (const block of footprints) {
    if (x > block.x - radius && x < block.x + block.w + radius && z > block.z - radius && z < block.z + block.d + radius) {
      return true;
    }
  }
  return false;
}

function updateWalk(dt) {
  let forward = 0;
  let strafe = 0;
  if (keys.KeyW || keys.ArrowUp) forward += 1;
  if (keys.KeyS || keys.ArrowDown) forward -= 1;
  if (keys.KeyD || keys.ArrowRight) strafe += 1;
  if (keys.KeyA || keys.ArrowLeft) strafe -= 1;
  const speed = (keys.ShiftLeft || keys.ShiftRight ? 28 : 14) * dt;
  const sin = Math.sin(player.yaw);
  const cos = Math.cos(player.yaw);
  let vx = (-sin * forward + cos * strafe) * speed;
  let vz = (-cos * forward - sin * strafe) * speed;
  const length = Math.hypot(forward, strafe);
  if (length > 1) {
    vx /= length;
    vz /= length;
  }
  let nx = player.x + vx;
  let nz = player.z + vz;
  const stuck = blocked(player.x, player.z);
  if (!stuck && player.fly < 1.2 && blocked(nx, nz)) {
    if (!blocked(nx, player.z)) nz = player.z;
    else if (!blocked(player.x, nz)) nx = player.x;
    else {
      nx = player.x;
      nz = player.z;
    }
  }
  player.x = nx;
  player.z = nz;
  if (keys.Space) player.fly = Math.min(40, player.fly + 16 * dt);
  if (keys.ControlLeft || keys.ControlRight) player.fly = Math.max(0, player.fly - 16 * dt);
  const eye = 1.7 + player.fly;
  camera.position.set(player.x, eye, player.z);
  const cp = Math.cos(player.pitch);
  camera.lookAt(
    player.x - Math.sin(player.yaw) * cp,
    eye + Math.sin(player.pitch),
    player.z - Math.cos(player.yaw) * cp
  );
  updateWalkHover();
}

let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  if (state.view === "walk") updateWalk(dt);
  else controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

document.querySelector("#chips").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  state.filter.kind = button.dataset.kind;
  setPressed(document.querySelector("#chips"), "kind", state.filter.kind);
  ageLabel.hidden = state.filter.kind !== "old";
  if (state.selected && layoutSize(state.selected) <= 0) state.selected = null;
  if (state.result) render({ frame: true });
});

document.querySelector("#heights").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  state.height = button.dataset.height;
  setPressed(document.querySelector("#heights"), "height", state.height);
  heightHelp();
  if (state.result) render({ frame: false });
});

document.querySelector("#views").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  setView(button.dataset.view);
});

function heightHelp() {
  const text = {
    age: "Más alto significa más antiguo. Un archivo usa su fecha; una carpeta cerrada, el archivo interno más viejo. La superficie sigue siendo el tamaño en bytes.",
    count: "En una carpeta cerrada, más alto significa más archivos en su interior. La superficie sigue siendo el tamaño.",
    flat: "Todos los bloques tienen altura parecida. Solo el área muestra el tamaño.",
  };
  document.querySelector("#heightHelp").textContent = text[state.height];
  updateCurveModeNote();
}

ageDays.addEventListener("change", () => {
  state.filter.days = Math.max(1, Number(ageDays.value) || 365);
  if (state.filter.kind === "old" && state.result) render({ frame: false });
});

queryInput.addEventListener("input", () => {
  state.filter.q = queryInput.value.trim().toLowerCase();
  if (state.selected && layoutSize(state.selected) <= 0) state.selected = null;
  if (state.result) render({ frame: true });
});

scanBtn.addEventListener("click", () => scanPath(pathInput.value));
pathInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") scanPath(pathInput.value);
});
demoBtn.addEventListener("click", async () => {
  setBusy(true);
  try {
    applyResult(await readJson(await fetch("/api/demo", { method: "POST" })));
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    setBusy(false);
  }
});
cancelBtn.addEventListener("click", () => {
  fetch("/api/cancel", { method: "POST" });
  statusEl.textContent = "Cancelando…";
});
browseUp.addEventListener("click", () => {
  if (browseUp.dataset.path) loadBrowse(browseUp.dataset.path).catch((error) => {
    statusEl.textContent = error.message;
  });
});
quickEl.addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  pathInput.value = button.dataset.path;
  loadBrowse(button.dataset.path).catch((error) => {
    statusEl.textContent = error.message;
  });
});
dirsEl.addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  pathInput.value = button.dataset.path;
  loadBrowse(button.dataset.path).catch((error) => {
    statusEl.textContent = error.message;
  });
});

treeEl.addEventListener("click", (ev) => {
  const twist = ev.target.closest("[data-twist]");
  if (twist) {
    const id = Number(twist.dataset.twist);
    if (treeOpen.has(id)) treeOpen.delete(id);
    else treeOpen.add(id);
    drawTree();
    return;
  }
  const button = ev.target.closest("[data-tree]");
  if (!button) return;
  const node = state.byId.get(Number(button.dataset.tree));
  if (!node) return;
  if (node.dir) enter(node);
  else focusNode(node);
});

listEl.addEventListener("click", (ev) => {
  const enterBtn = ev.target.closest("[data-enter]");
  if (enterBtn) {
    ev.stopPropagation();
    enter(state.byId.get(Number(enterBtn.dataset.enter)));
    return;
  }
  const row = ev.target.closest("[data-id]");
  if (!row) return;
  focusNode(state.byId.get(Number(row.dataset.id)));
});
topEl.addEventListener("click", (ev) => {
  const row = ev.target.closest("[data-id]");
  if (!row) return;
  focusNode(state.byId.get(Number(row.dataset.id)));
});
crumbEl.addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button) return;
  const node = state.byId.get(Number(button.dataset.id));
  if (!node?.dir) return;
  state.current = node;
  state.selected = null;
  render({ frame: true });
});
actionsEl.addEventListener("click", (ev) => {
  const id = ev.target.id;
  if (id === "upBtn") {
    ascend();
    return;
  }
  const node = state.selected;
  if (!node) return;
  if (id === "enterBtn") enter(node);
  if (id === "openBtn") openNode(node, false);
  if (id === "revealBtn") openNode(node, true);
});

map.addEventListener("click", (ev) => {
  const node = nodeAtMap(ev);
  if (node) selectInPlace(node);
});
map.addEventListener("dblclick", (ev) => {
  const node = nodeAtMap(ev);
  if (!node) return;
  if (node.dir) enter(node);
  else selectInPlace(node);
});
map.addEventListener("pointermove", (ev) => {
  const node = nodeAtMap(ev);
  if (!node) {
    tooltip.hidden = true;
    return;
  }
  showHover(node, ev);
});
map.addEventListener("pointerleave", () => {
  tooltip.hidden = true;
});

let down = null;
canvas.addEventListener("pointerdown", (ev) => {
  down = { x: ev.clientX, y: ev.clientY };
  if (state.view !== "walk") canvas.focus({ preventScroll: true });
});
stage.addEventListener("wheel", (ev) => {
  if (state.view !== "orbit") return;
  ev.preventDefault();
  if (ev.target === canvas) return;
  canvas.dispatchEvent(new WheelEvent("wheel", {
    bubbles: true,
    cancelable: true,
    deltaX: ev.deltaX,
    deltaY: ev.deltaY,
    deltaZ: ev.deltaZ,
    deltaMode: ev.deltaMode,
    clientX: ev.clientX,
    clientY: ev.clientY,
  }));
}, { passive: false, capture: true });
canvas.addEventListener("pointerup", (ev) => {
  if (!down) return;
  const dragged = Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 5;
  down = null;
  if (dragged && state.view !== "walk") return;
  if (state.view === "walk") {
    // Con pointer lock el cursor no se mueve: el arrastre no aplica.
    // Sin lock, un arrastre breve al mirar no debe seleccionar.
    if (dragged && document.pointerLockElement !== canvas) return;
    handleWalkPointer(ev);
    return;
  }
  const node = pickEvent(ev);
  if (!node || node === state.current) return;
  selectInPlace(node);
});
canvas.addEventListener("dblclick", (ev) => {
  if (state.view === "walk") {
    if (suppressWalkDbl) {
      suppressWalkDbl = false;
      return;
    }
    // Respaldo si el navegador emite dblclick (p. ej. sin pointer lock).
    const node = pickWalk(ev);
    if (node?.dir) enter(node);
    else if (node?.path) openNode(node, false);
    return;
  }
  const node = pickEvent(ev);
  if (node?.dir) enter(node);
  else if (node?.path) openNode(node, false);
});
canvas.addEventListener("pointermove", (ev) => {
  if (state.view === "walk") {
    if (document.pointerLockElement === canvas) return;
    const node = pickEvent(ev);
    if (!node || node === state.current) {
      tooltip.hidden = true;
      return;
    }
    showHover(node, ev);
    return;
  }
  const node = pickEvent(ev);
  if (!node) {
    tooltip.hidden = true;
    return;
  }
  showHover(node, ev);
});
canvas.addEventListener("pointerleave", () => {
  if (state.view === "walk" && document.pointerLockElement === canvas) return;
  tooltip.hidden = true;
});
canvas.addEventListener("click", () => {
  if (state.view === "walk" && document.pointerLockElement !== canvas) canvas.requestPointerLock();
});

document.addEventListener("pointerlockchange", () => {
  const locked = document.pointerLockElement === canvas;
  crosshair.hidden = !(locked && state.view === "walk");
  if (state.view === "walk" && !locked) {
    tooltip.hidden = true;
    hint.textContent = "Clic en la ciudad para mirar. Clic selecciona. Doble clic entra. Órbita vuelve a la cámara libre.";
  } else if (state.view === "walk" && locked) {
    hint.textContent = "WASD camina. Mouse mira. Clic selecciona (mira). Doble clic entra. Espacio sube. Ctrl baja. Esc suelta el mouse.";
  }
});
window.addEventListener("keydown", (ev) => {
  keys[ev.code] = true;
  if (state.view === "walk" && !typingTarget() && ["KeyW", "KeyA", "KeyS", "KeyD", "Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(ev.code)) {
    ev.preventDefault();
  }
});
window.addEventListener("keyup", (ev) => {
  keys[ev.code] = false;
});
document.addEventListener("mousemove", (ev) => {
  if (state.view !== "walk" || document.pointerLockElement !== canvas) return;
  player.yaw -= ev.movementX * 0.0022;
  player.pitch -= ev.movementY * 0.002;
  player.pitch = Math.max(-1.15, Math.min(1.15, player.pitch));
});

window.addEventListener("resize", () => {
  resizeRenderer();
  if (state.result) render({ frame: false });
});

curveBtn.addEventListener("click", () => setCurvePanel(curvePanel.hidden));
curveClose.addEventListener("click", () => {
  setCurvePanel(false);
  curveBtn.focus();
});
curveReset.addEventListener("click", resetCurve);
for (const input of [curveMin, curveMax, curveHorizon]) input.addEventListener("input", commitCurveDraft);
curveLine.addEventListener("input", () => {
  curveLineNum.value = curveLine.value;
  commitCurveDraft();
});
curveLineNum.addEventListener("input", () => {
  const value = Number(curveLineNum.value);
  if (Number.isFinite(value)) curveLine.value = String(Math.min(1, Math.max(0, value)));
  commitCurveDraft();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape" || curvePanel.hidden) return;
  setCurvePanel(false);
  curveBtn.focus();
});

state.curve = loadCurve();
fillCurveForm(state.curve);
updateCurveModeNote();

resizeRenderer();
requestAnimationFrame(frame);
drawTree();
loadBrowse("").catch((error) => {
  statusEl.textContent = error.message;
});
