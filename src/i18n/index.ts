import { en, type Dictionary } from './en';
import { hi } from './hi';

export type Locale = 'en' | 'hi';

export interface BilingualText {
  en: string;
  hi: string;
}

const STORAGE_KEY = 'beta_ui_locale';

export const DICTIONARIES: Record<Locale, Dictionary> = {
  en,
  hi,
};

export function getDictionary(locale: Locale): Dictionary {
  return DICTIONARIES[locale] ?? en;
}

export function loadSavedLocale(): Locale {
  try {
    if (typeof window === 'undefined') return 'en';
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === 'hi' || raw === 'en') return raw;
  } catch {
    // ignore storage errors in restricted iframes
  }
  return 'en';
}

export function saveLocale(locale: Locale): void {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // ignore storage errors
  }
}

export function interpolate(
  template: string,
  vars: Record<string, string | number | undefined>
): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const val = vars[key];
    return val !== undefined ? String(val) : `{${key}}`;
  });
}

export function pickText(text: BilingualText, locale: Locale): string {
  return locale === 'hi' ? text.hi : text.en;
}

/**
 * Formats a canonical date string ("28 Nov", "02 Dec") for EN or HI.
 * Uses Latin digits in both languages.
 */
export function formatLocalizedDate(dateKey: string, locale: Locale): string {
  const dict = getDictionary(locale);
  const mapped = (dict.dates as Record<string, string>)[dateKey];
  if (mapped) return mapped;
  if (locale === 'hi') {
    return dateKey
      .replace(/\bJan\b/g, 'जनवरी')
      .replace(/\bFeb\b/g, 'फ़रवरी')
      .replace(/\bMar\b/g, 'मार्च')
      .replace(/\bApr\b/g, 'अप्रैल')
      .replace(/\bMay\b/g, 'मई')
      .replace(/\bJun\b/g, 'जून')
      .replace(/\bJul\b/g, 'जुलाई')
      .replace(/\bAug\b/g, 'अगस्त')
      .replace(/\bSep\b/g, 'सितंबर')
      .replace(/\bOct\b/g, 'अक्टूबर')
      .replace(/\bNov\b/g, 'नवंबर')
      .replace(/\bDec\b/g, 'दिसंबर');
  }
  return dateKey;
}

/**
 * Formats a 12-hour IST time string with Latin digits:
 * - EN: "11:00 AM", "2:17 PM"
 * - HI: "सुबह 11:00", "दोपहर 2:17", "शाम 4:30", "रात 8:00"
 */
export function formatLocalizedTimeFromHoursMinutes(
  hour24: number,
  minute: number,
  locale: Locale
): string {
  const h24 = ((hour24 % 24) + 24) % 24;
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  const minStr = String(minute).padStart(2, '0');

  if (locale === 'en') {
    const suffix = h24 >= 12 ? 'PM' : 'AM';
    return `${h12}:${minStr} ${suffix}`;
  }

  let period = 'सुबह';
  if (h24 >= 12 && h24 < 16) {
    period = 'दोपहर';
  } else if (h24 >= 16 && h24 < 20) {
    period = 'शाम';
  } else if (h24 >= 20 || h24 < 4) {
    period = 'रात';
  }
  return `${period} ${h12}:${minStr}`;
}

export function formatLocalizedISTTime(date: Date, locale: Locale): string {
  const parts = new Intl.DateTimeFormat('en-IN', {
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  }).formatToParts(date);

  const hourPart = parts.find((p) => p.type === 'hour')?.value ?? '12';
  const minPart = parts.find((p) => p.type === 'minute')?.value ?? '00';
  const h = parseInt(hourPart, 10) % 24;
  const m = parseInt(minPart, 10) || 0;
  return formatLocalizedTimeFromHoursMinutes(h, m, locale);
}

/**
 * Formats a doctor slot like "29 Nov 11:00 AM" into localized date + time:
 * - EN: "29 Nov 11:00 AM"
 * - HI: "29 नवंबर, सुबह 11:00"
 */
export function formatLocalizedSlot(slot: string, locale: Locale): string {
  if (locale === 'en') return slot;
  const match = slot.match(/^(\d{1,2}\s+[A-Za-z]{3})\s+(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
  if (!match) return formatLocalizedDate(slot, locale);

  const [, datePart, hStr, mStr, ampm] = match;
  let h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10);
  if (ampm.toUpperCase() === 'PM' && h < 12) h += 12;
  if (ampm.toUpperCase() === 'AM' && h === 12) h = 0;

  return `${formatLocalizedDate(datePart, 'hi')}, ${formatLocalizedTimeFromHoursMinutes(h, m, 'hi')}`;
}

/**
 * Detects whether the parent's utterance is Hindi (Devanagari or Romanized Hinglish) vs English.
 * Keeps Parent panel responses matched to what the parent actually spoke or typed.
 */
const HINGLISH_HINT_REGEX =
  /\b(kya|hai|hain|haan|han|nahi|nahin|bijli|dawai|dawa|bhar|karo|karna|aaya|bhi|se|mein|mila|kisi|ko|batana|mat|kitna|rupaye|baje|yaad|pani|paani|kab|mera|meri|mere|mujhe|aap|theek|ji|bhej|paisa|paise|ka|ki|ke)\b/i;

export function detectParentLanguage(message: string, fallback: Locale = 'hi'): Locale {
  const trimmed = (message || '').trim();
  if (!trimmed) return fallback;

  // Any Devanagari character immediately identifies Hindi
  if (/[\u0900-\u097F]/.test(trimmed)) {
    return 'hi';
  }

  // Common Hinglish / Hindi words in Latin script
  if (HINGLISH_HINT_REGEX.test(trimmed)) {
    return 'hi';
  }

  // Otherwise clear English words
  if (/[a-zA-Z]{2,}/.test(trimmed)) {
    return 'en';
  }

  return fallback;
}

export { en, hi };
export type { Dictionary };
