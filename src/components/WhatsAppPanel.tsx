import { useEffect, useRef } from 'react';
import {
  Calendar,
  Check,
  CheckCheck,
  PhoneCall,
  ShieldAlert,
  ShieldCheck,
  X,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { formatINR } from '../lib/format';
import {
  formatLocalizedISTTime,
  getDictionary,
  interpolate,
  pickText,
  type Locale,
} from '../i18n';
import type { HouseholdState } from '../lib/store';

interface WhatsAppPanelProps {
  locale: Locale;
  state: HouseholdState;
  onApprove: (approvalId: string, via: 'console' | 'whatsapp') => void;
  onDecline: (approvalId: string, via: 'console' | 'whatsapp') => void;
}

export function WhatsAppPanel({ locale, state, onApprove, onDecline }: WhatsAppPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const t = getDictionary(locale);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [state.whatsappMessages.length]);

  return (
    <section
      aria-label={t.whatsapp.sectionAria}
      className={`flex flex-col h-full min-h-0 min-w-0 ${locale === 'hi' ? 'lang-hi' : ''}`}
    >
      {/* Panel label above phone frame */}
      <div className="flex items-center justify-between mb-2 px-1 shrink-0 gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-[#1B1F6B] truncate">{t.whatsapp.title}</h2>
          <p className="text-xs text-[#575A7B] truncate">{t.whatsapp.subtitle}</p>
        </div>
        <span className="text-xs font-mono text-[#1F7A55] font-semibold whitespace-nowrap shrink-0">
          {t.whatsapp.liveSync}
        </span>
      </div>

      {/* WhatsApp Phone Frame */}
      <div className="flex-1 min-h-0 bg-[#EFEAE2] rounded-[28px] border border-[#1B1F6B]/14 shadow-[0_14px_34px_-16px_rgba(27,31,107,0.22)] flex flex-col overflow-hidden">
        {/* WhatsApp Header */}
        <div className="bg-[#075E54] text-white px-4 py-3 flex items-center gap-3 shrink-0">
          <div className="w-9 h-9 rounded-full bg-[#FD9B36] text-[#1B1F6B] font-display font-extrabold text-xs flex items-center justify-center shrink-0">
            {t.meta.brand}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-display font-bold text-sm truncate">{t.whatsapp.botName}</span>
              <ShieldCheck className="w-3.5 h-3.5 text-[#86EFAC] shrink-0" />
            </div>
            <p className="text-[11px] text-white/80 truncate">{t.whatsapp.botSubtitle}</p>
          </div>
        </div>

        {/* Chat stream */}
        <div
          ref={scrollRef}
          className="flex-1 min-h-0 overflow-y-auto panel-scroll px-3 py-3 space-y-2.5"
          data-testid="whatsapp-stream"
        >
          <div className="text-center">
            <span className="inline-block bg-white/85 text-[#575A7B] text-[10px] font-medium px-2.5 py-0.5 rounded-md shadow-xs">
              {t.whatsapp.dayDivider}
            </span>
          </div>

          <AnimatePresence initial={false}>
            {state.whatsappMessages.map((msg) => {
              const isRavi = msg.sender === 'ravi';
              const approval = msg.approvalId ? state.approvals[msg.approvalId] : undefined;
              const timeStr = formatLocalizedISTTime(new Date(msg.timestampMs), locale);

              return (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                  className={`flex ${isRavi ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[92%] rounded-xl px-3 py-2.5 text-xs shadow-xs ${
                      isRavi
                        ? 'bg-[#D9FDD3] text-[#111B21] rounded-tr-xs'
                        : msg.kind === 'scam_alert'
                          ? 'bg-[#FEF2F2] text-[#7A1811] border border-[#B42318]/35 rounded-tl-xs'
                          : msg.kind === 'freeze_notice'
                            ? 'bg-[#FFF5F5] text-[#7A1811] border border-[#B42318]/20 rounded-tl-xs'
                            : 'bg-white text-[#111B21] rounded-tl-xs'
                    }`}
                  >
                    {msg.title && (
                      <div
                        className={`font-bold text-[12px] mb-1 flex items-center gap-1.5 ${
                          msg.kind === 'scam_alert' || msg.kind === 'freeze_notice'
                            ? 'text-[#B42318]'
                            : msg.kind === 'approval'
                              ? 'text-[#9A4A07]'
                              : 'text-[#075E54]'
                        }`}
                      >
                        {msg.kind === 'scam_alert' && (
                          <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
                        )}
                        <span>{pickText(msg.title, locale)}</span>
                      </div>
                    )}

                    <p className={locale === 'hi' ? 'leading-[1.6]' : 'leading-relaxed'}>
                      {pickText(msg.body, locale)}
                    </p>

                    {msg.secondaryBody && (
                      <p
                        className={`mt-1.5 pt-1.5 border-t border-black/8 text-[#3B4A54] ${
                          locale === 'en' ? 'lang-hi' : 'leading-relaxed'
                        }`}
                      >
                        {pickText(msg.secondaryBody, locale)}
                      </p>
                    )}

                    {/* Scam Alert details + 1930 Cyber Helpline */}
                    {msg.kind === 'scam_alert' && (
                      <div className="mt-2 space-y-1.5">
                        {msg.amountPaise && (
                          <div className="text-[11px] font-mono font-bold text-[#B42318] bg-white/80 rounded-lg px-2 py-1">
                            {interpolate(t.whatsapp.blockedTransfer, {
                              amount: formatINR(msg.amountPaise),
                            })}
                          </div>
                        )}
                        <div className="rounded-lg bg-[#B42318] text-white p-2 flex items-center gap-2">
                          <PhoneCall className="w-4 h-4 shrink-0" />
                          <div className="leading-tight">
                            <p className="font-bold text-[11px]">
                              {interpolate(t.whatsapp.helplineTitle, {
                                number: msg.helpline || '1930',
                              })}
                            </p>
                            <p className="text-[10px] text-white/85">{t.whatsapp.helplineSub}</p>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Google Calendar mock card inside WhatsApp */}
                    {msg.calendarCard && (
                      <div className="mt-2 rounded-lg bg-[#ECFDF5] border border-[#1F7A55]/25 p-2">
                        <div className="flex items-center gap-1 text-[11px] font-bold text-[#1F7A55]">
                          <Calendar className="w-3.5 h-3.5 shrink-0" />
                          <span>{t.whatsapp.calendarAdded}</span>
                        </div>
                        <p className="font-semibold text-[#141738] mt-0.5">
                          {pickText(msg.calendarCard.title, locale)}
                        </p>
                        <p className="text-[10px] font-mono text-[#575A7B] tabular-nums">
                          {pickText(msg.calendarCard.dateTime, locale)}
                        </p>
                      </div>
                    )}

                    {/* Interactive Approve / Decline buttons synced with Console */}
                    {msg.kind === 'approval' && approval && (
                      <div className="mt-2.5 pt-2 border-t border-black/10">
                        {approval.status === 'pending' ? (
                          <div className="grid grid-cols-2 gap-1.5">
                            <button
                              type="button"
                              onClick={() => onApprove(approval.id, 'whatsapp')}
                              aria-label={interpolate(t.whatsapp.approveAria, {
                                summary: pickText(approval.summary, locale),
                              })}
                              className="inline-flex items-center justify-center gap-1 py-2 px-2 rounded-lg bg-[#1F7A55] hover:bg-[#176143] text-white font-bold text-xs transition-colors cursor-pointer"
                            >
                              <Check className="w-3.5 h-3.5 shrink-0" />
                              <span className="truncate">{t.whatsapp.approveBtn}</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => onDecline(approval.id, 'whatsapp')}
                              aria-label={interpolate(t.whatsapp.declineAria, {
                                summary: pickText(approval.summary, locale),
                              })}
                              className="inline-flex items-center justify-center gap-1 py-2 px-2 rounded-lg bg-[#FEF2F2] hover:bg-[#FEE2E2] text-[#B42318] border border-[#B42318]/25 font-bold text-xs transition-colors cursor-pointer"
                            >
                              <X className="w-3.5 h-3.5 shrink-0" />
                              <span className="truncate">{t.whatsapp.declineBtn}</span>
                            </button>
                          </div>
                        ) : (
                          <div className="text-[11px] font-semibold font-mono text-[#1B1F6B] bg-[#F8F2E4] px-2 py-1 rounded-md">
                            {approval.status === 'cooling_off' &&
                              interpolate(t.whatsapp.statusCooling, {
                                sec: approval.coolingOffRemainingSec,
                              })}
                            {approval.status === 'executed' && t.whatsapp.statusExecuted}
                            {approval.status === 'declined' && t.whatsapp.statusDeclined}
                            {approval.status === 'cancelled' && t.whatsapp.statusCancelled}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Timestamp & WhatsApp checkmarks */}
                    <div className="mt-1 flex items-center justify-end gap-1 text-[10px] text-[#667781] font-mono tabular-nums">
                      <span>{timeStr}</span>
                      <CheckCheck className="w-3 h-3 text-[#53BDEB]" />
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>

        {/* Footer info bar inside WhatsApp frame */}
        <div className="bg-[#F0F2F5] px-3 py-2 border-t border-black/8 flex items-center justify-between text-[11px] text-[#667781] shrink-0 gap-2">
          <span className="truncate">{t.whatsapp.footerHint}</span>
          <span className="font-mono text-[10px] whitespace-nowrap shrink-0">
            {t.whatsapp.footerTag}
          </span>
        </div>
      </div>
    </section>
  );
}
