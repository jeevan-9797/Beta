import { useCallback, useEffect, useRef, useState } from 'react';
import { TopBar, type DemoScenarioId } from './components/TopBar';
import { ParentCallPanel, type ParentLangPreference } from './components/ParentCallPanel';
import { GuardianConsolePanel } from './components/GuardianConsolePanel';
import { WhatsAppPanel } from './components/WhatsAppPanel';
import { createInitialHouseholdState, type HouseholdState } from './lib/store';
import { parseParentIntent } from './lib/intent';
import {
  approveGuardianRequest,
  cancelCoolingOff,
  completeCoolingOffAndExecute,
  declineGuardianRequest,
  executeGuardedToolCall,
  tickCoolingOff,
  toggleHouseholdFreeze,
} from './lib/tools';
import { speakBetaReply, stopBetaSpeech } from './lib/speech';
import {
  detectParentLanguage,
  getDictionary,
  loadSavedLocale,
  saveLocale,
  type Locale,
} from './i18n';

export default function App() {
  const [locale, setLocale] = useState<Locale>(() => loadSavedLocale());
  const [parentLang, setParentLang] = useState<Locale>(() => loadSavedLocale());
  const [parentLangPref, setParentLangPref] = useState<ParentLangPreference>('auto');

  const [state, setState] = useState<HouseholdState>(() =>
    createInitialHouseholdState(new Date(), loadSavedLocale())
  );
  const [activeTab, setActiveTab] = useState<'feed' | 'receipts'>('feed');
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [voiceCallActive, setVoiceCallActive] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [callSeconds, setCallSeconds] = useState(0);
  const [demoTypingText, setDemoTypingText] = useState('');
  const [activeDemo, setActiveDemo] = useState<{ id: DemoScenarioId; label: string } | null>(null);

  const stateRef = useRef(state);
  stateRef.current = state;

  const localeRef = useRef(locale);
  localeRef.current = locale;

  const parentLangRef = useRef(parentLang);
  parentLangRef.current = parentLang;

  const parentLangPrefRef = useRef(parentLangPref);
  parentLangPrefRef.current = parentLangPref;

  const voiceEnabledRef = useRef(voiceEnabled);
  voiceEnabledRef.current = voiceEnabled;

  const demoRunIdRef = useRef(0);

  const t = getDictionary(locale);

  // Live call duration timer
  useEffect(() => {
    const timer = window.setInterval(() => {
      setCallSeconds((prev) => prev + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Preload speech synthesis voices on mount
  useEffect(() => {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.getVoices();
    }
    return () => stopBetaSpeech();
  }, []);

  const handleLocaleChange = useCallback((nextLocale: Locale) => {
    setLocale(nextLocale);
    localeRef.current = nextLocale;
    saveLocale(nextLocale);
  }, []);

  const handleParentLangPrefChange = useCallback((pref: ParentLangPreference) => {
    setParentLangPref(pref);
    parentLangPrefRef.current = pref;
    if (pref === 'hi' || pref === 'en') {
      setParentLang(pref);
      parentLangRef.current = pref;
    }
  }, []);

  const speakIfEnabled = useCallback((text: string, lang: Locale) => {
    if (!text) return;
    speakBetaReply(text, lang, voiceEnabledRef.current, {
      onStart: () => setIsSpeaking(true),
      onEnd: () => setIsSpeaking(false),
    });
  }, []);

  const handleInterruptAndSpeak = useCallback(() => {
    stopBetaSpeech();
    setIsSpeaking(false);
  }, []);

  const handleReplayBetaMessage = useCallback((text: string, lang: Locale) => {
    speakBetaReply(text, lang, true, {
      onStart: () => setIsSpeaking(true),
      onEnd: () => setIsSpeaking(false),
    });
  }, []);

  // Cooling-off 1-second ticker & auto-execution at 0s
  const coolingApproval = Object.values(state.approvals).find((a) => a.status === 'cooling_off');
  const coolingApprovalId = coolingApproval?.id;
  const coolingRemaining = coolingApproval?.coolingOffRemainingSec;

  useEffect(() => {
    if (!coolingApprovalId) return;

    if (coolingRemaining !== undefined && coolingRemaining <= 0) {
      const outcome = completeCoolingOffAndExecute(stateRef.current, coolingApprovalId);
      setState(outcome.nextState);
      stateRef.current = outcome.nextState;
      if (outcome.betaReply) {
        speakIfEnabled(outcome.betaReply, outcome.replyLang);
      }
      return;
    }

    const timer = window.setTimeout(() => {
      setState((prev) => {
        const next = tickCoolingOff(prev, coolingApprovalId);
        stateRef.current = next;
        return next;
      });
    }, 1000);

    return () => window.clearTimeout(timer);
  }, [coolingApprovalId, coolingRemaining, speakIfEnabled]);

  const handleParentMessage = useCallback(
    async (rawText: string) => {
      const text = rawText.trim();
      if (!text) return null;

      // Parent panel automatically tracks what language the parent speaks
      const resolvedParentLang: Locale =
        parentLangPrefRef.current === 'auto'
          ? detectParentLanguage(text, parentLangRef.current)
          : parentLangPrefRef.current;

      setParentLang(resolvedParentLang);
      parentLangRef.current = resolvedParentLang;

      setIsProcessing(true);
      try {
        const parsed = await parseParentIntent(text, stateRef.current);
        const outcome = executeGuardedToolCall(stateRef.current, parsed, text, {
          parentLang: resolvedParentLang,
        });
        setState(outcome.nextState);
        stateRef.current = outcome.nextState;
        if (outcome.betaReply) {
          speakIfEnabled(outcome.betaReply, outcome.replyLang);
        }
        return outcome;
      } finally {
        setIsProcessing(false);
      }
    },
    [speakIfEnabled]
  );

  const handleToggleFreeze = useCallback((freeze: boolean) => {
    setState((prev) => {
      const next = toggleHouseholdFreeze(prev, freeze);
      stateRef.current = next;
      return next;
    });
  }, []);

  const handleApprove = useCallback((approvalId: string, via: 'console' | 'whatsapp') => {
    setState((prev) => {
      const next = approveGuardianRequest(prev, approvalId, via);
      stateRef.current = next;
      return next;
    });
  }, []);

  const handleDecline = useCallback(
    (approvalId: string, via: 'console' | 'whatsapp') => {
      const outcome = declineGuardianRequest(stateRef.current, approvalId, via);
      setState(outcome.nextState);
      stateRef.current = outcome.nextState;
      if (outcome.betaReply) {
        speakIfEnabled(outcome.betaReply, outcome.replyLang);
      }
    },
    [speakIfEnabled]
  );

  const handleCancelCoolingOff = useCallback(
    (approvalId: string) => {
      const outcome = cancelCoolingOff(stateRef.current, approvalId);
      setState(outcome.nextState);
      stateRef.current = outcome.nextState;
      if (outcome.betaReply) {
        speakIfEnabled(outcome.betaReply, outcome.replyLang);
      }
    },
    [speakIfEnabled]
  );

  const stopActiveDemo = useCallback(() => {
    demoRunIdRef.current += 1;
    setDemoTypingText('');
    setActiveDemo(null);
  }, []);

  const handleResetDemo = useCallback(() => {
    demoRunIdRef.current += 1;
    stopBetaSpeech();
    setIsSpeaking(false);
    setVoiceCallActive(false);
    setDemoTypingText('');
    setActiveDemo(null);
    setActiveTab('feed');
    setCallSeconds(0);
    const fresh = createInitialHouseholdState(new Date(), localeRef.current);
    setParentLang(localeRef.current);
    parentLangRef.current = localeRef.current;
    setState(fresh);
    stateRef.current = fresh;
  }, []);

  const handleToggleVoice = useCallback(() => {
    setVoiceEnabled((prev) => {
      const next = !prev;
      if (!next) {
        stopBetaSpeech();
        setIsSpeaking(false);
      }
      return next;
    });
  }, []);

  const handleToggleVoiceCall = useCallback(() => {
    setVoiceCallActive((prev) => {
      const next = !prev;
      if (!next) {
        stopBetaSpeech();
        setIsSpeaking(false);
      }
      return next;
    });
  }, []);

  const handleRunDemo = useCallback(
    async (id: DemoScenarioId) => {
      const runId = demoRunIdRef.current + 1;
      demoRunIdRef.current = runId;

      stopBetaSpeech();
      setIsSpeaking(false);
      setVoiceCallActive(false);
      setDemoTypingText('');
      setActiveTab('feed');
      setCallSeconds(0);

      const currentLocale = localeRef.current;
      const dict = getDictionary(currentLocale);
      const fresh = createInitialHouseholdState(new Date(), 'hi');
      setParentLang('hi');
      parentLangRef.current = 'hi';
      setState(fresh);
      stateRef.current = fresh;
      setActiveDemo({ id, label: dict.topBar.scenarios[id].title });

      const isCancelled = () => demoRunIdRef.current !== runId;

      const wait = (ms: number) =>
        new Promise<void>((resolve) => {
          window.setTimeout(resolve, ms);
        });

      const autoTypeAndSend = async (phrase: string) => {
        if (isCancelled()) return null;
        const stepSize = phrase.length > 48 ? 3 : 2;
        for (let i = stepSize; i <= phrase.length; i += stepSize) {
          if (isCancelled()) return null;
          setDemoTypingText(phrase.slice(0, i));
          await wait(24);
        }
        if (isCancelled()) return null;
        setDemoTypingText(phrase);
        await wait(220);
        if (isCancelled()) return null;
        setDemoTypingText('');
        return handleParentMessage(phrase);
      };

      const useDevanagari = currentLocale === 'hi';

      try {
        if (id === 'A') {
          await wait(350);
          await autoTypeAndSend(
            useDevanagari ? 'बिजली का बिल आया क्या?' : 'Bijli ka bill aaya kya?'
          );
          await wait(1600);
          await autoTypeAndSend(useDevanagari ? 'हाँ, भर दो' : 'Haan, bhar do');
          await wait(1700);
          await autoTypeAndSend(
            useDevanagari ? 'डॉक्टर का अपॉइंटमेंट भी' : 'Doctor ka appointment bhi'
          );
        } else if (id === 'B') {
          await wait(350);
          const outcome = await autoTypeAndSend(
            useDevanagari ? 'दवाई ऑर्डर करो' : 'Dawai order karo'
          );
          if (!isCancelled() && outcome?.createdApproval) {
            await wait(1800);
            if (!isCancelled()) {
              handleApprove(outcome.createdApproval.id, 'whatsapp');
            }
          }
        } else if (id === 'C') {
          await wait(350);
          await autoTypeAndSend(
            useDevanagari
              ? 'CBI से फ़ोन आया, पार्सल में ड्रग्स मिला, ₹2 लाख ट्रांसफ़र करना है, किसी को बताना मत'
              : 'CBI se phone aaya, parcel mein drugs mila, ₹2 lakh transfer karna hai, kisi ko batana mat'
          );
        } else if (id === 'D') {
          await wait(500);
          if (!isCancelled()) {
            handleToggleFreeze(true);
          }
          await wait(1100);
          await autoTypeAndSend(
            useDevanagari ? 'Airtel का ₹500 बिल भर दो' : 'Airtel ka ₹500 bill bhar do'
          );
        }
      } finally {
        if (!isCancelled()) {
          setDemoTypingText('');
          setActiveDemo(null);
        }
      }
    },
    [handleApprove, handleParentMessage, handleToggleFreeze]
  );

  return (
    <div className="min-h-screen lg:h-screen lg:overflow-hidden flex flex-col bg-[#F8F2E4] text-[#141738]">
      <TopBar
        locale={locale}
        onLocaleChange={handleLocaleChange}
        voiceEnabled={voiceEnabled}
        onToggleVoice={handleToggleVoice}
        activeDemo={activeDemo}
        onRunDemo={handleRunDemo}
        onStopDemo={stopActiveDemo}
        onResetDemo={handleResetDemo}
      />

      {/* 3-Panel Main Workspace: 30% Left / 45% Center / 25% Right */}
      <main className="flex-1 min-h-0 max-w-[1600px] w-full mx-auto px-4 lg:px-5 py-3.5 overflow-x-hidden">
        <div className="h-full min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,30fr)_minmax(0,45fr)_minmax(0,25fr)] gap-4">
          <ParentCallPanel
            state={state}
            parentLang={parentLang}
            langPreference={parentLangPref}
            onChangeLangPreference={handleParentLangPrefChange}
            callSeconds={callSeconds}
            voiceCallActive={voiceCallActive}
            onToggleVoiceCall={handleToggleVoiceCall}
            isSpeaking={isSpeaking}
            isProcessing={isProcessing}
            demoTypingText={demoTypingText}
            onSendMessage={handleParentMessage}
            onInterruptAndSpeak={handleInterruptAndSpeak}
            onReplayBetaMessage={handleReplayBetaMessage}
          />

          <GuardianConsolePanel
            locale={locale}
            state={state}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            onToggleFreeze={handleToggleFreeze}
            onApprove={handleApprove}
            onDecline={handleDecline}
            onCancelCoolingOff={handleCancelCoolingOff}
          />

          <WhatsAppPanel
            locale={locale}
            state={state}
            onApprove={handleApprove}
            onDecline={handleDecline}
          />
        </div>
      </main>

      {/* Quiet Prototype Footer */}
      <footer
        className={`shrink-0 border-t border-[#1B1F6B]/10 bg-[#F3EBD8]/70 px-5 py-2 text-center ${
          locale === 'hi' ? 'lang-hi' : ''
        }`}
      >
        <p className="text-[11px] text-[#575A7B]">{t.meta.footer}</p>
      </footer>
    </div>
  );
}
