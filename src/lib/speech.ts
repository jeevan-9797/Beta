import type { Locale } from '../i18n';

/**
 * Two-way Browser Web Speech API engine:
 * - SpeechSynthesis for BETA replies (picks hi-IN voice for Hindi, en-IN for English)
 * - SpeechRecognition with live interim transcripts & hands-free two-way conversation support
 */

let activeSpeechTimer: number | null = null;

function clearSafetyTimer() {
  if (activeSpeechTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(activeSpeechTimer);
    activeSpeechTimer = null;
  }
}

export function stopBetaSpeech(): void {
  clearSafetyTimer();
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
  try {
    window.speechSynthesis.cancel();
  } catch {
    // ignore browser quirks
  }
}

export function pickVoiceForLocale(
  voices: SpeechSynthesisVoice[],
  lang: Locale
): SpeechSynthesisVoice | undefined {
  if (lang === 'hi') {
    return (
      voices.find((v) => v.lang.toLowerCase() === 'hi-in') ||
      voices.find((v) => v.lang.toLowerCase().startsWith('hi')) ||
      voices.find((v) => v.lang.toLowerCase().startsWith('en-in'))
    );
  }
  return (
    voices.find((v) => v.lang.toLowerCase() === 'en-in') ||
    voices.find((v) => v.lang.toLowerCase().startsWith('en-in')) ||
    voices.find((v) => v.lang.toLowerCase().startsWith('en'))
  );
}

export function speakBetaReply(
  text: string,
  lang: Locale,
  enabled: boolean,
  callbacks?: { onStart?: () => void; onEnd?: () => void }
): void {
  if (!enabled || !text || typeof window === 'undefined' || !('speechSynthesis' in window)) {
    callbacks?.onEnd?.();
    return;
  }

  let finished = false;
  const finishOnce = () => {
    if (finished) return;
    finished = true;
    clearSafetyTimer();
    callbacks?.onEnd?.();
  };

  try {
    window.speechSynthesis.cancel();
    clearSafetyTimer();

    const utterance = new SpeechSynthesisUtterance(text);
    const voices = window.speechSynthesis.getVoices();
    const chosen = pickVoiceForLocale(voices, lang);

    if (chosen) {
      utterance.voice = chosen;
      utterance.lang = chosen.lang;
    } else {
      utterance.lang = lang === 'hi' ? 'hi-IN' : 'en-IN';
    }

    utterance.rate = lang === 'hi' ? 0.96 : 0.98;
    utterance.pitch = 1.02;

    utterance.onstart = () => {
      callbacks?.onStart?.();
    };
    utterance.onend = () => finishOnce();
    utterance.onerror = () => finishOnce();

    // Safety fallback so hands-free two-way call never hangs in headless/muted browsers
    const estimatedMs = Math.min(18000, Math.max(2500, text.length * 95));
    activeSpeechTimer = window.setTimeout(() => {
      finishOnce();
    }, estimatedMs);

    window.speechSynthesis.speak(utterance);
  } catch {
    finishOnce();
  }
}

export interface BrowserSpeechRecognition {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onstart: (() => void) | null;
  onresult:
    | ((event: {
        resultIndex: number;
        results: ArrayLike<{
          isFinal: boolean;
          0: { transcript: string };
        }>;
      }) => void)
    | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => BrowserSpeechRecognition;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isVoiceInputSupported(): boolean {
  return getSpeechRecognitionCtor() !== null;
}

export function createTwoWayVoiceListener(
  lang: 'hi-IN' | 'en-IN',
  handlers: {
    onStart?: () => void;
    onInterim: (text: string) => void;
    onFinal: (text: string) => void;
    onEnd: () => void;
    onError: (errorCode: string) => void;
  }
): BrowserSpeechRecognition | null {
  const Ctor = getSpeechRecognitionCtor();
  if (!Ctor) return null;

  const recognition = new Ctor();
  recognition.lang = lang;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.continuous = false;

  let deliveredFinal = false;

  recognition.onstart = () => {
    handlers.onStart?.();
  };

  recognition.onresult = (event) => {
    let interim = '';
    let finalText = '';

    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const text = result?.[0]?.transcript ?? '';
      if (result.isFinal) {
        finalText += text;
      } else {
        interim += text;
      }
    }

    if (interim.trim()) {
      handlers.onInterim(interim.trim());
    }

    if (finalText.trim() && !deliveredFinal) {
      deliveredFinal = true;
      handlers.onInterim('');
      handlers.onFinal(finalText.trim());
    }
  };

  recognition.onerror = (event) => {
    handlers.onInterim('');
    handlers.onError(event.error || 'unknown');
  };

  recognition.onend = () => {
    handlers.onInterim('');
    handlers.onEnd();
  };

  return recognition;
}
