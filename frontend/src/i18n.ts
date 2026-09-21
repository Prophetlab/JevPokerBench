import {useSyncExternalStore} from 'react';
import {translate, type Language} from './translations';

const storageKey = 'pokerbench-language';
function savedLanguage(): Language {
  try { return localStorage.getItem(storageKey) === 'zh' ? 'zh' : 'en'; }
  catch { return 'en'; }
}
let language: Language = savedLanguage();
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setLanguage(next: Language) {
  language = next;
  try { localStorage.setItem(storageKey, next); } catch { /* Keep the current session usable without storage. */ }
  listeners.forEach(listener => listener());
}
export function useLanguage() {
  const current = useSyncExternalStore(subscribe, () => language, () => 'en' as Language);
  return [current, setLanguage] as const;
}
// App subscribes once; its un-memoized page tree rerenders without remounting forms or replays.
export function t(message: string | null | undefined, ...values: (string | number)[]) {
  return translate(message || '', language, ...values);
}
