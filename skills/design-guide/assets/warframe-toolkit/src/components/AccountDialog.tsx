import { useEffect, useRef, useState } from 'react';
import { Copy, Download, KeyRound, Link } from 'lucide-react';
import { Dialog } from './Dialog';
import { accessKeyLink } from '../lib/account';
import type { AccountUser } from '../lib/account';
import { downloadFile } from '../lib/storage';

type Props = {
  user: AccountUser | null;
  initialMode: 'login' | 'register';
  canIncludeCurrent: boolean;
  dirty: boolean;
  onAuthenticate: (mode: 'login' | 'register', username: string, accessKey: string, includeCurrent: boolean) => Promise<string | undefined>;
  onReplaceAccessKey: () => Promise<string>;
  onSignOut: () => Promise<void>;
  onClose: () => void;
};

export function AccountDialog({ user, initialMode, canIncludeCurrent, dirty, onAuthenticate, onReplaceAccessKey, onSignOut, onClose }: Props) {
  const [mode, setMode] = useState<'account' | 'login' | 'register' | 'key' | 'replace'>(user ? 'account' : initialMode);
  const [username, setUsername] = useState(user?.username ?? ''), [credential, setCredential] = useState('');
  const [issuedKey, setIssuedKey] = useState(''), [keySaved, setKeySaved] = useState(false);
  const [includeCurrent, setIncludeCurrent] = useState(canIncludeCurrent);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [copied, setCopied] = useState('');
  const usernameInput = useRef<HTMLInputElement>(null), keyInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (mode === 'login' || mode === 'register') usernameInput.current?.focus();
    if (mode === 'key') keyInput.current?.focus();
  }, [mode]);
  useEffect(() => {
    if (!issuedKey || keySaved) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [issuedKey, keySaved]);
  const close = () => {
    if (busy) return;
    if (issuedKey && !keySaved) { setError('Save your key or sign-in link, then confirm below.'); return; }
    onClose();
  };
  const showKey = (key: string) => { setIssuedKey(key); setCredential(''); setKeySaved(false); setCopied(''); setError(''); setMode('key'); };
  const copy = async (value: string, label: string) => {
    setError('');
    try { await navigator.clipboard.writeText(value); setCopied(`${label} copied.`); }
    catch { setError('Could not copy. Select and copy the text instead.'); }
  };
  const signInLink = issuedKey ? accessKeyLink(location.href, user?.username ?? username, issuedKey) : '';
  const title = mode === 'key' ? 'Save your access key' : mode === 'replace' ? 'Replace access key' : mode === 'account' ? 'Account' : mode === 'register' ? 'Create account' : 'Sign in';
  const feedback = <>{error && <p className="error-text" role="alert">{error}</p>}{copied && <p className="muted small" role="status">{copied}</p>}</>;
  return <Dialog title={title} onClose={close} className="account-dialog">
    {mode === 'key' ? <div className="account-form">
      <p className="muted small">Save your key or personal sign-in link. You’ll need one to sign in again; there is no email recovery.</p>
      <label>Warframe username<input readOnly value={user?.username ?? username} /></label>
      <label>Access key<input ref={keyInput} className="account-secret" readOnly autoComplete="off" spellCheck={false} value={issuedKey} onFocus={event => event.target.select()} /></label>
      <div className="account-key-actions">
        <button type="button" className="button secondary" onClick={() => void copy(issuedKey, 'Access key')}><Copy size={15} />Copy key</button>
        <button type="button" className="button secondary" onClick={() => downloadFile('grakata-access-key.txt', `Grakata\nUsername: ${user?.username ?? username}\nAccess key: ${issuedKey}\nSign-in link: ${signInLink}\n\nKeep this file private. Anyone with this key or link can access your account.\n`, 'text/plain;charset=utf-8')}><Download size={15} />Download key</button>
      </div>
      <label>Personal sign-in link<input className="account-secret" readOnly autoComplete="off" spellCheck={false} value={signInLink} onFocus={event => event.target.select()} /></label>
      <div className="account-key-actions">
        <button type="button" className="button secondary" onClick={() => void copy(signInLink, 'Sign-in link')}><Link size={15} />Copy sign-in link</button>
        <a className="account-bookmark" href={signInLink} draggable onClick={event => event.preventDefault()} aria-label="Drag personal sign-in link to bookmarks">Drag to bookmarks</a>
      </div>
      <p className="muted small">Add the link to your browser bookmarks to sign in automatically. Keep it private: anyone with the key or link can access your account.</p>
      <label className="check-row"><input type="checkbox" checked={keySaved} onChange={event => { setKeySaved(event.target.checked); setError(''); }} /><span>I have saved my key or sign-in link</span></label>
      {feedback}
      <div className="dialog-footer"><button type="button" className="button primary" disabled={!keySaved} onClick={close}>Done</button></div>
    </div> : mode === 'replace' ? <div className="account-form">
      <p className="muted small">Your current key and bookmarked sign-in links will stop working. Other devices will be signed out. Save the new key or link before closing.</p>
      {feedback}
      <div className="dialog-footer">
        <button type="button" className="button quiet" disabled={busy} onClick={() => { setMode('account'); setError(''); }}>Cancel</button>
        <button type="button" className="button primary" disabled={busy} onClick={async () => { setBusy(true); setError(''); try { showKey(await onReplaceAccessKey()); } catch (error) { setError((error as Error).message); } finally { setBusy(false); } }}>{busy ? 'Replacing…' : 'Replace key'}</button>
      </div>
    </div> : mode === 'account' ? <>
      <p className="account-username">{user!.username}</p>
      <p className="muted small">{dirty ? 'You have unsaved changes.' : 'Your inventory, views, crafting plans, and arsenals belong to this account.'}</p>
      <button type="button" className="button secondary" disabled={busy} onClick={() => { setMode('replace'); setError(''); }}><KeyRound size={15} />Replace access key</button>
      {feedback}
      <div className="dialog-footer"><button type="button" className="button quiet" disabled={busy} onClick={() => { setMode('login'); setError(''); }}>Sign in again</button><button type="button" className="button secondary" disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await onSignOut(); onClose(); } catch (error) { setError((error as Error).message); } finally { setBusy(false); } }}>{busy ? 'Signing out…' : 'Sign out'}</button></div>
    </> : <form className="account-form" onSubmit={async event => {
      event.preventDefault(); setError(''); setBusy(true);
      try {
        const key = await onAuthenticate(mode, username, credential, includeCurrent);
        if (key) showKey(key); else { setCredential(''); onClose(); }
      } catch (error) { setError(error instanceof Error ? error.message : 'Could not sign in. Please try again.'); }
      finally { setBusy(false); }
    }}>
      <label>Warframe username<input ref={usernameInput} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} maxLength={64} required value={username} readOnly={Boolean(user && dirty)} onChange={event => setUsername(event.target.value)} disabled={busy} /></label>
      {mode === 'login' ? <>
        <label>Access key<input name="accessKey" type="password" autoComplete="current-password" autoCapitalize="none" spellCheck={false} maxLength={128} required value={credential} onChange={event => setCredential(event.target.value)} disabled={busy} /></label>
      </> : <>
        <p className="muted small">We’ll generate an access key for you to save. No password or email is needed. If you lose your key and sign-in link, there is no email recovery.</p>
        {canIncludeCurrent && <label className="check-row"><input type="checkbox" checked={includeCurrent} onChange={event => setIncludeCurrent(event.target.checked)} disabled={busy} /><span>Save my current inventory, views, crafting plans, and arsenals to this account</span></label>}
      </>}
      {feedback}
      <div className="dialog-footer"><button type="button" className="button quiet" disabled={busy} onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setCredential(''); setError(''); }}>{mode === 'login' ? 'Create account' : 'Sign in instead'}</button><button type="submit" className="button primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'register' ? 'Create account' : 'Sign in'}</button></div>
    </form>}
  </Dialog>;
}
