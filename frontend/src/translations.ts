import en from './locales/en.json';
import zh from './locales/zh.json';
export type Language = 'en' | 'zh';
const english: Record<string, string> = en;
const chinese: Record<string, string> = zh;
const interpolate = (text: string, values: (string | number)[]) => text.replace(/\{(\d+)\}/g, (whole, index) => String(values[Number(index)] ?? whole));
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Translate server-authored events/errors at display time. Stored records and model inputs stay intact.
const serverTemplates = Object.entries(english).filter(([key]) => /\{\d+\}/.test(key)).map(([key, value]) => ({
  pattern: new RegExp('^' + key.split(/\{\d+\}/).map(escape).join('(.+?)') + '$'), value,
}));
export function translate(message: string, language: Language, ...values: (string | number)[]): string {
  if (/模型身份不符|模型权重版本不符/.test(message)) return language === 'zh' ? '比赛已暂停，请稍后继续。' : 'Match paused. Please resume shortly.';
  if (language === 'zh') return interpolate(chinese[message] ?? message, values);
  if (message in english) return interpolate(english[message], values);
  for (const {pattern, value} of serverTemplates) {
    const match = pattern.exec(message);
    if (match) return interpolate(value, match.slice(1).map(part => translate(part, language)));
  }
  // Validation can combine a field path with one or more translated messages.
  if (/[：；]/.test(message)) return message.split(/([：；])/).map(part => part === '：' ? ': ' : part === '；' ? '; ' : translate(part, language)).join('');
  return message;
}
