import { useState } from 'react';
import {
  Ban,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Eye,
  HelpCircle,
  IndianRupee,
  Receipt as ReceiptIcon,
  ShieldAlert,
  ShieldCheck,
  Snowflake,
  X,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { formatINR } from '../lib/format';
import {
  formatLocalizedISTTime,
  formatLocalizedTimeFromHoursMinutes,
  getDictionary,
  interpolate,
  pickText,
  type Locale,
} from '../i18n';
import type { ApprovalRequest, AuditEvent, HouseholdState, RiskLevel } from '../lib/store';

interface GuardianConsolePanelProps {
  locale: Locale;
  state: HouseholdState;
  activeTab: 'feed' | 'receipts';
  onTabChange: (tab: 'feed' | 'receipts') => void;
  onToggleFreeze: (freeze: boolean) => void;
  onApprove: (approvalId: string, via: 'console' | 'whatsapp') => void;
  onDecline: (approvalId: string, via: 'console' | 'whatsapp') => void;
  onCancelCoolingOff: (approvalId: string) => void;
}

function getRiskChipMeta(level: RiskLevel) {
  switch (level) {
    case 'R0':
      return {
        icon: Eye,
        classes: 'bg-[#EEF2FF] text-[#1B1F6B] border-[#1B1F6B]/20',
      };
    case 'R1':
      return {
        icon: CheckCircle2,
        classes: 'bg-[#ECFDF5] text-[#1F7A55] border-[#1F7A55]/25',
      };
    case 'R2':
      return {
        icon: IndianRupee,
        classes: 'bg-[#ECFDF5] text-[#1F7A55] border-[#1F7A55]/25',
      };
    case 'R3':
      return {
        icon: Clock,
        classes: 'bg-[#FFF7ED] text-[#9A4A07] border-[#FD9B36]/45',
      };
    case 'R4':
      return {
        icon: ShieldAlert,
        classes: 'bg-[#FEF2F2] text-[#B42318] border-[#B42318]/25',
      };
  }
}

function getDecisionBadgeMeta(decision: AuditEvent['decision']) {
  switch (decision) {
    case 'ALLOW':
      return {
        icon: CheckCircle2,
        classes: 'bg-[#1F7A55]/12 text-[#1F7A55] border-[#1F7A55]/25',
      };
    case 'NEEDS_APPROVAL':
      return {
        icon: Clock,
        classes: 'bg-[#FD9B36]/20 text-[#9A4A07] border-[#FD9B36]/45',
      };
    case 'DENY':
      return {
        icon: Ban,
        classes: 'bg-[#B42318]/12 text-[#B42318] border-[#B42318]/25',
      };
  }
}

function CoolingOffRing({
  remaining,
  total,
  unit,
}: {
  remaining: number;
  total: number;
  unit: string;
}) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const progress = total > 0 ? remaining / total : 0;
  const dashOffset = circumference * (1 - progress);

  return (
    <div className="relative w-16 h-16 shrink-0 flex items-center justify-center">
      <svg className="w-16 h-16 -rotate-90" viewBox="0 0 64 64" aria-hidden="true">
        <circle cx="32" cy="32" r={radius} fill="none" stroke="#F3E5CE" strokeWidth="5" />
        <circle
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          stroke="#FD9B36"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          style={{ transition: 'stroke-dashoffset 0.85s linear' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-mono font-bold text-sm tabular-nums text-[#1B1F6B] leading-none">
          {remaining}
          {unit}
        </span>
      </div>
    </div>
  );
}

function ApprovalBanner({
  locale,
  approval,
  onApprove,
  onDecline,
  onCancelCoolingOff,
}: {
  locale: Locale;
  approval: ApprovalRequest;
  onApprove: (id: string, via: 'console' | 'whatsapp') => void;
  onDecline: (id: string, via: 'console' | 'whatsapp') => void;
  onCancelCoolingOff: (id: string) => void;
}) {
  const t = getDictionary(locale);
  const isCooling = approval.status === 'cooling_off';
  const viaLabel =
    approval.approvedVia === 'whatsapp' ? t.console.viaWhatsapp : t.console.viaConsole;

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      data-testid="active-approval-card"
      className={`rounded-2xl p-4 border shrink-0 ${
        isCooling
          ? 'bg-[#FFF9F0] border-[#FD9B36]/60'
          : 'bg-white border-[#FD9B36] shadow-[0_6px_20px_-10px_rgba(253,155,54,0.45)]'
      }`}
    >
      <div className="flex items-center justify-between gap-3 min-w-0">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          {isCooling ? (
            <CoolingOffRing
              remaining={approval.coolingOffRemainingSec}
              total={approval.coolingOffTotalSec}
              unit={t.console.limits.secondsShort}
            />
          ) : (
            <div className="w-11 h-11 rounded-xl bg-[#FD9B36]/20 text-[#9A4A07] flex items-center justify-center shrink-0">
              <Clock className="w-5 h-5" />
            </div>
          )}

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-bold text-[#9A4A07]">
                {isCooling ? t.console.approvalCoolingTitle : t.console.approvalPendingTitle}
              </span>
              <span className="text-xs text-[#666985]" aria-hidden="true">
                ·
              </span>
              <span className="text-xs font-mono tabular-nums font-bold text-[#1B1F6B]">
                {formatINR(approval.amountPaise)}
              </span>
            </div>
            <p className="text-sm font-semibold text-[#141738] leading-snug mt-0.5 break-words">
              {pickText(approval.summary, locale)} — {approval.payee}
            </p>
            <p className="text-xs text-[#575A7B] mt-0.5 leading-snug">
              {isCooling
                ? interpolate(t.console.approvalCoolingSub, { via: viaLabel })
                : interpolate(t.console.approvalPendingSub, {
                    reason: pickText(approval.reason, locale),
                  })}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isCooling ? (
            <button
              type="button"
              onClick={() => onCancelCoolingOff(approval.id)}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#B42318] hover:bg-[#961B12] text-white text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
              <span>{t.console.cancelPaymentBtn}</span>
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onDecline(approval.id, 'console')}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-[#B42318]/30 bg-white hover:bg-[#FEF2F2] text-[#B42318] text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <X className="w-3.5 h-3.5" />
                <span>{t.console.declineBtn}</span>
              </button>
              <button
                type="button"
                onClick={() => onApprove(approval.id, 'console')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#1F7A55] hover:bg-[#176143] text-white text-xs font-semibold transition-colors shadow-sm whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Check className="w-3.5 h-3.5" />
                <span>{t.console.approveBtn}</span>
              </button>
            </>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function FactPill({
  name,
  value,
  danger,
  good,
}: {
  name: string;
  value: string;
  danger?: boolean;
  good?: boolean;
}) {
  const tone = danger
    ? 'bg-[#FEF2F2] text-[#B42318] border-[#B42318]/25'
    : good
      ? 'bg-[#ECFDF5] text-[#1F7A55] border-[#1F7A55]/25'
      : 'bg-[#F8F2E4] text-[#1B1F6B] border-[#1B1F6B]/15';

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-mono ${tone}`}
    >
      <span className="opacity-75">{name}:</span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

export function GuardianConsolePanel({
  locale,
  state,
  activeTab,
  onTabChange,
  onToggleFreeze,
  onApprove,
  onDecline,
  onCancelCoolingOff,
}: GuardianConsolePanelProps) {
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [confirmFreezeOpen, setConfirmFreezeOpen] = useState(false);
  const [raviAvatarBroken, setRaviAvatarBroken] = useState(false);
  const t = getDictionary(locale);

  const activeApproval = Object.values(state.approvals).find(
    (a) => a.status === 'pending' || a.status === 'cooling_off'
  );

  function toggleWhy(id: string) {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  return (
    <section
      aria-label={t.console.sectionAria}
      className={`flex flex-col h-full min-h-0 min-w-0 ${locale === 'hi' ? 'lang-hi' : ''}`}
    >
      {/* Panel label */}
      <div className="flex items-center justify-between mb-2 px-1 shrink-0 gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-[#1B1F6B] truncate">{t.console.title}</h2>
          <p className="text-xs text-[#575A7B] truncate">{t.console.subtitle}</p>
        </div>
        <span className="text-xs text-[#575A7B] whitespace-nowrap shrink-0">
          {t.console.guardianMeta}
        </span>
      </div>

      {/* Main Console Surface */}
      <div className="flex-1 min-h-0 bg-white rounded-2xl border border-[#1B1F6B]/14 shadow-[0_12px_30px_-16px_rgba(27,31,107,0.18)] flex flex-col overflow-hidden">
        {/* Console Top Bar */}
        <div
          className={`px-4 py-3 border-b shrink-0 transition-colors ${
            state.frozen
              ? 'bg-[#FEF2F2] border-[#B42318]/25'
              : 'bg-[#FAF6EC] border-[#1B1F6B]/10'
          }`}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              {!raviAvatarBroken ? (
                <img
                  src={state.guardian.avatar}
                  alt={t.console.raviAvatarAlt}
                  referrerPolicy="no-referrer"
                  onError={() => setRaviAvatarBroken(true)}
                  className="w-10 h-10 rounded-full object-cover ring-2 ring-[#1B1F6B]/15 shrink-0"
                />
              ) : (
                <div className="w-10 h-10 rounded-full bg-[#1B1F6B] text-white font-display font-bold flex items-center justify-center shrink-0">
                  RD
                </div>
              )}
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-display font-bold text-sm text-[#1B1F6B] truncate">
                    {t.console.householdName}
                  </h3>
                  <span
                    data-testid="household-status-pill"
                    className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold border whitespace-nowrap shrink-0 ${
                      state.frozen
                        ? 'bg-[#B42318] text-white border-[#B42318]'
                        : 'bg-[#1F7A55]/12 text-[#1F7A55] border-[#1F7A55]/25'
                    }`}
                  >
                    {state.frozen ? (
                      <>
                        <Snowflake className="w-3 h-3" />
                        <span>{t.console.statusFrozen}</span>
                      </>
                    ) : (
                      <>
                        <ShieldCheck className="w-3 h-3" />
                        <span>{t.console.statusProtected}</span>
                      </>
                    )}
                  </span>
                </div>
                <p className="text-xs text-[#575A7B] truncate">
                  {t.console.savedPayeesPrefix}: {state.savedPayees.join(', ')}
                </p>
              </div>
            </div>

            {/* Big Freeze / Unfreeze Toggle */}
            <div className="shrink-0">
              <button
                type="button"
                onClick={() => setConfirmFreezeOpen(true)}
                className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-colors shadow-sm whitespace-nowrap shrink-0 cursor-pointer ${
                  state.frozen
                    ? 'bg-[#1F7A55] hover:bg-[#176143] text-white'
                    : 'bg-[#B42318] hover:bg-[#961B12] text-white'
                }`}
              >
                {state.frozen ? (
                  <>
                    <ShieldCheck className="w-4 h-4" />
                    <span>{t.console.unfreezeBtn}</span>
                  </>
                ) : (
                  <>
                    <Snowflake className="w-4 h-4" />
                    <span>{t.console.freezeBtn}</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Inline Freeze Confirmation Bar */}
          <AnimatePresence>
            {confirmFreezeOpen && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-hidden"
              >
                <div className="mt-3 p-3 rounded-xl bg-white border border-[#1B1F6B]/15 flex items-center justify-between gap-3">
                  <p className="text-xs text-[#141738] font-medium">
                    {state.frozen
                      ? t.console.confirmUnfreezePrompt
                      : t.console.confirmFreezePrompt}
                  </p>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={() => setConfirmFreezeOpen(false)}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold text-[#575A7B] hover:bg-[#F8F2E4] cursor-pointer whitespace-nowrap"
                    >
                      {t.console.confirmCancel}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        onToggleFreeze(!state.frozen);
                        setConfirmFreezeOpen(false);
                      }}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold text-white cursor-pointer whitespace-nowrap ${
                        state.frozen
                          ? 'bg-[#1F7A55] hover:bg-[#176143]'
                          : 'bg-[#B42318] hover:bg-[#961B12]'
                      }`}
                    >
                      {state.frozen
                        ? t.console.confirmUnfreezeAction
                        : t.console.confirmFreezeAction}
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Active Approval / Cooling-Off Banner */}
        <AnimatePresence>
          {activeApproval && (
            <div className="px-4 pt-3 shrink-0">
              <ApprovalBanner
                locale={locale}
                approval={activeApproval}
                onApprove={onApprove}
                onDecline={onDecline}
                onCancelCoolingOff={onCancelCoolingOff}
              />
            </div>
          )}
        </AnimatePresence>

        {/* Tabs Bar */}
        <div className="px-4 pt-3 pb-2 flex items-center justify-between gap-3 border-b border-[#1B1F6B]/8 shrink-0">
          <div
            role="tablist"
            aria-label={t.console.tabsAria}
            className="inline-flex items-center gap-1 p-1 bg-[#F8F2E4] rounded-xl"
          >
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'feed'}
              onClick={() => onTabChange('feed')}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                activeTab === 'feed'
                  ? 'bg-white text-[#1B1F6B] shadow-sm'
                  : 'text-[#575A7B] hover:text-[#1B1F6B]'
              }`}
            >
              {t.console.tabLiveFeed} ({state.feed.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'receipts'}
              onClick={() => onTabChange('receipts')}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                activeTab === 'receipts'
                  ? 'bg-white text-[#1B1F6B] shadow-sm'
                  : 'text-[#575A7B] hover:text-[#1B1F6B]'
              }`}
            >
              {t.console.tabReceipts} ({state.receipts.length})
            </button>
          </div>

          <div className="text-[11px] font-mono tabular-nums text-[#575A7B] hidden sm:block whitespace-nowrap">
            {t.console.billsDueLabel}: {state.bills.filter((b) => b.status === 'due').length}/
            {state.bills.length}
          </div>
        </div>

        {/* Scrollable Content Region */}
        <div className="flex-1 min-h-0 overflow-y-auto panel-scroll px-4 py-3 space-y-2.5 bg-[#FCFAF5]">
          {activeTab === 'feed' ? (
            state.feed.length === 0 ? (
              <div className="h-full min-h-[210px] flex flex-col items-center justify-center text-center px-6 py-8">
                <div className="w-12 h-12 rounded-2xl bg-[#F8F2E4] text-[#1B1F6B] flex items-center justify-center mb-3">
                  <ShieldCheck className="w-6 h-6" />
                </div>
                <p className="font-display font-bold text-sm text-[#1B1F6B]">
                  {t.console.emptyFeedTitle}
                </p>
                <p className="text-xs text-[#575A7B] max-w-sm mt-1">
                  {t.console.emptyFeedBody}
                </p>
              </div>
            ) : (
              <AnimatePresence initial={false}>
                {state.feed.map((evt) => {
                  const riskMeta = getRiskChipMeta(evt.riskLevel);
                  const RiskIcon = riskMeta.icon;
                  const decMeta = getDecisionBadgeMeta(evt.decision);
                  const DecIcon = decMeta.icon;
                  const isExpanded = Boolean(expandedIds[evt.id]);
                  const summaryText = pickText(evt.summary, locale);
                  const reasonText = pickText(evt.reason, locale);
                  const timeStr = formatLocalizedISTTime(new Date(evt.timestampMs), locale);
                  const istFactDisplay = formatLocalizedTimeFromHoursMinutes(
                    evt.facts.hour_ist,
                    0,
                    locale
                  );

                  return (
                    <motion.article
                      key={evt.id}
                      initial={{ opacity: 0, y: -10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.22 }}
                      className="bg-white rounded-xl border border-[#1B1F6B]/12 p-3.5 shadow-[0_1px_3px_rgba(27,31,107,0.04)]"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[11px] font-mono tabular-nums text-[#666985]">
                              {timeStr}
                            </span>
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold whitespace-nowrap ${riskMeta.classes}`}
                            >
                              <RiskIcon className="w-3 h-3 shrink-0" />
                              <span>{t.risk[evt.riskLevel]}</span>
                            </span>
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-bold whitespace-nowrap ${decMeta.classes}`}
                            >
                              <DecIcon className="w-3 h-3 shrink-0" />
                              <span>{t.decision[evt.decision]}</span>
                            </span>
                          </div>
                          <h4 className="text-sm font-bold text-[#141738] mt-1.5 leading-snug">
                            {summaryText}
                          </h4>
                          <p className="text-xs text-[#575A7B] mt-0.5">{reasonText}</p>
                        </div>

                        <button
                          type="button"
                          onClick={() => toggleWhy(evt.id)}
                          aria-expanded={isExpanded}
                          aria-label={interpolate(t.console.whyBtnAria, { summary: summaryText })}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#F8F2E4] hover:bg-[#EFE5D1] text-[11px] font-semibold text-[#1B1F6B] transition-colors whitespace-nowrap shrink-0 cursor-pointer"
                        >
                          <HelpCircle className="w-3 h-3" />
                          <span>{t.console.whyBtn}</span>
                          {isExpanded ? (
                            <ChevronUp className="w-3 h-3" />
                          ) : (
                            <ChevronDown className="w-3 h-3" />
                          )}
                        </button>
                      </div>

                      <AnimatePresence>
                        {isExpanded && (
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            className="overflow-hidden"
                          >
                            <div className="mt-3 pt-3 border-t border-[#1B1F6B]/10 space-y-2">
                              <div className="flex items-center justify-between gap-2 text-[11px] flex-wrap">
                                <span className="font-semibold text-[#1B1F6B]">
                                  {t.console.factsHeading}
                                </span>
                                <span className="font-mono text-[#575A7B]">{evt.ruleId}</span>
                              </div>
                              <div className="flex flex-wrap gap-1.5">
                                <FactPill
                                  name={t.facts.payee_known}
                                  value={evt.facts.payee_known ? t.facts.yes : t.facts.no}
                                  good={evt.facts.payee_known}
                                />
                                <FactPill name={t.facts.hour_ist} value={istFactDisplay} />
                                <FactPill
                                  name={t.facts.payments_last_hour}
                                  value={String(evt.facts.payments_last_hour)}
                                />
                                <FactPill
                                  name={t.facts.approval_valid}
                                  value={evt.facts.approval_valid ? t.facts.yes : t.facts.no}
                                  good={evt.facts.approval_valid}
                                />
                                <FactPill
                                  name={t.facts.scam_flag}
                                  value={evt.facts.scam_flag ? t.facts.yes : t.facts.no}
                                  danger={evt.facts.scam_flag}
                                />
                                <FactPill
                                  name={t.facts.frozen}
                                  value={evt.facts.frozen ? t.facts.yes : t.facts.no}
                                  danger={evt.facts.frozen}
                                />
                              </div>
                              <div className="text-[11px] font-mono text-[#575A7B] bg-[#FAF6EC] px-2.5 py-1.5 rounded-lg break-all">
                                {t.console.toolLabel}: {evt.tool}({JSON.stringify(evt.sanitizedArgs)})
                              </div>
                              {evt.strippedUntrustedFields.length > 0 && (
                                <p className="text-[11px] text-[#B42318]">
                                  {interpolate(t.console.strippedNote, {
                                    fields: evt.strippedUntrustedFields.join(', '),
                                  })}
                                </p>
                              )}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </motion.article>
                  );
                })}
              </AnimatePresence>
            )
          ) : state.receipts.length === 0 ? (
            <div className="h-full min-h-[210px] flex flex-col items-center justify-center text-center px-6 py-8">
              <div className="w-12 h-12 rounded-2xl bg-[#F8F2E4] text-[#1B1F6B] flex items-center justify-center mb-3">
                <ReceiptIcon className="w-6 h-6" />
              </div>
              <p className="font-display font-bold text-sm text-[#1B1F6B]">
                {t.console.emptyReceiptsTitle}
              </p>
              <p className="text-xs text-[#575A7B] max-w-sm mt-1">
                {t.console.emptyReceiptsBody}
              </p>
            </div>
          ) : (
            <div className="space-y-3" data-testid="receipts-list">
              {state.receipts.map((rcpt) => {
                const timeStr = formatLocalizedISTTime(new Date(rcpt.timestampMs), locale);
                return (
                  <article
                    key={rcpt.id}
                    className="bg-white rounded-xl border border-[#1B1F6B]/12 p-4 space-y-3 shadow-[0_1px_3px_rgba(27,31,107,0.04)]"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h4 className="font-display font-bold text-sm text-[#1B1F6B]">
                          {pickText(rcpt.title, locale)}
                        </h4>
                        <p className="text-[11px] text-[#1F7A55] font-semibold mt-0.5">
                          {t.console.receiptDelivered}
                        </p>
                      </div>
                      <span className="text-[11px] font-mono tabular-nums text-[#666985]">
                        {timeStr}
                      </span>
                    </div>

                    {/* Bilingual English + Hindi summary */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
                      <div className="bg-[#FAF6EC] rounded-lg p-2.5">
                        <span className="block text-[10px] font-bold text-[#575A7B] mb-0.5">
                          {t.console.receiptLangEn}
                        </span>
                        <p className="text-[#141738] leading-relaxed">{rcpt.englishSummary}</p>
                      </div>
                      <div className="bg-[#FAF6EC] rounded-lg p-2.5 lang-hi">
                        <span className="block text-[10px] font-bold text-[#575A7B] mb-0.5">
                          {t.console.receiptLangHi}
                        </span>
                        <p className="text-[#141738]">{rcpt.hindiSummary}</p>
                      </div>
                    </div>

                    {rcpt.calendarCard && (
                      <div className="rounded-xl bg-[#ECFDF5] border border-[#1F7A55]/25 p-3 flex items-start gap-2.5">
                        <Calendar className="w-4 h-4 text-[#1F7A55] mt-0.5 shrink-0" />
                        <div className="text-xs">
                          <p className="font-bold text-[#1F7A55]">{t.console.calendarAdded}</p>
                          <p className="font-semibold text-[#141738] mt-0.5">
                            {pickText(rcpt.calendarCard.title, locale)}
                          </p>
                          <p className="text-[#575A7B] font-mono text-[11px] tabular-nums">
                            {pickText(rcpt.calendarCard.dateTime, locale)} ·{' '}
                            {pickText(rcpt.calendarCard.location, locale)}
                          </p>
                        </div>
                      </div>
                    )}

                    {/* Audit Trail: tool → facts → policy decision → executed */}
                    <div className="pt-2 border-t border-[#1B1F6B]/10">
                      <p className="text-[11px] font-bold text-[#1B1F6B] mb-1.5">
                        {t.console.auditTrailTitle}
                      </p>
                      <div className="grid grid-cols-1 gap-1 text-[11px] font-mono bg-[#F8F2E4]/75 rounded-lg p-2.5 text-[#373A59]">
                        <div>
                          <span className="text-[#666985]">{t.console.auditStepTool}: </span>
                          <span className="text-[#1B1F6B] font-semibold">
                            {rcpt.auditTrail.tool}
                          </span>
                        </div>
                        <div>
                          <span className="text-[#666985]">{t.console.auditStepFacts}: </span>
                          <span>{rcpt.auditTrail.factsSummary}</span>
                        </div>
                        <div>
                          <span className="text-[#666985]">{t.console.auditStepPolicy}: </span>
                          <span className="text-[#1F7A55] font-semibold">
                            {t.decision[rcpt.auditTrail.decision]} ({rcpt.auditTrail.ruleId})
                          </span>
                        </div>
                        <div>
                          <span className="text-[#666985]">{t.console.auditStepExecuted}: </span>
                          <span>{pickText(rcpt.auditTrail.executed, locale)}</span>
                        </div>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>

        {/* Always-Visible Household Limits Card */}
        <div className="bg-[#FAF6EC] border-t border-[#1B1F6B]/12 px-4 py-2.5 shrink-0">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <div>
              <p className="text-[11px] text-[#575A7B]">{t.console.limits.autoApprove}</p>
              <p className="font-mono font-bold tabular-nums text-[#1B1F6B]">
                ≤ {formatINR(state.limits.autoApproveLimitPaise)}
              </p>
            </div>
            <div>
              <p className="text-[11px] text-[#575A7B]">{t.console.limits.absoluteMax}</p>
              <p className="font-mono font-bold tabular-nums text-[#1B1F6B]">
                {formatINR(state.limits.absoluteMaxPaise)}
              </p>
            </div>
            <div>
              <p className="text-[11px] text-[#575A7B]">{t.console.limits.coolingOff}</p>
              <p className="font-mono font-bold tabular-nums text-[#1B1F6B]">
                {state.limits.coolingOffDemoSec} {t.console.limits.secondsShort}{' '}
                <span className="font-sans font-normal text-[10px] text-[#575A7B]">
                  {t.console.limits.coolingOffUnit}
                </span>
              </p>
            </div>
            <div>
              <p className="text-[11px] text-[#575A7B]">{t.console.limits.approvalExpiry}</p>
              <p className="font-mono font-bold tabular-nums text-[#1B1F6B]">
                {state.limits.approvalExpiryHours} {t.console.limits.hoursShort}
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
