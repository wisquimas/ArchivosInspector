// Treemap squarificado (Bruls, Huizing, van Wijk).
// La superficie de cada rectángulo es proporcional a `size`.

export function squarify(entries, x, y, w, h) {
  const items = (entries || [])
    .filter((entry) => entry && entry.size > 0 && entry.node)
    .sort((a, b) => b.size - a.size);
  if (!items.length || w <= 0 || h <= 0) return [];

  const total = items.reduce((sum, entry) => sum + entry.size, 0);
  const scale = (w * h) / total;
  const areas = items.map((entry) => ({
    node: entry.node,
    area: entry.size * scale,
  }));

  const placed = [];
  let row = [];
  let rx = x;
  let ry = y;
  let rw = w;
  let rh = h;
  let index = 0;

  while (index < areas.length || row.length) {
    const side = Math.min(rw, rh);
    const canAdd =
      index < areas.length &&
      (row.length === 0 || worst(row, side) >= worst(row.concat(areas[index]), side));

    if (canAdd) {
      row.push(areas[index]);
      index += 1;
      if (index < areas.length) continue;
    }

    if (!row.length || rw <= 0 || rh <= 0) break;
    const next = placeRow(row, rx, ry, rw, rh, placed);
    rx = next.x;
    ry = next.y;
    rw = next.w;
    rh = next.h;
    row = [];
    if (index >= areas.length) break;
  }

  return placed;
}

function worst(row, side) {
  let sum = 0;
  let maxArea = 0;
  let minArea = Infinity;
  for (const item of row) {
    sum += item.area;
    if (item.area > maxArea) maxArea = item.area;
    if (item.area < minArea) minArea = item.area;
  }
  if (!(sum > 0) || !(side > 0) || !(minArea > 0)) return Infinity;
  const sum2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * maxArea) / sum2, sum2 / (side2 * minArea));
}

function placeRow(row, x, y, w, h, out) {
  let sum = 0;
  for (const item of row) sum += item.area;

  if (w >= h) {
    const width = h > 0 ? sum / h : 0;
    let y0 = y;
    for (const item of row) {
      const height = width > 0 ? item.area / width : 0;
      out.push({ node: item.node, x, y: y0, w: width, h: height });
      y0 += height;
    }
    return { x: x + width, y, w: w - width, h };
  }

  const height = w > 0 ? sum / w : 0;
  let x0 = x;
  for (const item of row) {
    const width = height > 0 ? item.area / height : 0;
    out.push({ node: item.node, x: x0, y, w: width, h: height });
    x0 += width;
  }
  return { x, y: y + height, w, h: h - height };
}
