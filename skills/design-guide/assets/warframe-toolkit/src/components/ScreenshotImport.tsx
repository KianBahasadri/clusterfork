import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { CircleHelp } from 'lucide-react';
import { catalog } from '../data/catalog';
import { createEquipment } from '../lib/model';
import type { Equipment } from '../lib/model';
import { appendDetections, matchOcrLines, reviewDetection } from '../lib/matching';
import type { Detection, ScreenshotSource } from '../lib/matching';
import { appendRelicDetections, matchRelicOcrLines, relicImportError, reviewRelicDetection } from '../lib/relicMatching';
import type { RelicDetection } from '../lib/relicMatching';
import type { Relic } from '../lib/relics';
import { Select } from './Select';
import { ScreenshotHelp } from './ScreenshotHelp';
import { ScreenshotGallery } from './ScreenshotGallery';

const equipmentOptions = catalog.map(item => ({ value: item.id, label: item.name }));
const importSources = [{ value: 'inventory', label: 'Inventory' }, { value: 'codex', label: 'Codex' }, { value: 'relics', label: 'Relics' }] as const;
const RelicScreenshotReview = lazy(() => import('./RelicScreenshotReview').then(module => ({ default: module.RelicScreenshotReview })));

interface Scan { file: File; url: string; read: boolean }
interface Batch { source: ScreenshotSource | null; scans: Scan[]; pending: File[]; completed: boolean }

export function ScreenshotImport({ onImport, onRelicImport, existingRelics, initialSource = 'inventory' }: {
  onImport: (observations: Equipment[], files: string[]) => boolean;
  onRelicImport: (observations: Pick<Relic, 'name' | 'refinement' | 'quantity'>[], files: string[]) => boolean;
  existingRelics: Relic[]; initialSource?: ScreenshotSource;
}) {
  const [mode, setMode] = useState<'equipment' | 'relics'>(initialSource === 'relics' ? 'relics' : 'equipment');
  const [source, setSource] = useState<ScreenshotSource>(initialSource);
  const [pasteTarget, setPasteTarget] = useState<ScreenshotSource>(initialSource);
  const [help, setHelp] = useState<ScreenshotSource | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [departing, setDeparting] = useState<ScreenshotSource | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [relicDetections, setRelicDetections] = useState<RelicDetection[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState<ScreenshotSource | null>(null);
  const inputs = useRef<Record<ScreenshotSource, HTMLInputElement | null>>({ inventory: null, codex: null, relics: null });
  const sourceStage = useRef<HTMLDivElement | null>(null);
  const pasteSource = useRef<ScreenshotSource>(initialSource);
  const controller = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const batch = useRef<Batch>({ source: null, scans: [], pending: [], completed: false });
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); batch.current.pending = []; batch.current.scans.forEach(scan => URL.revokeObjectURL(scan.url)); }; }, []);
  useEffect(() => {
    if (!departing) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setDeparting(null); return; }
    // Also finish if a resize or browser interruption cancels the CSS transition.
    const timeout = window.setTimeout(() => setDeparting(null), 320);
    return () => window.clearTimeout(timeout);
  }, [departing]);
  const choosePasteSource = useCallback((value: ScreenshotSource) => {
    if (batch.current.source || batch.current.completed || document.querySelector('dialog[open]')) return;
    pasteSource.current = value;
    setPasteTarget(value);
  }, []);
  useEffect(() => {
    const followPointer = (event: PointerEvent) => {
      if (mode === 'relics' || event.pointerType === 'touch' || batch.current.source || document.querySelector('dialog[open]')) return;
      const stage = sourceStage.current?.getBoundingClientRect();
      if (!stage) return;
      // Keep the hit boundary stationary while the panels move beneath it.
      const beforeDivider = window.matchMedia('(max-width: 900px)').matches
        ? event.clientY < stage.top + stage.height / 2
        : event.clientX < stage.left + stage.width / 2;
      choosePasteSource(beforeDivider ? 'inventory' : 'codex');
    };
    document.addEventListener('pointermove', followPointer);
    return () => document.removeEventListener('pointermove', followPointer);
  }, [choosePasteSource, mode]);

  const processFiles = async (files: File[], screenshotSource: ScreenshotSource) => {
    if (!files.length || batch.current.completed || controller.current?.signal.aborted || (batch.current.source && batch.current.source !== screenshotSource)) return;
    setSource(screenshotSource);
    if (batch.current.scans.length + files.length > 10) { setError('Up to 10 screenshots per batch. Import or clear this batch before adding more.'); return; }
    if (files.some(file => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) { setError('Use PNG, JPG, or WebP screenshots.'); return; }
    if (files.some(file => file.size > 20 * 1024 * 1024)) { setError('Each screenshot must be smaller than 20 MB.'); return; }
    const next = files.map(file => ({ file, url: (file as { sampleUrl?: string }).sampleUrl ?? URL.createObjectURL(file), read: false }));
    if (!batch.current.source && screenshotSource !== 'relics') setDeparting(screenshotSource === 'inventory' ? 'codex' : 'inventory');
    batch.current.source = screenshotSource;
    batch.current.scans = [...batch.current.scans, ...next];
    batch.current.pending.push(...files);
    pasteSource.current = screenshotSource;
    setPasteTarget(screenshotSource);
    setScans(batch.current.scans); setError(''); setStatus('');
    if (busyRef.current) return;
    setBusy(true); busyRef.current = true; setProgress(0); setStatus('');
    const abort = new AbortController(); controller.current = abort;
    try {
      const { recognizeScreenshots } = await import('../lib/ocr');
      const relicNames = screenshotSource === 'relics' ? (await import('../data/relicCatalog')).relicNames : [];
      while (batch.current.pending.length) {
        const queued = batch.current.pending.splice(0);
        const readCount = batch.current.scans.filter(scan => scan.read).length;
        await recognizeScreenshots(queued, (_message, fraction) => {
          if (active.current) setProgress((readCount + fraction * queued.length) / batch.current.scans.length);
        }, (file, result) => {
          if (!active.current || abort.signal.aborted) return;
          batch.current.scans = batch.current.scans.map(scan => scan.file === file ? { ...scan, read: true } : scan);
          setScans(batch.current.scans);
          if (screenshotSource === 'relics') {
            const matched = matchRelicOcrLines(result.lines, relicNames, file.name, result.imageSize);
            setRelicDetections(current => appendRelicDetections(current, matched));
          } else {
            const matched = matchOcrLines(result.lines, catalog, file.name, screenshotSource);
            setDetections(current => appendDetections(current, matched));
          }
        }, abort.signal, screenshotSource);
      }
      if (active.current) setProgress(1);
    } catch (problem) {
      if (active.current) {
        if (abort.signal.aborted) setStatus('Reading canceled.');
        else setError(problem instanceof Error ? problem.message : 'Could not read this image. Try a clearer screenshot.');
      }
    } finally {
      // Failed/canceled images can be added again without clearing read results.
      batch.current.pending = [];
      batch.current.scans.filter(scan => !scan.read).forEach(scan => URL.revokeObjectURL(scan.url));
      batch.current.scans = batch.current.scans.filter(scan => scan.read);
      if (!batch.current.scans.length) batch.current.source = null;
      controller.current = null;
      busyRef.current = false;
      if (active.current) {
        setScans(batch.current.scans);
        if (!batch.current.scans.length) { setDeparting(null); setViewing(null); }
        setBusy(false);
      }
    }
  };
  const processRef = useRef(processFiles); processRef.current = processFiles;
  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      if (document.querySelector('dialog[open]') || event.target instanceof Element && event.target.closest('input, textarea, [contenteditable]')) return;
      const files = [...(event.clipboardData?.items || [])].filter(item => item.type.startsWith('image/')).map(item => item.getAsFile()).filter((file): file is File => Boolean(file));
      if (files.length) { event.preventDefault(); void processRef.current(files, pasteSource.current); }
    };
    document.addEventListener('paste', paste);
    return () => document.removeEventListener('paste', paste);
  }, []);
  const update = (id: string, patch: Partial<Detection>) => setDetections(current => current.map(detection => detection.id === id ? reviewDetection(detection, patch) : detection));
  const updateRelic = (id: string, patch: Partial<RelicDetection>) => setRelicDetections(current => current.map(detection => detection.id === id ? reviewRelicDetection(detection, patch) : detection));
  const selected = detections.filter(detection => detection.selected);
  const hasBatch = scans.length > 0;
  const visibleSources = importSources.filter(entry => (entry.value === 'relics') === (mode === 'relics') && (!hasBatch || entry.value === source || entry.value === departing));
  const clear = () => {
    batch.current.scans.forEach(scan => URL.revokeObjectURL(scan.url));
    batch.current = { source: null, scans: [], pending: [], completed: false };
    setScans([]); setDetections([]); setRelicDetections([]); setError(''); setStatus(''); setProgress(0); setDeparting(null); setViewing(null);
  };
  const save = () => {
    if (busyRef.current || batch.current.completed) return;
    if (source === 'relics') {
      const selected = relicDetections.filter(detection => detection.selected);
      if (!selected.length) return;
      const problem = relicImportError(relicDetections);
      if (problem) { setError(problem); return; }
      if (onRelicImport(selected.map(detection => ({ name: detection.name, refinement: detection.refinement!, quantity: detection.quantity! })), scans.filter(scan => scan.read).map(scan => scan.file.name))) batch.current.completed = true;
      else setError('Your import could not be applied. Previous data is unchanged.');
      return;
    }
    if (!selected.length) return;
    const observations = selected.map(detection => ({ ...createEquipment(detection.item), owned: detection.owned, mastered: detection.mastered, rank: detection.rank, source: `Screenshot · ${detection.filename}` }));
    if (onImport(observations, scans.filter(scan => scan.read).map(scan => scan.file.name))) batch.current.completed = true;
  };
  return <section className="import-section" aria-label="Import screenshots">
    {!hasBatch && <div className="import-type-options" role="group" aria-label="Screenshot type">{(['equipment', 'relics'] as const).map(value => <button key={value} type="button" className="button quiet" aria-pressed={mode === value} onClick={() => {
      setMode(value); const next = value === 'relics' ? 'relics' : 'inventory'; setSource(next); setPasteTarget(next); pasteSource.current = next; setError(''); setStatus('');
    }}>{value === 'relics' ? 'Relics' : 'Equipment'}</button>)}</div>}
    <div ref={sourceStage} className={`import-sources${hasBatch ? ' has-batch' : ''}${mode === 'relics' ? ' is-single' : ''}`}>{visibleSources.map(({ value, label }) => <section key={value} data-source={value} className={`import-source${!hasBatch && pasteTarget === value ? ' is-paste-target' : ''}${departing === value ? ' is-exiting' : ''}`} inert={departing === value} aria-hidden={departing === value ? true : undefined} aria-labelledby={`${value}-import-title`} aria-description={!hasBatch && pasteTarget === value ? 'Selected for screenshot paste' : undefined} onFocusCapture={() => choosePasteSource(value)} onPointerDownCapture={() => choosePasteSource(value)} onTransitionEnd={event => { if (event.target === event.currentTarget && event.propertyName === 'transform' && departing === value) setDeparting(null); }}>
      <div className="import-source-content">
      <div className="section-heading import-source-heading">
        <h2 id={`${value}-import-title`}>{label}</h2>
        <div className="import-actions">
          <button type="button" className="button secondary" onClick={() => { const f = new File(['sample'], `${value}-sample.webp`, { type: 'image/webp' }); (f as { sampleUrl?: string }).sampleUrl = value === 'codex' ? 'images/import/codex-weapons.webp' : 'images/import/inventory-weapons.webp'; void processFiles([f], value); }}>Load sample</button><button type="button" className="button quiet" aria-label={`${label} instructions`} aria-haspopup="dialog" onClick={() => setHelp(value)}><CircleHelp size={16} aria-hidden="true" />Instructions</button>
          {source === value && (busy ? <button type="button" className="button secondary" onClick={() => controller.current?.abort()}>Cancel reading</button> : (scans.length > 0 || status || error) && <button type="button" className="button secondary" onClick={clear}>Clear</button>)}
        </div>
      </div>
    <input ref={node => { inputs.current[value] = node; }} className="sr-only" type="file" multiple accept="image/png,image/jpeg,image/webp" aria-label={`Choose ${label.toLowerCase()} screenshots`} tabIndex={-1} onChange={event => { void processFiles([...event.target.files || []], value); event.target.value = ''; }} />
    {source === value && error && <p className="callout danger" role="alert">{error}</p>}
    <button type="button" className={`drop-zone ${scans.length && source === value ? 'drop-zone-compact' : ''} ${dragging === value ? 'dragging' : ''}`} aria-labelledby={`${value}-upload-label ${value}-upload-limits`} onClick={() => inputs.current[value]?.click()} onDragOver={event => { event.preventDefault(); choosePasteSource(value); setDragging(value); }} onDragLeave={() => setDragging(null)} onDrop={event => { event.preventDefault(); setDragging(null); void processFiles([...event.dataTransfer.files], value); }}>
      {value !== 'relics' && (!hasBatch || departing === value) && <img className="import-source-example" src={`images/import/${value}-weapons.webp`} width={1920} height={1200} alt={value === 'inventory' ? 'Example Warframe Inventory screen showing weapons and ranks' : 'Example Warframe Codex screen showing weapons and mastery badges'} draggable={false} />}
      <span id={`${value}-upload-label`}>{scans.length && source === value ? 'Add screenshots' : 'Drag and drop or Ctrl+V to paste. Or click to open file browser'}</span>
      <small id={`${value}-upload-limits`}>{scans.length && source === value ? `Drop, paste, or choose · ${scans.length}/10 images` : 'PNG, JPG, WebP · up to 10 images · 20 MB each'}</small>
    </button>
    {source === value && <>
    {busy && <div className="import-progress"><span role="status">Reading screenshots… {Math.round(progress * 100)}%</span><progress value={progress} max={1} aria-label="Screenshot reading progress" /></div>}
    {!!scans.length && <section className="import-preview" aria-label="Screenshot preview">
      <ul className="screenshot-gallery" aria-label="Screenshot previews">{scans.map((scan, index) => <li key={scan.url}>
        <figure>
          <button type="button" className="image-preview" aria-label={`View screenshot ${index + 1}: ${scan.file.name}`} aria-haspopup="dialog" onClick={() => setViewing(scan.url)}><img src={scan.url} alt={`Import source: ${scan.file.name}`} /></button>
          <figcaption>{index + 1}. {scan.file.name}</figcaption>
        </figure>
      </li>)}</ul>
    </section>}
    {!busy && status && <p className="import-status" role="status">{status}</p>}
    {!!scans.length && source === 'relics' && (relicDetections.length ? <Suspense fallback={<p role="status">Loading review…</p>}><RelicScreenshotReview detections={relicDetections} existing={existingRelics} busy={busy} onUpdate={updateRelic} onSelectAll={() => setRelicDetections(current => current.map(detection => reviewRelicDetection(detection, { selected: !current.every(entry => entry.selected) })))} onImport={save} /></Suspense> : !busy && !status && !error && <p className="import-status" role="status">No relics matched.</p>)}
    {!!scans.length && source !== 'relics' && (detections.length ? <section className="review-section" aria-labelledby="review-title">
      <div className="section-heading"><h3 id="review-title">Review matches</h3><div className="import-actions">
        <button type="button" className="button secondary" onClick={() => setDetections(current => current.map(detection => reviewDetection(detection, { selected: !current.every(entry => entry.selected) })))}>{detections.every(detection => detection.selected) ? 'Deselect all' : 'Select all'}</button>
        <button type="button" className="button primary" disabled={busy || !selected.length} title={busy ? 'Finish or cancel reading before importing.' : undefined} onClick={save}>Import {selected.length} {selected.length === 1 ? 'item' : 'items'}</button>
      </div></div>
      <div className="table-scroll" role="region" aria-label="Screenshot matches" tabIndex={0}><table className="review-table">
        <caption className="sr-only">Review names, ownership, mastery, and rank before importing selected rows. Existing ownership and mastery are preserved.</caption>
        <thead><tr><th scope="col">Import</th><th scope="col">Equipment</th><th scope="col" title="Imports preserve existing ownership.">Owned</th><th scope="col" title="Checked from a recognized Codex mastery badge or the catalog's maximum rank. Modular equipment needs review unless a badge was recognized. Existing mastery is preserved.">Mastered</th><th scope="col">Rank</th></tr></thead>
        <tbody>{detections.map(detection => <tr key={detection.id}>
          <td><label className="table-checkbox"><input type="checkbox" checked={detection.selected} aria-label={`Import ${detection.item.name}`} onChange={event => update(detection.id, { selected: event.target.checked })} /></label></td>
          <td className="match-cell"><Select value={detection.item.id} label={`Matched name for ${detection.rawText}`} onChange={value => { const entry = catalog.find(item => item.id === value); if (entry) update(detection.id, { item: entry, exact: false }); }} options={equipmentOptions} /><small>Read: {detection.rawText}</small>{(!detection.exact || detection.confidence < 65) && <span className="badge caution-badge">Check match</span>}</td>
          <td><label className="table-checkbox"><input type="checkbox" checked={detection.owned} aria-label={`Owned: ${detection.item.name}`} onChange={event => update(detection.id, { owned: event.target.checked })} /></label></td>
          <td><label className="table-checkbox"><input type="checkbox" checked={detection.mastered} aria-label={`Mastered: ${detection.item.name}`} onChange={event => update(detection.id, { mastered: event.target.checked })} /></label></td>
          <td><input className="rank-input" type="number" min={0} max={40} step={1} value={detection.rank ?? ''} placeholder="—" aria-label={`Rank: ${detection.item.name}`} onChange={event => { const value = Number(event.target.value); update(detection.id, { rank: event.target.value !== '' && Number.isInteger(value) && value >= 0 && value <= 40 ? value : null }); }} /></td>
        </tr>)}</tbody>
      </table></div>
    </section> : !busy && !status && !error && <p className="import-status" role="status">No equipment matched.</p>)}
    </>}
    </div>
    </section>)}</div>
    {help && <ScreenshotHelp source={help} onClose={() => setHelp(null)} />}
    {viewing && !!scans.length && <ScreenshotGallery images={scans.map(scan => ({ url: scan.url, name: scan.file.name }))} activeUrl={viewing} onSelect={setViewing} onClose={() => setViewing(null)} />}
  </section>;
}
