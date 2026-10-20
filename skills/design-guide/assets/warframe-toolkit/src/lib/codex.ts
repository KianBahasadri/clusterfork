import masteryMask from '../data/codex-mastery-mask.json' with { type: 'json' };

export interface PixelImage { width: number; height: number; data: Uint8ClampedArray }
export interface CodexLabel { x: number; y: number; width: number; height: number }

const badgeMask = masteryMask.rows.join('');
const badgePixels = [...badgeMask].filter(pixel => pixel === '1').length;
const badgeOffsets = [0, -0.005, 0.005, -0.01, 0.01, -0.015, 0.015, -0.02, 0.02];

// Match the laurel's shape, including its empty center, rather than treating
// green artwork or a filled progress bar as mastery. Coordinates follow each
// detected card; nearby offsets and scales tolerate resampling and UI scaling.
export function hasCodexMasteryBadge(image: PixelImage, label: CodexLabel): boolean {
  const { width, height, data } = image;
  for (const sizeRatio of [0.188, 0.18, 0.196, 0.17, 0.205]) {
    const size = label.width * sizeRatio;
    for (const dx of badgeOffsets) for (const dy of badgeOffsets) {
      const left = label.x + label.width * (0.88 + dx) - size / 2;
      const top = label.y - label.width * (0.74 + dy) - size / 2;
      if (left < 0 || top < 0 || left + size > width || top + size > height) continue;
      let lit = 0, overlap = 0;
      for (let y = 0; y < masteryMask.size; y++) for (let x = 0; x < masteryMask.size; x++) {
        const pixel = (Math.floor(top + (y + 0.5) * size / masteryMask.size) * width + Math.floor(left + (x + 0.5) * size / masteryMask.size)) * 4;
        if (data[pixel + 1] > 150 && data[pixel + 1] > data[pixel] * 1.15 && data[pixel + 2] > data[pixel] * 1.05) {
          lit++;
          if (badgeMask[y * masteryMask.size + x] === '1') overlap++;
        }
      }
      if (overlap / badgePixels >= 0.72 && overlap / lit >= 0.7) return true;
    }
  }
  return false;
}

// Find pale rectangular name strips, rather than assuming a resolution,
// number of columns, or fixed screen coordinates. Follow their sides through
// the text, without joining weapon artwork that touches the strip from above.
export function findCodexLabels(image: PixelImage): CodexLabel[] {
  const { width, height, data } = image;
  const mask = new Uint8Array(width * height);
  for (let pixel = 0; pixel < mask.length; pixel++) {
    const offset = pixel * 4;
    const low = Math.min(data[offset], data[offset + 1], data[offset + 2]);
    const high = Math.max(data[offset], data[offset + 1], data[offset + 2]);
    mask[pixel] = low > 185 && high - low < 35 ? 1 : 0;
  }
  const labels: CodexLabel[] = [];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width;) {
      if (!mask[y * width + x]) { x++; continue; }
      const left = x;
      while (x < width && mask[y * width + x]) x++;
      const w = x - left;
      if (w < 60 || w > width * 0.45 || labels.some(label => left >= label.x && x <= label.x + label.width && y >= label.y && y < label.y + label.height)) continue;
      const belongs = (row: number) => {
        if (row < 0 || row >= height) return false;
        const edge = Math.max(2, Math.round(w * 0.01));
        if (!mask.subarray(row * width + left, row * width + left + edge).some(Boolean)
          || !mask.subarray(row * width + x - edge, row * width + x).some(Boolean)) return false;
        let bright = 0;
        for (let col = left; col < x; col++) bright += mask[row * width + col];
        return bright > w * 0.2;
      };
      let top = y, bottom = y + 1;
      const maxHeight = Math.min(height * 0.12, w / 3.5);
      while (bottom - top <= maxHeight && belongs(top - 1)) top--;
      while (bottom - top <= maxHeight && belongs(bottom)) bottom++;
      const h = bottom - top;
      if (h >= 12 && w / h >= 3.5 && w / h <= 9 && h < height * 0.12) labels.push({ x: left, y: top, width: w, height: h });
    }
  }
  // Require a row of similarly sized cards before using the specialized reader.
  const aligned = labels.filter(label => labels.filter(other => Math.abs(other.x - label.x) > label.width * 0.8 && Math.abs(other.y - label.y) < label.height * 0.2
    && Math.abs(other.width - label.width) < label.width * 0.12
    && Math.abs(other.height - label.height) < label.height * 0.2).length >= 2);
  const distinct: CodexLabel[] = [];
  for (const label of aligned.sort((a, b) => b.width * b.height - a.width * a.height)) {
    if (!distinct.some(other => Math.abs(other.x - label.x) < label.width / 2 && Math.abs(other.y - label.y) < label.height / 2)) distinct.push(label);
  }
  return distinct.sort((a, b) => Math.abs(a.y - b.y) < Math.min(a.height, b.height) / 2 ? a.x - b.x : a.y - b.y);
}
