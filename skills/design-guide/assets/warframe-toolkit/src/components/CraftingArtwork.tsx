import { useState } from 'react';
import { Box } from 'lucide-react';

export function CraftingArtwork({ item }: { item: { image?: string; name: string } }) {
  const [failed, setFailed] = useState<string>();
  return <span className="craft-art" aria-hidden="true">{item.image && failed !== item.image ? <img src={item.image} alt="" loading="lazy" decoding="async" onError={() => setFailed(item.image)} /> : <Box size={24} />}</span>;
}
