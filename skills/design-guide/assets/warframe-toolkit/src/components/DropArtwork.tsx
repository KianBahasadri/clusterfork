import { useState } from 'react';
import type { DropArt } from '../lib/dropArtwork';

export function DropArtwork({ art }: { art?: DropArt }) {
  const [failed, setFailed] = useState<string>();
  const available = art && failed !== art.src;
  return <span className={`drop-artwork ${available ? `is-${art.fit}` : 'is-missing'}`}>
    {available ? <img src={art.src} alt={art.label} title={art.label} loading="lazy" decoding="async" onError={() => setFailed(art.src)} /> : <span className="drop-artwork-missing">Artwork unavailable</span>}
  </span>;
}
