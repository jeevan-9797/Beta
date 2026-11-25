import { useEffect, useRef, useState } from 'react';
import {
  ChevronDown,
  FileText,
  Pill,
  Play,
  RotateCcw,
  ShieldAlert,
  Snowflake,
  Square,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { getDictionary, type Locale } from '../i18n';

export type DemoScenarioId = 'A' | 'B' | 'C' | 'D';

interface TopBarProps {
  locale: Locale;
  onLocaleChange: (locale: Locale) => void;
  voiceEnabled: boolean;
  onToggleVoice: () => void;
  activeDemo: { id: DemoScenarioId; label: string } | null;
  onRunDemo: (id: DemoScenarioId) => void;
  onStopDemo: () => void;
  onResetDemo: () => void;
}

const DEMO_META: {
  id: DemoScenarioId;
  icon: typeof FileText;
  accent: string;
}[] = [
  { id: 'A', icon: FileText, accent: 'text-[#1F7A55]' },
  { id: 'B', icon: Pill, accent: 'text-[#C26A14]' },
  { id: 'C', icon: ShieldAlert, accent: 'text-[#B42318]' },
  { id: 'D', icon: Snowflake, accent: 'text-[#1B1F6B]' },
];

export function TopBar({
  locale,
  onLocaleChange,
  voiceEnabled,
  onToggleVoice,
  activeDemo,
  onRunDemo,
  onStopDemo,
  onResetDemo,
}: TopBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const t = getDictionary(locale);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setMenuOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  return (
    <header
      className={`bg-[#1B1F6B] text-white border-b border-white/10 shrink-0 z-30 ${
        locale === 'hi' ? 'lang-hi' : ''
      }`}
    >
      <div className="max-w-[1600px] mx-auto px-5 h-14 flex items-center justify-between gap-4">
        {/* Zone 1: Brand mark */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-[#FD9B36] flex items-center justify-center shrink-0 shadow-sm">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              className="text-[#1B1F6B]"
            >
              <path
                d="M12 2L4 5.5V11.5C4 16.8 7.4 21.5 12 23C16.6 21.5 20 16.8 20 11.5V5.5L12 2Z"
                stroke="currentColor"
                strokeWidth="2.1"
                strokeLinejoin="round"
              />
              <circle cx="9.75" cy="11.5" r="1.75" fill="currentColor" />
              <circle cx="14.25" cy="11.5" r="1.75" fill="currentColor" />
              <path
                d="M9.75 13.25V16M14.25 13.25V16"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-2.5 min-w-0">
            <span className="font-display font-extrabold text-lg tracking-tight whitespace-nowrap shrink-0">
              {t.meta.brand}
            </span>
            <span className="text-white/40 hidden sm:inline" aria-hidden="true">
              /
            </span>
            <span className="text-sm text-white/85 font-medium truncate hidden sm:inline">
              {t.meta.tagline}
            </span>
          </div>
        </div>

        {/* Zone 2: Active demo playback indicator (only while running) */}
        {activeDemo && (
          <div className="hidden xl:flex items-center gap-2.5 bg-white/10 border border-white/15 rounded-lg px-3 py-1.5 text-xs font-medium shrink-0">
            <Play className="w-3.5 h-3.5 text-[#FD9B36] fill-[#FD9B36] shrink-0" />
            <span className="whitespace-nowrap text-white/95">
              {t.topBar.playingPrefix}: {activeDemo.label}
            </span>
            <button
              type="button"
              onClick={onStopDemo}
              className="ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white/15 hover:bg-white/25 text-white transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              aria-label={t.topBar.stopDemoAria}
            >
              <Square className="w-2.5 h-2.5 fill-current" />
              <span>{t.topBar.stop}</span>
            </button>
          </div>
        )}

        {/* Zone 3: Global EN | हिंदी toggle, Voice toggle, Reset, Demo menu */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Global Language Toggle: EN | हिंदी */}
          <div
            role="group"
            aria-label={t.topBar.languageToggleAria}
            className="inline-flex items-center bg-[#13164D] border border-white/15 rounded-lg p-0.5 shrink-0"
          >
            <button
              type="button"
              onClick={() => onLocaleChange('en')}
              aria-pressed={locale === 'en'}
              className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                locale === 'en'
                  ? 'bg-[#FD9B36] text-[#141738]'
                  : 'text-white/75 hover:text-white'
              }`}
            >
              EN
            </button>
            <span className="text-white/25 text-[11px] px-0.5 select-none" aria-hidden="true">
              |
            </span>
            <button
              type="button"
              onClick={() => onLocaleChange('hi')}
              aria-pressed={locale === 'hi'}
              className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                locale === 'hi'
                  ? 'bg-[#FD9B36] text-[#141738]'
                  : 'text-white/75 hover:text-white'
              }`}
            >
              हिंदी
            </button>
          </div>

          <button
            type="button"
            onClick={onToggleVoice}
            aria-pressed={voiceEnabled}
            aria-label={voiceEnabled ? t.topBar.muteVoiceAria : t.topBar.unmuteVoiceAria}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap shrink-0 cursor-pointer border ${
              voiceEnabled
                ? 'bg-white/10 border-white/15 text-white hover:bg-white/15'
                : 'bg-white/5 border-white/10 text-white/65 hover:text-white hover:bg-white/10'
            }`}
          >
            {voiceEnabled ? (
              <Volume2 className="w-3.5 h-3.5 text-[#FD9B36]" />
            ) : (
              <VolumeX className="w-3.5 h-3.5" />
            )}
            <span>{voiceEnabled ? t.topBar.voiceOn : t.topBar.voiceMuted}</span>
          </button>

          <button
            type="button"
            onClick={onResetDemo}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/10 hover:bg-white/18 border border-white/15 text-white transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>{t.topBar.resetDemo}</span>
          </button>

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((prev) => !prev)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-[#FD9B36] hover:bg-[#f58e22] text-[#141738] transition-colors shadow-sm whitespace-nowrap shrink-0 cursor-pointer"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{t.topBar.demo}</span>
              <ChevronDown
                className={`w-3.5 h-3.5 transition-transform ${menuOpen ? 'rotate-180' : ''}`}
              />
            </button>

            {menuOpen && (
              <div
                role="menu"
                aria-label={t.topBar.demoMenuAria}
                className={`absolute right-0 mt-2 w-84 bg-white text-[#141738] rounded-xl shadow-xl border border-[#1B1F6B]/15 p-1.5 z-50 ${
                  locale === 'hi' ? 'lang-hi' : ''
                }`}
              >
                <div className="px-3 py-2 border-b border-[#1B1F6B]/10">
                  <p className="text-xs font-semibold text-[#1B1F6B]">{t.topBar.demoMenuTitle}</p>
                  <p className="text-[11px] text-[#575A7B]">{t.topBar.demoMenuSubtitle}</p>
                </div>
                <div className="py-1 space-y-0.5">
                  {DEMO_META.map((item) => {
                    const Icon = item.icon;
                    const scenarioText = t.topBar.scenarios[item.id];
                    return (
                      <button
                        key={item.id}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuOpen(false);
                          onRunDemo(item.id);
                        }}
                        className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-[#F8F2E4] transition-colors flex items-start gap-3 cursor-pointer"
                      >
                        <div
                          className={`mt-0.5 p-1.5 rounded-lg bg-[#F8F2E4] ${item.accent} shrink-0`}
                        >
                          <Icon className="w-4 h-4" />
                        </div>
                        <div className="min-w-0">
                          <span className="text-xs font-bold text-[#1B1F6B] block">
                            {scenarioText.title}
                          </span>
                          <p className="text-[11px] text-[#575A7B] leading-snug mt-0.5">
                            {scenarioText.subtitle}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="pt-1 mt-1 border-t border-[#1B1F6B]/10">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onResetDemo();
                    }}
                    className="w-full text-left px-3 py-2 rounded-lg hover:bg-[#F8F2E4] text-xs font-medium text-[#575A7B] hover:text-[#1B1F6B] flex items-center gap-2 cursor-pointer"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>{t.topBar.resetHousehold}</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
