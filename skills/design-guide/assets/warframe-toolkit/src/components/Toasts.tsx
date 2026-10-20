import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, Info, TriangleAlert } from 'lucide-react';
import { Dialog } from './Dialog';
import './Toasts.css';

type Tone = 'neutral' | 'success' | 'caution';
type Toast = { id: number; message: string; tone: Tone; createdAt: string; archived: boolean };
type Notify = (message: string, tone?: Tone) => void;
const ToastContext = createContext<Notify>(() => {});
export const useToasts = () => useContext(ToastContext);
const severity = { neutral: 'Notice', success: 'Success', caution: 'Warning' };
const icons = { neutral: Info, success: Check, caution: TriangleAlert };
const timestamp = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });

function ToastItem({ toast, archive, openHistory }: { toast: Toast; archive: (id: number) => void; openHistory: () => void }) {
  const clock = useRef({ remaining: 4500, started: 0, timer: 0 });
  const element = useRef<HTMLDivElement>(null);
  const pause = () => {
    const current = clock.current;
    if (!current.timer) return;
    clearTimeout(current.timer); current.timer = 0;
    current.remaining = Math.max(0, current.remaining - (performance.now() - current.started));
  };
  const resume = () => {
    const current = clock.current;
    if (toast.archived || current.timer || element.current?.matches(':hover') || element.current?.contains(document.activeElement)) return;
    current.started = performance.now();
    current.timer = window.setTimeout(() => { current.timer = 0; archive(toast.id); }, current.remaining);
  };
  useEffect(() => { resume(); return pause; }, [toast.archived]); // Preserve the remaining window across hover and focus.
  const Icon = icons[toast.tone];
  return <div ref={element} className={`toast ${toast.archived ? 'is-archived' : ''}`} data-tone={toast.tone}
    onPointerEnter={pause} onPointerLeave={resume} onFocus={pause} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) requestAnimationFrame(resume); }}>
    <button type="button" className="toast-main" aria-label={`${toast.message} Open notification history`} onClick={openHistory}>
      <Icon size={16} className="toast-icon" aria-hidden="true" focusable="false" /><span className="toast-msg">{toast.message}</span>
    </button>
  </div>;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [history, setHistory] = useState(false);
  const sequence = useRef(0), tray = useRef<HTMLDivElement>(null);
  const notify = useCallback<Notify>((message, tone = 'neutral') => {
    const toast = { id: ++sequence.current, message, tone, createdAt: new Date().toISOString(), archived: false };
    setToasts(current => [...current, toast]);
  }, []);
  const archive = useCallback((id: number) => setToasts(current => current.map(toast => toast.id === id ? { ...toast, archived: true } : toast)), []);
  useLayoutEffect(() => { if (tray.current && !tray.current.matches(':hover, :focus-within')) tray.current.scrollTop = tray.current.scrollHeight; }, [toasts]);
  return <ToastContext.Provider value={notify}>
    {children}
    <div ref={tray} className={`toast-container ${toasts.length ? 'has-history' : ''} ${toasts.length && toasts.every(toast => toast.archived) ? 'is-resting' : ''}`} role="region" aria-label="Notifications">
      {toasts.map(toast => <ToastItem key={toast.id} toast={toast} archive={archive} openHistory={() => setHistory(true)} />)}
    </div>
    <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{toasts.length > 0 && <span key={toasts.at(-1)!.id}>{toasts.at(-1)!.message}</span>}</div>
    {history && <Dialog title="Notification history" className="toast-history-dialog" onClose={() => setHistory(false)}>
      <ol className="toast-history-list">{toasts.slice().reverse().map(toast => {
        const Icon = icons[toast.tone];
        return <li key={toast.id} className="toast-history-item" data-tone={toast.tone}>
          <Icon size={16} className="toast-icon" aria-hidden="true" focusable="false" />
          <div className="toast-history-copy"><span className="toast-history-message">{toast.message}</span><span className="toast-history-time">{severity[toast.tone]} · <time dateTime={toast.createdAt}>{timestamp.format(new Date(toast.createdAt))}</time></span></div>
        </li>;
      })}</ol>
    </Dialog>}
  </ToastContext.Provider>;
}
