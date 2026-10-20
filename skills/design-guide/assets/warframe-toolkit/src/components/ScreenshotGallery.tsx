import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Dialog } from './Dialog';

interface GalleryImage { url: string; name: string }

export function ScreenshotGallery({ images, activeUrl, onSelect, onClose }: { images: GalleryImage[]; activeUrl: string; onSelect: (url: string) => void; onClose: () => void }) {
  const index = Math.max(0, images.findIndex(image => image.url === activeUrl));
  const current = images[index];
  const move = (step: number) => onSelect(images[(index + step + images.length) % images.length].url);
  return <Dialog title="Screenshot gallery" className="screenshot-gallery-dialog" onClose={onClose} onKeyDown={event => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Tab') {
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      if (document.activeElement === (event.shiftKey ? first : last)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
    }
    if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); move(1); }
    if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); onSelect(images[event.key === 'Home' ? 0 : images.length - 1].url); }
  }}>
    <img className="gallery-image" src={current.url} alt={`Screenshot ${index + 1}: ${current.name}`} />
    <div className="gallery-controls">
      <button type="button" className="icon-button" aria-label="Previous screenshot" title="Previous screenshot" autoFocus={images.length > 1} disabled={images.length < 2} onClick={() => move(-1)}><ChevronLeft size={20} aria-hidden="true" /></button>
      <div className="gallery-caption" role="status"><span>{current.name}</span><small>{index + 1} of {images.length}</small></div>
      <button type="button" className="icon-button" aria-label="Next screenshot" title="Next screenshot" disabled={images.length < 2} onClick={() => move(1)}><ChevronRight size={20} aria-hidden="true" /></button>
    </div>
  </Dialog>;
}
