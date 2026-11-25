import { useEffect, useRef, useState } from 'react';
import {
  Calendar,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Send,
  ShieldAlert,
  Volume2,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import type { HouseholdState } from '../lib/store';
import {
  formatLocalizedISTTime,
  getDictionary,
  pickText,
  type Locale,
} from '../i18n';
import {
  createTwoWayVoiceListener,
  isVoiceInputSupported,
  type BrowserSpeechRecognition,
} from '../lib/speech';

export type ParentLangPreference = 'auto' | 'hi' | 'en';

interface ParentCallPanelProps {
  state: HouseholdState;
  parentLang: Locale;
  langPreference: ParentLangPreference;
  onChangeLangPreference: (pref: ParentLangPreference) => void;
  callSeconds: number;
  voiceCallActive: boolean;
  onToggleVoiceCall: () => void;
  isSpeaking: boolean;
  isProcessing: boolean;
  demoTypingText: string;
  onSendMessage: (text: string) => void;
  onInterruptAndSpeak: () => void;
  onReplayBetaMessage: (text: string, lang: Locale) => void;
}

function formatCallDuration(totalSec: number): string {
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function ParentCallPanel({
  state,
  parentLang,
  langPreference,
  onChangeLangPreference,
  callSeconds,
  voiceCallActive,
  onToggleVoiceCall,
  isSpeaking,
  isProcessing,
  demoTypingText,
  onSendMessage,
  onInterruptAndSpeak,
  onReplayBetaMessage,
}: ParentCallPanelProps) {
  const [inputText, setInputText] = useState('');
  const [interimTranscript, setInterimTranscript] = useState('');
  const [isListeningMic, setIsListeningMic] = useState(false);
  const [micNoteKey, setMicNoteKey] = useState<'unsupported' | 'denied' | 'noSpeech' | null>(null);
  const [avatarBroken, setAvatarBroken] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const voiceSupported = isVoiceInputSupported();

  // Parent panel follows what the parent speaks (parentLang)
  const t = getDictionary(parentLang);
  const speechRecLang: 'hi-IN' | 'en-IN' =
    langPreference === 'en' ? 'en-IN' : langPreference === 'hi' ? 'hi-IN' : parentLang === 'en' ? 'en-IN' : 'hi-IN';

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [state.transcript.length, demoTypingText, interimTranscript, isProcessing]);

  // Stop any running mic session on unmount
  useEffect(() => {
    return () => {
      try {
        recognitionRef.current?.abort();
      } catch {
        // ignore
      }
    };
  }, []);

  // Two-way continuous voice call loop:
  // When voiceCallActive is true and BETA is neither speaking nor processing, open the mic automatically.
  useEffect(() => {
    if (!voiceCallActive) {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch {
          // ignore
        }
        recognitionRef.current = null;
      }
      setIsListeningMic(false);
      setInterimTranscript('');
      return;
    }

    if (isSpeaking || isProcessing) {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch {
          // ignore
        }
        recognitionRef.current = null;
      }
      setIsListeningMic(false);
      return;
    }

    if (!voiceSupported) {
      setMicNoteKey('unsupported');
      return;
    }

    let cancelled = false;
    let restartTimer: number | null = null;

    const startTurnListener = () => {
      if (cancelled) return;
      const listener = createTwoWayVoiceListener(speechRecLang, {
        onStart: () => {
          if (!cancelled) {
            setMicNoteKey(null);
            setIsListeningMic(true);
          }
        },
        onInterim: (text) => {
          if (!cancelled) setInterimTranscript(text);
        },
        onFinal: (finalText) => {
          if (!cancelled) {
            setIsListeningMic(false);
            setInterimTranscript('');
            onSendMessage(finalText);
          }
        },
        onError: (code) => {
          if (cancelled) return;
          setIsListeningMic(false);
          if (code === 'not-allowed' || code === 'service-not-allowed') {
            setMicNoteKey('denied');
          } else if (code === 'no-speech') {
            // Silently restart listening in hands-free mode
            restartTimer = window.setTimeout(startTurnListener, 450);
          }
        },
        onEnd: () => {
          if (!cancelled) {
            setIsListeningMic(false);
          }
        },
      });

      if (listener) {
        recognitionRef.current = listener;
        try {
          listener.start();
        } catch {
          // ignore if browser already starting
        }
      }
    };

    const initialDelay = window.setTimeout(startTurnListener, 220);

    return () => {
      cancelled = true;
      window.clearTimeout(initialDelay);
      if (restartTimer !== null) window.clearTimeout(restartTimer);
      try {
        recognitionRef.current?.abort();
      } catch {
        // ignore
      }
      recognitionRef.current = null;
    };
  }, [voiceCallActive, isSpeaking, isProcessing, speechRecLang, voiceSupported, onSendMessage]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = inputText.trim();
    if (!trimmed || isProcessing) return;
    setInputText('');
    setMicNoteKey(null);
    onSendMessage(trimmed);
  }

  function handleSingleTurnMic() {
    if (isListeningMic) {
      try {
        recognitionRef.current?.stop();
      } catch {
        // ignore
      }
      setIsListeningMic(false);
      setInterimTranscript('');
      return;
    }

    if (!voiceSupported) {
      setMicNoteKey('unsupported');
      return;
    }

    if (isSpeaking) {
      onInterruptAndSpeak();
    }

    setMicNoteKey(null);
    const listener = createTwoWayVoiceListener(speechRecLang, {
      onStart: () => setIsListeningMic(true),
      onInterim: (text) => setInterimTranscript(text),
      onFinal: (finalText) => {
        setIsListeningMic(false);
        setInterimTranscript('');
        onSendMessage(finalText);
      },
      onEnd: () => {
        setIsListeningMic(false);
        setInterimTranscript('');
      },
      onError: (code) => {
        setIsListeningMic(false);
        setInterimTranscript('');
        if (code === 'not-allowed' || code === 'service-not-allowed') {
          setMicNoteKey('denied');
        } else if (code === 'no-speech') {
          setMicNoteKey('noSpeech');
        }
      },
    });

    if (listener) {
      recognitionRef.current = listener;
      try {
        listener.start();
      } catch {
        setMicNoteKey('denied');
      }
    }
  }

  function cycleLangPref() {
    const order: ParentLangPreference[] = ['auto', 'hi', 'en'];
    const idx = order.indexOf(langPreference);
    onChangeLangPreference(order[(idx + 1) % order.length]);
  }

  const statusLabel = isProcessing
    ? t.parentPanel.statusChecking
    : isSpeaking
      ? t.parentPanel.statusSpeaking
      : isListeningMic
        ? t.parentPanel.statusListeningLive
        : t.parentPanel.statusListening;

  const micNoteText =
    micNoteKey === 'unsupported'
      ? t.parentPanel.micUnsupported
      : micNoteKey === 'denied'
        ? t.parentPanel.micDenied
        : micNoteKey === 'noSpeech'
          ? t.parentPanel.micNoSpeech
          : null;

  const quickPrompts = [
    t.parentPanel.quickPrompts.billCheck,
    t.parentPanel.quickPrompts.payYes,
    t.parentPanel.quickPrompts.meds,
    t.parentPanel.quickPrompts.doctor,
  ];

  const langPrefLabel =
    langPreference === 'auto'
      ? t.parentPanel.langModeAuto
      : langPreference === 'hi'
        ? t.parentPanel.langModeHi
        : t.parentPanel.langModeEn;

  return (
    <section
      aria-label={t.parentPanel.sectionAria}
      className={`flex flex-col h-full min-h-0 min-w-0 ${parentLang === 'hi' ? 'lang-hi' : ''}`}
    >
      {/* Panel label above phone frame */}
      <div className="flex items-center justify-between mb-2 px-1 shrink-0 gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-[#1B1F6B] truncate">{t.parentPanel.title}</h2>
          <p className="text-xs text-[#575A7B] truncate">{t.parentPanel.subtitle}</p>
        </div>
        <span className="text-xs font-mono tabular-nums text-[#575A7B] whitespace-nowrap shrink-0">
          {t.parentPanel.phoneMeta}
        </span>
      </div>

      {/* Phone Frame */}
      <div className="flex-1 min-h-0 bg-white rounded-[28px] border border-[#1B1F6B]/14 shadow-[0_14px_34px_-16px_rgba(27,31,107,0.22)] flex flex-col overflow-hidden">
        {/* Call Header */}
        <div className="bg-[#1B1F6B] text-white px-4 pt-3.5 pb-3 shrink-0">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0">
              {!avatarBroken ? (
                <img
                  src={state.parent.avatar}
                  alt={t.parentPanel.parentAvatarAlt}
                  referrerPolicy="no-referrer"
                  onError={() => setAvatarBroken(true)}
                  className="w-11 h-11 rounded-full object-cover ring-2 ring-[#FD9B36]/80 shrink-0"
                />
              ) : (
                <div className="w-11 h-11 rounded-full bg-[#FD9B36] text-[#1B1F6B] font-display font-bold flex items-center justify-center shrink-0">
                  KD
                </div>
              )}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-display font-bold text-sm truncate">
                    {t.parentPanel.parentNameAge}
                  </span>
                  <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-white/12 text-[#FD9B36] whitespace-nowrap shrink-0">
                    {parentLang === 'hi' ? t.parentPanel.langBadgeHi : t.parentPanel.langBadgeEn}
                  </span>
                </div>
                <p className="text-xs text-white/75 truncate">{t.parentPanel.connectedSub}</p>
              </div>
            </div>

            <div className="inline-flex items-center gap-1.5 text-xs font-mono tabular-nums text-white/95 bg-white/10 px-2.5 py-1 rounded-full shrink-0">
              <Phone className="w-3 h-3 text-[#FD9B36]" />
              <span>{formatCallDuration(callSeconds)}</span>
            </div>
          </div>

          {/* Two-Way Voice Bar: waveform + Start/End Voice Call + Barge-in */}
          <div className="mt-3 flex items-center justify-between gap-2 bg-[#13164D] rounded-xl px-3 py-2 border border-white/10">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="flex items-center gap-1 h-4 shrink-0" aria-hidden="true">
                <span
                  className={`w-1 rounded-full h-3.5 origin-center ${
                    isSpeaking
                      ? 'bg-[#FD9B36]'
                      : isListeningMic
                        ? 'bg-[#34D399]'
                        : 'bg-[#6EE7B7]'
                  } animate-wave-1`}
                />
                <span
                  className={`w-1 rounded-full h-4 origin-center ${
                    isSpeaking
                      ? 'bg-[#FD9B36]'
                      : isListeningMic
                        ? 'bg-[#34D399]'
                        : 'bg-[#6EE7B7]'
                  } animate-wave-2`}
                />
                <span
                  className={`w-1 rounded-full h-2.5 origin-center ${
                    isSpeaking
                      ? 'bg-[#FD9B36]'
                      : isListeningMic
                        ? 'bg-[#34D399]'
                        : 'bg-[#6EE7B7]'
                  } animate-wave-3`}
                />
                <span
                  className={`w-1 rounded-full h-3.5 origin-center ${
                    isSpeaking
                      ? 'bg-[#FD9B36]'
                      : isListeningMic
                        ? 'bg-[#34D399]'
                        : 'bg-[#6EE7B7]'
                  } animate-wave-4`}
                />
              </div>
              <span className="text-xs font-medium text-white/95 truncate">{statusLabel}</span>
            </div>

            <div className="flex items-center gap-1.5 shrink-0">
              {isSpeaking ? (
                <button
                  type="button"
                  onClick={onInterruptAndSpeak}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#FD9B36] hover:bg-[#f58e22] text-[#141738] text-[11px] font-bold transition-colors whitespace-nowrap cursor-pointer"
                >
                  <Mic className="w-3 h-3" />
                  <span>{t.parentPanel.interruptBtn}</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onToggleVoiceCall}
                  aria-pressed={voiceCallActive}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors whitespace-nowrap cursor-pointer ${
                    voiceCallActive
                      ? 'bg-[#B42318] hover:bg-[#961B12] text-white'
                      : 'bg-[#1F7A55] hover:bg-[#176143] text-white'
                  }`}
                >
                  {voiceCallActive ? (
                    <>
                      <PhoneOff className="w-3 h-3" />
                      <span>{t.parentPanel.endCallBtn}</span>
                    </>
                  ) : (
                    <>
                      <Phone className="w-3 h-3" />
                      <span>{t.parentPanel.startCallBtn}</span>
                    </>
                  )}
                </button>
              )}
            </div>
          </div>

          {voiceCallActive && (
            <div className="mt-1.5 flex items-center justify-between text-[11px] text-white/75 px-0.5">
              <span className="truncate">{t.parentPanel.handsFreeHint}</span>
              <span className="font-mono text-[10px] text-white/60 shrink-0 ml-2">
                {t.parentPanel.noOtpTag}
              </span>
            </div>
          )}
        </div>

        {/* Transcript area */}
        <div
          ref={scrollRef}
          className="flex-1 min-h-0 overflow-y-auto panel-scroll bg-[#FBF8F1] px-3.5 py-3.5 space-y-3"
          data-testid="parent-transcript"
        >
          <AnimatePresence initial={false}>
            {state.transcript.map((msg) => {
              const isParent = msg.sender === 'parent';
              const msgDict = getDictionary(msg.lang);
              const timeStr = formatLocalizedISTTime(new Date(msg.timestampMs), parentLang);

              return (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                  className={`flex flex-col ${isParent ? 'items-end' : 'items-start'} ${
                    msg.lang === 'hi' ? 'lang-hi' : ''
                  }`}
                >
                  <div className="flex items-center gap-1.5 mb-1 px-1 text-[11px] text-[#666985] flex-wrap">
                    <span className="font-semibold">
                      {isParent ? t.parentPanel.senderParent : t.parentPanel.senderBeta}
                    </span>
                    <span aria-hidden="true">·</span>
                    <span className="font-mono tabular-nums">{timeStr}</span>
                    {msg.intentTag && (
                      <>
                        <span aria-hidden="true">·</span>
                        <span
                          className={`font-medium ${
                            msg.isScamWarning ? 'text-[#B42318]' : 'text-[#1B1F6B]'
                          }`}
                        >
                          {pickText(msg.intentTag, parentLang)}
                        </span>
                      </>
                    )}
                    {!isParent && (
                      <button
                        type="button"
                        onClick={() => onReplayBetaMessage(msg.text, msg.lang)}
                        aria-label={t.parentPanel.replayAria}
                        className="ml-0.5 p-0.5 rounded hover:bg-[#1B1F6B]/10 text-[#1B1F6B]/70 hover:text-[#1B1F6B] transition-colors cursor-pointer"
                      >
                        <Volume2 className="w-3 h-3" />
                      </button>
                    )}
                  </div>

                  <div
                    className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-[13.5px] ${
                      msg.lang === 'hi' ? 'leading-[1.6]' : 'leading-relaxed'
                    } ${
                      isParent
                        ? 'bg-[#1B1F6B] text-white rounded-tr-sm'
                        : msg.isScamWarning
                          ? 'bg-[#FEF2F2] text-[#7A1811] border border-[#B42318]/30 rounded-tl-sm'
                          : 'bg-white text-[#141738] border border-[#1B1F6B]/10 rounded-tl-sm shadow-[0_1px_2px_rgba(27,31,107,0.05)]'
                    }`}
                  >
                    {msg.isScamWarning && (
                      <div className="flex items-center gap-1.5 text-xs font-bold text-[#B42318] mb-1">
                        <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
                        <span>{msgDict.parentPanel.scamBanner}</span>
                      </div>
                    )}
                    <p>{msg.text}</p>

                    {msg.calendarCard && (
                      <div className="mt-2.5 p-2.5 rounded-xl bg-[#F8F2E4] border border-[#1B1F6B]/12 text-[#141738]">
                        <div className="flex items-center gap-1.5 text-[11px] font-bold text-[#1F7A55]">
                          <Calendar className="w-3.5 h-3.5 shrink-0" />
                          <span>{t.parentPanel.calendarAdded}</span>
                        </div>
                        <p className="text-xs font-semibold text-[#1B1F6B] mt-1">
                          {pickText(msg.calendarCard.title, parentLang)}
                        </p>
                        <p className="text-[11px] text-[#575A7B] font-mono tabular-nums mt-0.5">
                          {pickText(msg.calendarCard.dateTime, parentLang)} ·{' '}
                          {pickText(msg.calendarCard.location, parentLang)}
                        </p>
                      </div>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>

          {/* Live voice recognition interim bubble */}
          {interimTranscript && (
            <div className="flex flex-col items-end">
              <div className="text-[11px] text-[#1F7A55] font-semibold mb-1 px-1">
                {t.parentPanel.liveInterimLabel}
              </div>
              <div className="max-w-[90%] rounded-2xl rounded-tr-sm px-3.5 py-2.5 text-[13.5px] bg-[#1B1F6B]/80 text-white border border-[#FD9B36]/50">
                {interimTranscript}
                <span className="inline-block w-1.5 h-3.5 bg-[#FD9B36] ml-1 align-middle animate-pulse" />
              </div>
            </div>
          )}

          {/* Auto-typing preview during demo scenarios */}
          {demoTypingText && (
            <div className="flex flex-col items-end">
              <div className="text-[11px] text-[#666985] mb-1 px-1">
                {t.parentPanel.demoTypingLabel}
              </div>
              <div className="max-w-[90%] rounded-2xl rounded-tr-sm px-3.5 py-2.5 text-[13.5px] bg-[#1B1F6B]/85 text-white">
                {demoTypingText}
                <span className="inline-block w-1.5 h-3.5 bg-[#FD9B36] ml-1 align-middle animate-pulse" />
              </div>
            </div>
          )}
        </div>

        {/* Input controls */}
        <div className="bg-white border-t border-[#1B1F6B]/10 p-3 shrink-0">
          {micNoteText && (
            <p className="text-[11px] text-[#B42318] mb-2 px-1" role="status">
              {micNoteText}
            </p>
          )}

          {/* Quick phrase helpers for manual live testing */}
          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            {quickPrompts.map((phrase) => (
              <button
                key={phrase}
                type="button"
                disabled={isProcessing}
                onClick={() => onSendMessage(phrase)}
                className="px-2 py-1 rounded-lg bg-[#F8F2E4] hover:bg-[#EFE5D1] text-[11px] font-medium text-[#1B1F6B] transition-colors whitespace-nowrap shrink-0 cursor-pointer disabled:opacity-50"
              >
                {phrase}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="flex items-center gap-2">
            <button
              type="button"
              onClick={cycleLangPref}
              aria-label={t.parentPanel.langSwitchAria}
              className="px-2 py-2 rounded-xl border border-[#1B1F6B]/15 text-[11px] font-semibold text-[#1B1F6B] hover:bg-[#F8F2E4] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              {langPrefLabel}
            </button>

            <button
              type="button"
              onClick={handleSingleTurnMic}
              aria-label={isListeningMic ? t.parentPanel.micStopAria : t.parentPanel.micStartAria}
              aria-pressed={isListeningMic}
              className={`w-9 h-9 rounded-xl flex items-center justify-center transition-colors shrink-0 cursor-pointer ${
                isListeningMic
                  ? 'bg-[#B42318] text-white'
                  : 'bg-[#F8F2E4] text-[#1B1F6B] hover:bg-[#EFE5D1]'
              }`}
            >
              {isListeningMic ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            </button>

            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={t.parentPanel.inputPlaceholder}
              aria-label={t.parentPanel.inputAria}
              disabled={isProcessing}
              className="flex-1 min-w-0 bg-[#F8F2E4]/75 border border-[#1B1F6B]/15 rounded-xl px-3 py-2 text-sm text-[#141738] placeholder:text-[#787B97] focus:outline-none focus:ring-2 focus:ring-[#FD9B36] focus:border-transparent"
            />

            <button
              type="submit"
              disabled={!inputText.trim() || isProcessing}
              aria-label={t.parentPanel.sendAria}
              className="w-9 h-9 rounded-xl bg-[#1B1F6B] hover:bg-[#13164D] disabled:opacity-40 text-white flex items-center justify-center transition-colors shrink-0 cursor-pointer"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
