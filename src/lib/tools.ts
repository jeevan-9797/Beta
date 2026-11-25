import { formatINR, getISTHour, nextId } from './format';
import { evaluatePolicy, interceptToolCall } from './policy';
import {
  detectParentLanguage,
  en,
  formatLocalizedDate,
  formatLocalizedISTTime,
  formatLocalizedSlot,
  getDictionary,
  hi,
  interpolate,
  type BilingualText,
  type Locale,
} from '../i18n';
import type { ParsedIntent } from './intent';
import type {
  ApprovalRequest,
  AuditEvent,
  CalendarCardData,
  GuardianFacts,
  HouseholdState,
  Receipt,
  ToolName,
  TranscriptMessage,
  WhatsAppMessage,
} from './store';

export interface ExecutionOutcome {
  nextState: HouseholdState;
  betaReply: string;
  replyLang: Locale;
  auditEvent?: AuditEvent;
  createdApproval?: ApprovalRequest;
}

function summarizeFacts(facts: GuardianFacts): string {
  return `payee_known=${facts.payee_known}, hour_ist=${facts.hour_ist}, payments_1h=${facts.payments_last_hour}, approval=${facts.approval_valid}, scam=${facts.scam_flag}, frozen=${facts.frozen}`;
}

function buildIdempotencyKey(tool: ToolName, args: Record<string, unknown>): string {
  const payee = String(args.payee ?? args.biller ?? 'none').toLowerCase();
  const amount = typeof args.amountPaise === 'number' ? args.amountPaise : 0;
  return `${tool}:${payee}:${amount}`;
}

function buildEventSummary(
  tool: ToolName,
  args: Record<string, unknown>,
  state: HouseholdState
): BilingualText {
  const payee =
    typeof args.payee === 'string'
      ? args.payee
      : typeof args.biller === 'string'
        ? args.biller
        : '';
  const amountPaise = typeof args.amountPaise === 'number' ? args.amountPaise : undefined;
  const amountStr = amountPaise !== undefined ? formatINR(amountPaise) : '';

  switch (tool) {
    case 'get_due_bills':
      return payee
        ? {
            en: interpolate(en.events.checkedBiller, { payee }),
            hi: interpolate(hi.events.checkedBiller, { payee }),
          }
        : {
            en: en.events.checkedAllBills,
            hi: hi.events.checkedAllBills,
          };
    case 'pay_bill':
      return {
        en: interpolate(en.events.payBill, { payee: payee || 'Payee', amount: amountStr }).trim(),
        hi: interpolate(hi.events.payBill, { payee: payee || 'भुगतानकर्ता', amount: amountStr }).trim(),
      };
    case 'find_slots':
      return {
        en: interpolate(en.events.findSlots, { doctor: state.doctor.name }),
        hi: interpolate(hi.events.findSlots, { doctor: state.doctor.nameHi }),
      };
    case 'book_appointment': {
      const slot = String(args.slot ?? state.doctor.availableSlots[0]);
      return {
        en: interpolate(en.events.bookAppointment, {
          doctor: state.doctor.name,
          slot: formatLocalizedSlot(slot, 'en'),
        }),
        hi: interpolate(hi.events.bookAppointment, {
          doctor: state.doctor.nameHi,
          slot: formatLocalizedSlot(slot, 'hi'),
        }),
      };
    }
    case 'reorder_medicine':
      return {
        en: interpolate(en.events.reorderMedicine, {
          payee: payee || 'Pharmacy',
          amount: amountStr,
        }).trim(),
        hi: interpolate(hi.events.reorderMedicine, {
          payee: payee || 'Pharmacy',
          amount: amountStr,
        }).trim(),
      };
    case 'set_reminder': {
      const time = String(args.reminderTime ?? '8:00 PM');
      return {
        en: interpolate(en.events.setReminder, { time }),
        hi: interpolate(hi.events.setReminder, { time }),
      };
    }
    case 'request_approval':
      return {
        en: interpolate(en.events.requestApproval, {
          payee: payee || 'Payee',
          amount: amountStr,
        }).trim(),
        hi: interpolate(hi.events.requestApproval, {
          payee: payee || 'भुगतानकर्ता',
          amount: amountStr,
        }).trim(),
      };
    case 'assess_risk':
      return {
        en: interpolate(en.events.assessRisk, { amount: amountStr }).trim(),
        hi: interpolate(hi.events.assessRisk, { amount: amountStr }).trim(),
      };
    default:
      return { en: String(tool), hi: String(tool) };
  }
}

export function executeGuardedToolCall(
  state: HouseholdState,
  parsed: ParsedIntent,
  rawMessage: string,
  options?: {
    approvalId?: string;
    nowMs?: number;
    skipParentMessageAppend?: boolean;
    parentLang?: Locale;
  }
): ExecutionOutcome {
  const nowMs = options?.nowMs ?? Date.now();
  const nowDate = new Date(nowMs);
  const replyLang: Locale = options?.parentLang ?? detectParentLanguage(rawMessage, 'hi');
  const replyDict = getDictionary(replyLang);

  const parentMsg: TranscriptMessage | null = options?.skipParentMessageAppend
    ? null
    : {
        id: nextId('msg_p'),
        sender: 'parent',
        text: rawMessage,
        lang: replyLang,
        timestampMs: nowMs,
      };

  const baseTranscript = parentMsg ? [...state.transcript, parentMsg] : [...state.transcript];

  if (parsed.tool === 'general_help') {
    const betaReply = replyDict.betaReplies.generalHelp;
    const nextState: HouseholdState = {
      ...state,
      transcript: [
        ...baseTranscript,
        { id: nextId('msg_b'), sender: 'beta', text: betaReply, lang: replyLang, timestampMs: nowMs },
      ],
    };
    return { nextState, betaReply, replyLang };
  }

  const tool = parsed.tool;

  // Step 1: Interceptor computes 6 trusted facts
  const intercepted = interceptToolCall(tool, parsed.args, state, {
    messageText: rawMessage,
    approvalId: options?.approvalId,
    nowMs,
  });

  // Step 2: Policy engine makes the ONLY decision
  const policy = evaluatePolicy(tool, intercepted.sanitizedArgs, intercepted.facts, state.limits);

  const payeeStr =
    typeof intercepted.sanitizedArgs.payee === 'string'
      ? intercepted.sanitizedArgs.payee
      : typeof intercepted.sanitizedArgs.biller === 'string'
        ? intercepted.sanitizedArgs.biller
        : undefined;
  const amountPaise =
    typeof intercepted.sanitizedArgs.amountPaise === 'number'
      ? intercepted.sanitizedArgs.amountPaise
      : undefined;

  const summaryBi = buildEventSummary(tool, intercepted.sanitizedArgs, state);

  const auditEvent: AuditEvent = {
    id: nextId('evt'),
    timestampMs: nowMs,
    tool,
    summary: summaryBi,
    amountPaise,
    payee: payeeStr,
    riskLevel: policy.riskLevel,
    decision: policy.decision,
    reason: policy.reasonBilingual,
    ruleId: policy.ruleId,
    facts: intercepted.facts,
    sanitizedArgs: intercepted.sanitizedArgs,
    strippedUntrustedFields: intercepted.strippedUntrustedFields,
  };

  // --- DECISION: DENY ---
  if (policy.decision === 'DENY') {
    if (intercepted.facts.scam_flag) {
      const betaReply =
        replyLang === 'hi'
          ? intercepted.scamDetails.betaReplyBilingual.hi
          : intercepted.scamDetails.betaReplyBilingual.en;

      const waAlert: WhatsAppMessage = {
        id: nextId('wa_scam'),
        sender: 'beta',
        timestampMs: nowMs,
        kind: 'scam_alert',
        title: intercepted.scamDetails.alertTitle,
        body: intercepted.scamDetails.alertDetails,
        amountPaise: amountPaise && amountPaise > 0 ? amountPaise : undefined,
        helpline: '1930',
      };

      const nextState: HouseholdState = {
        ...state,
        feed: [auditEvent, ...state.feed],
        transcript: [
          ...baseTranscript,
          {
            id: nextId('msg_b'),
            sender: 'beta',
            text: betaReply,
            lang: replyLang,
            timestampMs: nowMs,
            intentTag: { en: en.tags.scamBlocked, hi: hi.tags.scamBlocked },
            isScamWarning: true,
          },
        ],
        whatsappMessages: [...state.whatsappMessages, waAlert],
      };

      return { nextState, betaReply, replyLang, auditEvent };
    }

    if (intercepted.facts.frozen) {
      const betaReply = replyDict.betaReplies.frozenBlocked;
      const waFreezeBlock: WhatsAppMessage = {
        id: nextId('wa_frozen'),
        sender: 'beta',
        timestampMs: nowMs,
        kind: 'freeze_notice',
        title: { en: en.waMessages.freezeBlockTitle, hi: hi.waMessages.freezeBlockTitle },
        body: {
          en: interpolate(en.waMessages.freezeBlockBody, { summary: summaryBi.en }),
          hi: interpolate(hi.waMessages.freezeBlockBody, { summary: summaryBi.hi }),
        },
      };

      const nextState: HouseholdState = {
        ...state,
        feed: [auditEvent, ...state.feed],
        transcript: [
          ...baseTranscript,
          {
            id: nextId('msg_b'),
            sender: 'beta',
            text: betaReply,
            lang: replyLang,
            timestampMs: nowMs,
            intentTag: { en: en.tags.frozenDenied, hi: hi.tags.frozenDenied },
          },
        ],
        whatsappMessages: [...state.whatsappMessages, waFreezeBlock],
      };
      return { nextState, betaReply, replyLang, auditEvent };
    }

    const maxStr = formatINR(state.limits.absoluteMaxPaise);
    const amountStr = amountPaise ? formatINR(amountPaise) : maxStr;
    const betaReply = interpolate(replyDict.betaReplies.capBlocked, {
      amount: amountStr,
      max: maxStr,
    });
    const waCapBlock: WhatsAppMessage = {
      id: nextId('wa_cap'),
      sender: 'beta',
      timestampMs: nowMs,
      kind: 'freeze_notice',
      title: { en: en.waMessages.capBlockTitle, hi: hi.waMessages.capBlockTitle },
      body: {
        en: interpolate(en.waMessages.capBlockBody, { summary: summaryBi.en, max: maxStr }),
        hi: interpolate(hi.waMessages.capBlockBody, { summary: summaryBi.hi, max: maxStr }),
      },
    };
    const nextState: HouseholdState = {
      ...state,
      feed: [auditEvent, ...state.feed],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.capDenied, hi: hi.tags.capDenied },
        },
      ],
      whatsappMessages: [...state.whatsappMessages, waCapBlock],
    };
    return { nextState, betaReply, replyLang, auditEvent };
  }

  // --- DECISION: NEEDS_APPROVAL ---
  if (policy.decision === 'NEEDS_APPROVAL') {
    const idempotencyKey = buildIdempotencyKey(tool, intercepted.sanitizedArgs);

    if (state.paidIdempotencyKeys.includes(idempotencyKey)) {
      const betaReply = interpolate(replyDict.betaReplies.duplicatePayment, {
        payee: payeeStr || 'Pharmacy',
        amount: amountPaise ? formatINR(amountPaise) : '',
      });
      const dupAudit: AuditEvent = {
        ...auditEvent,
        idempotencyHit: true,
        reason: { en: en.policyReasons.idempotencyHit, hi: hi.policyReasons.idempotencyHit },
      };
      const nextState: HouseholdState = {
        ...state,
        feed: [dupAudit, ...state.feed],
        transcript: [
          ...baseTranscript,
          {
            id: nextId('msg_b'),
            sender: 'beta',
            text: betaReply,
            lang: replyLang,
            timestampMs: nowMs,
            intentTag: { en: en.tags.idempotent, hi: hi.tags.idempotent },
          },
        ],
      };
      return { nextState, betaReply, replyLang, auditEvent: dupAudit };
    }

    const approvalId = nextId('apr');
    const items = Array.isArray(intercepted.sanitizedArgs.items)
      ? (intercepted.sanitizedArgs.items as string[])
      : state.meds.map((m) => m.name);

    const approvalSummary: BilingualText =
      tool === 'reorder_medicine'
        ? {
            en: interpolate(en.events.medicineRefillSummary, { items: items.join(' + ') }),
            hi: interpolate(hi.events.medicineRefillSummary, { items: items.join(' + ') }),
          }
        : {
            en: interpolate(en.events.paymentSummary, { payee: payeeStr || 'Payee' }),
            hi: interpolate(hi.events.paymentSummary, { payee: payeeStr || 'भुगतानकर्ता' }),
          };

    const approvalReq: ApprovalRequest = {
      id: approvalId,
      createdAtMs: nowMs,
      expiresAtMs: nowMs + state.limits.approvalExpiryHours * 60 * 60 * 1000,
      tool,
      payee: payeeStr || 'Pharmacy',
      amountPaise: amountPaise ?? 0,
      summary: approvalSummary,
      items,
      reason: policy.reasonBilingual,
      status: 'pending',
      coolingOffRemainingSec: state.limits.coolingOffDemoSec,
      coolingOffTotalSec: state.limits.coolingOffDemoSec,
      idempotencyKey,
      originalMessage: rawMessage,
      parentLang: replyLang,
    };

    const amountFmt = formatINR(approvalReq.amountPaise);
    const betaReply =
      tool === 'reorder_medicine'
        ? interpolate(replyDict.betaReplies.medsNeedsApproval, { amount: amountFmt })
        : interpolate(replyDict.betaReplies.billNeedsApproval, {
            payee: approvalReq.payee,
            amount: amountFmt,
          });

    const waApproval: WhatsAppMessage = {
      id: nextId('wa_apr'),
      sender: 'beta',
      timestampMs: nowMs,
      kind: 'approval',
      title: {
        en: interpolate(en.waMessages.approvalTitle, { amount: amountFmt }),
        hi: interpolate(hi.waMessages.approvalTitle, { amount: amountFmt }),
      },
      body: {
        en: interpolate(en.waMessages.approvalBody, {
          summary: approvalSummary.en,
          payee: approvalReq.payee,
          sec: state.limits.coolingOffDemoSec,
        }),
        hi: interpolate(hi.waMessages.approvalBody, {
          summary: approvalSummary.hi,
          payee: approvalReq.payee,
          sec: state.limits.coolingOffDemoSec,
        }),
      },
      approvalId,
      amountPaise: approvalReq.amountPaise,
    };

    const nextState: HouseholdState = {
      ...state,
      approvals: {
        ...state.approvals,
        [approvalId]: approvalReq,
      },
      feed: [auditEvent, ...state.feed],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.needsOk, hi: hi.tags.needsOk },
        },
      ],
      whatsappMessages: [...state.whatsappMessages, waApproval],
    };

    return { nextState, betaReply, replyLang, auditEvent, createdApproval: approvalReq };
  }

  // --- DECISION: ALLOW — Execute the mock MCP tool ---
  if (tool === 'get_due_bills') {
    const requestedBiller =
      typeof intercepted.sanitizedArgs.biller === 'string'
        ? intercepted.sanitizedArgs.biller
        : undefined;
    const dueBills = state.bills.filter((b) => b.status === 'due');
    const targetBill = requestedBiller
      ? dueBills.find((b) => b.biller.toLowerCase() === requestedBiller.toLowerCase())
      : dueBills[0];

    let betaReply: string;
    let nextMentioned = state.lastMentionedBiller;

    if (targetBill) {
      nextMentioned = targetBill.biller;
      betaReply = interpolate(replyDict.betaReplies.dueBillFound, {
        biller: targetBill.biller,
        category: replyDict.categories[targetBill.category],
        amount: formatINR(targetBill.amountPaise),
        dueDate: formatLocalizedDate(targetBill.dueDate, replyLang),
      });
    } else if (dueBills.length === 0) {
      betaReply = replyDict.betaReplies.noBillsDue;
    } else {
      const summaryList = dueBills
        .map(
          (b) =>
            `${b.biller} ${formatINR(b.amountPaise)} (${formatLocalizedDate(b.dueDate, replyLang)})`
        )
        .join(', ');
      nextMentioned = dueBills[0].biller;
      betaReply = interpolate(replyDict.betaReplies.multipleBillsDue, { list: summaryList });
    }

    const nextState: HouseholdState = {
      ...state,
      lastMentionedBiller: nextMentioned,
      feed: [auditEvent, ...state.feed],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.readAllowed, hi: hi.tags.readAllowed },
        },
      ],
    };

    return { nextState, betaReply, replyLang, auditEvent };
  }

  if (tool === 'find_slots') {
    const localizedSlots = state.doctor.availableSlots.map((s) =>
      formatLocalizedSlot(s, replyLang)
    );
    const betaReply = interpolate(replyDict.betaReplies.slotsAvailable, {
      doctor: replyLang === 'hi' ? state.doctor.nameHi : state.doctor.name,
      clinic: replyLang === 'hi' ? state.doctor.clinicHi : state.doctor.clinic,
      slots: localizedSlots.join(', '),
      firstSlot: localizedSlots[0],
    });
    const nextState: HouseholdState = {
      ...state,
      feed: [auditEvent, ...state.feed],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.readAllowed, hi: hi.tags.readAllowed },
        },
      ],
    };
    return { nextState, betaReply, replyLang, auditEvent };
  }

  if (tool === 'book_appointment') {
    const slotRaw = String(intercepted.sanitizedArgs.slot ?? state.doctor.availableSlots[0]);
    const slotEn = formatLocalizedSlot(slotRaw, 'en');
    const slotHi = formatLocalizedSlot(slotRaw, 'hi');
    const timeEn = formatLocalizedISTTime(nowDate, 'en');
    const timeHi = formatLocalizedISTTime(nowDate, 'hi');

    const calendarCard: CalendarCardData = {
      title: {
        en: interpolate(en.receipts.calendarEventTitle, { doctor: state.doctor.name }),
        hi: interpolate(hi.receipts.calendarEventTitle, { doctor: state.doctor.nameHi }),
      },
      dateTime: {
        en: `${slotEn} IST`,
        hi: `${slotHi} IST`,
      },
      location: {
        en: state.doctor.clinic,
        hi: state.doctor.clinicHi,
      },
      attendees: {
        en: 'Kamala Deshpande, Ravi Deshpande',
        hi: 'कमला देशपांडे, रवि देशपांडे',
      },
    };

    const newAppt = {
      id: nextId('appt'),
      doctor: state.doctor.name,
      specialty: state.doctor.specialty,
      clinic: state.doctor.clinic,
      slot: slotRaw,
      calendarAdded: true,
      bookedAtMs: nowMs,
    };

    const englishSummary = interpolate(en.receipts.apptSummary, {
      doctor: state.doctor.name,
      clinic: state.doctor.clinic,
      slot: slotEn,
    });
    const hindiSummary = interpolate(hi.receipts.apptSummary, {
      doctor: state.doctor.nameHi,
      clinic: state.doctor.clinicHi,
      slot: slotHi,
    });

    const receipt: Receipt = {
      id: nextId('rcpt'),
      timestampMs: nowMs,
      title: {
        en: interpolate(en.receipts.apptTitle, { doctor: state.doctor.name }),
        hi: interpolate(hi.receipts.apptTitle, { doctor: state.doctor.nameHi }),
      },
      category: 'appointment',
      englishSummary,
      hindiSummary,
      deliveredTo: { parent: true, guardian: true },
      calendarCard,
      auditTrail: {
        tool: `book_appointment(doctor="${state.doctor.name}", slot="${slotRaw}")`,
        factsSummary: summarizeFacts(intercepted.facts),
        decision: policy.decision,
        ruleId: policy.ruleId,
        executed: {
          en: interpolate(en.receipts.apptExecuted, { time: timeEn }),
          hi: interpolate(hi.receipts.apptExecuted, { time: timeHi }),
        },
      },
    };

    const waCalendar: WhatsAppMessage = {
      id: nextId('wa_cal'),
      sender: 'beta',
      timestampMs: nowMs,
      kind: 'calendar',
      title: { en: en.waMessages.calendarTitle, hi: hi.waMessages.calendarTitle },
      body: { en: englishSummary, hi: hindiSummary },
      secondaryBody: { en: hindiSummary, hi: englishSummary },
      calendarCard,
    };

    const betaReply = interpolate(replyDict.betaReplies.appointmentBooked, {
      doctor: replyLang === 'hi' ? state.doctor.nameHi : state.doctor.name,
      slot: replyLang === 'hi' ? slotHi : slotEn,
    });

    const nextState: HouseholdState = {
      ...state,
      appointments: [newAppt, ...state.appointments],
      feed: [auditEvent, ...state.feed],
      receipts: [receipt, ...state.receipts],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.lowBooked, hi: hi.tags.lowBooked },
          calendarCard,
        },
      ],
      whatsappMessages: [...state.whatsappMessages, waCalendar],
    };

    return { nextState, betaReply, replyLang, auditEvent };
  }

  if (tool === 'set_reminder') {
    const reminderText = String(intercepted.sanitizedArgs.reminderText ?? 'Metformin 500mg');
    const reminderTime = String(intercepted.sanitizedArgs.reminderTime ?? '8:00 PM');
    const timeEn = formatLocalizedISTTime(nowDate, 'en');
    const timeHi = formatLocalizedISTTime(nowDate, 'hi');

    const englishSummary = interpolate(en.receipts.reminderSummary, {
      time: reminderTime,
      text: reminderText,
    });
    const hindiSummary = interpolate(hi.receipts.reminderSummary, {
      time: reminderTime,
      text: reminderText,
    });

    const receipt: Receipt = {
      id: nextId('rcpt'),
      timestampMs: nowMs,
      title: {
        en: interpolate(en.receipts.reminderTitle, { time: reminderTime }),
        hi: interpolate(hi.receipts.reminderTitle, { time: reminderTime }),
      },
      category: 'reminder',
      englishSummary,
      hindiSummary,
      deliveredTo: { parent: true, guardian: true },
      auditTrail: {
        tool: `set_reminder(time="${reminderTime}")`,
        factsSummary: summarizeFacts(intercepted.facts),
        decision: policy.decision,
        ruleId: policy.ruleId,
        executed: {
          en: interpolate(en.receipts.reminderExecuted, { time: timeEn }),
          hi: interpolate(hi.receipts.reminderExecuted, { time: timeHi }),
        },
      },
    };

    const betaReply = interpolate(replyDict.betaReplies.reminderSet, { time: reminderTime });

    const nextState: HouseholdState = {
      ...state,
      reminders: [
        { id: nextId('rem'), text: reminderText, time: reminderTime, createdAtMs: nowMs },
        ...state.reminders,
      ],
      feed: [auditEvent, ...state.feed],
      receipts: [receipt, ...state.receipts],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.lowSet, hi: hi.tags.lowSet },
        },
      ],
      whatsappMessages: [
        ...state.whatsappMessages,
        {
          id: nextId('wa_rem'),
          sender: 'beta',
          timestampMs: nowMs,
          kind: 'receipt',
          title: {
            en: interpolate(en.waMessages.reminderTitle, { time: reminderTime }),
            hi: interpolate(hi.waMessages.reminderTitle, { time: reminderTime }),
          },
          body: { en: englishSummary, hi: hindiSummary },
          secondaryBody: { en: hindiSummary, hi: englishSummary },
        },
      ],
    };

    return { nextState, betaReply, replyLang, auditEvent };
  }

  // Money tools: pay_bill & reorder_medicine (ALLOW reached)
  const idempotencyKey = buildIdempotencyKey(tool, intercepted.sanitizedArgs);
  if (state.paidIdempotencyKeys.includes(idempotencyKey)) {
    const betaReply = interpolate(replyDict.betaReplies.duplicatePayment, {
      payee: payeeStr || 'BESCOM',
      amount: amountPaise ? formatINR(amountPaise) : '',
    });
    const idempotentEvent: AuditEvent = {
      ...auditEvent,
      idempotencyHit: true,
      reason: { en: en.policyReasons.idempotencyHit, hi: hi.policyReasons.idempotencyHit },
    };
    const nextState: HouseholdState = {
      ...state,
      feed: [idempotentEvent, ...state.feed],
      transcript: [
        ...baseTranscript,
        {
          id: nextId('msg_b'),
          sender: 'beta',
          text: betaReply,
          lang: replyLang,
          timestampMs: nowMs,
          intentTag: { en: en.tags.idempotent, hi: hi.tags.idempotent },
        },
      ],
    };
    return { nextState, betaReply, replyLang, auditEvent: idempotentEvent };
  }

  const paidAmount = amountPaise ?? 0;
  const paidFmt = formatINR(paidAmount);
  const targetPayee = payeeStr || (tool === 'reorder_medicine' ? 'Pharmacy' : 'BESCOM');
  const timeEn = formatLocalizedISTTime(nowDate, 'en');
  const timeHi = formatLocalizedISTTime(nowDate, 'hi');

  const updatedBills =
    tool === 'pay_bill'
      ? state.bills.map((b) =>
          b.biller.toLowerCase() === targetPayee.toLowerCase()
            ? { ...b, status: 'paid' as const, paidAtMs: nowMs }
            : b
        )
      : state.bills;

  const isMedicine = tool === 'reorder_medicine';
  const medItems = isMedicine
    ? ((intercepted.sanitizedArgs.items as string[]) ?? state.meds.map((m) => m.name)).join(' + ')
    : '';

  const englishSummary = isMedicine
    ? interpolate(en.receipts.medSummary, { amount: paidFmt, payee: targetPayee, items: medItems })
    : interpolate(en.receipts.billSummary, { payee: targetPayee, amount: paidFmt });

  const hindiSummary = isMedicine
    ? interpolate(hi.receipts.medSummary, { amount: paidFmt, payee: targetPayee, items: medItems })
    : interpolate(hi.receipts.billSummary, { payee: targetPayee, amount: paidFmt });

  const receipt: Receipt = {
    id: nextId('rcpt'),
    timestampMs: nowMs,
    title: isMedicine
      ? {
          en: interpolate(en.receipts.medTitle, { amount: paidFmt }),
          hi: interpolate(hi.receipts.medTitle, { amount: paidFmt }),
        }
      : {
          en: interpolate(en.receipts.billTitle, { payee: targetPayee, amount: paidFmt }),
          hi: interpolate(hi.receipts.billTitle, { payee: targetPayee, amount: paidFmt }),
        },
    category: isMedicine ? 'medicine' : 'payment',
    amountPaise: paidAmount,
    englishSummary,
    hindiSummary,
    deliveredTo: { parent: true, guardian: true },
    auditTrail: {
      tool: `${tool}(payee="${targetPayee}", amount=${paidFmt})`,
      factsSummary: summarizeFacts(intercepted.facts),
      decision: policy.decision,
      ruleId: policy.ruleId,
      executed: {
        en: interpolate(en.receipts.paymentExecuted, { amount: paidFmt, time: timeEn }),
        hi: interpolate(hi.receipts.paymentExecuted, { amount: paidFmt, time: timeHi }),
      },
      idempotencyKey,
    },
  };

  const waReceipt: WhatsAppMessage = {
    id: nextId('wa_rcpt'),
    sender: 'beta',
    timestampMs: nowMs,
    kind: 'receipt',
    title: isMedicine
      ? {
          en: interpolate(en.waMessages.medReceiptTitle, { amount: paidFmt }),
          hi: interpolate(hi.waMessages.medReceiptTitle, { amount: paidFmt }),
        }
      : {
          en: interpolate(en.waMessages.billReceiptTitle, { payee: targetPayee, amount: paidFmt }),
          hi: interpolate(hi.waMessages.billReceiptTitle, { payee: targetPayee, amount: paidFmt }),
        },
    body: { en: englishSummary, hi: hindiSummary },
    secondaryBody: { en: hindiSummary, hi: englishSummary },
    amountPaise: paidAmount,
  };

  const betaReply = isMedicine
    ? interpolate(replyDict.betaReplies.medicineExecuted, { amount: paidFmt, items: medItems })
    : interpolate(replyDict.betaReplies.billPaid, { biller: targetPayee, amount: paidFmt });

  const nextState: HouseholdState = {
    ...state,
    bills: updatedBills,
    paidIdempotencyKeys: [...state.paidIdempotencyKeys, idempotencyKey],
    paymentsTimestamps: [...state.paymentsTimestamps, nowMs],
    feed: [auditEvent, ...state.feed],
    receipts: [receipt, ...state.receipts],
    transcript: [
      ...baseTranscript,
      {
        id: nextId('msg_b'),
        sender: 'beta',
        text: betaReply,
        lang: replyLang,
        timestampMs: nowMs,
        intentTag: isMedicine
          ? { en: en.tags.approvedExecuted, hi: hi.tags.approvedExecuted }
          : { en: en.tags.smallPaid, hi: hi.tags.smallPaid },
      },
    ],
    whatsappMessages: [...state.whatsappMessages, waReceipt],
  };

  return { nextState, betaReply, replyLang, auditEvent };
}

export function approveGuardianRequest(
  state: HouseholdState,
  approvalId: string,
  via: 'console' | 'whatsapp',
  nowMs: number = Date.now()
): HouseholdState {
  const existing = state.approvals[approvalId];
  if (!existing || existing.status !== 'pending') return state;

  const updated: ApprovalRequest = {
    ...existing,
    status: 'cooling_off',
    approvedAtMs: nowMs,
    approvedVia: via,
    coolingOffRemainingSec: state.limits.coolingOffDemoSec,
  };

  const amountFmt = formatINR(existing.amountPaise);
  const waConfirm: WhatsAppMessage = {
    id: nextId('wa_ravi_ok'),
    sender: 'ravi',
    timestampMs: nowMs,
    kind: 'info',
    body: {
      en: interpolate(en.waMessages.raviApproved, {
        amount: amountFmt,
        payee: existing.payee,
        via: via === 'whatsapp' ? en.console.viaWhatsapp : en.console.viaConsole,
        sec: state.limits.coolingOffDemoSec,
      }),
      hi: interpolate(hi.waMessages.raviApproved, {
        amount: amountFmt,
        payee: existing.payee,
        via: via === 'whatsapp' ? hi.console.viaWhatsapp : hi.console.viaConsole,
        sec: state.limits.coolingOffDemoSec,
      }),
    },
  };

  return {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: updated,
    },
    whatsappMessages: [...state.whatsappMessages, waConfirm],
  };
}

export function tickCoolingOff(state: HouseholdState, approvalId: string): HouseholdState {
  const existing = state.approvals[approvalId];
  if (!existing || existing.status !== 'cooling_off') return state;
  const nextSec = Math.max(0, existing.coolingOffRemainingSec - 1);
  return {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        ...existing,
        coolingOffRemainingSec: nextSec,
      },
    },
  };
}

export function completeCoolingOffAndExecute(
  state: HouseholdState,
  approvalId: string,
  nowMs: number = Date.now()
): ExecutionOutcome {
  const existing = state.approvals[approvalId];
  if (!existing || existing.status !== 'cooling_off') {
    return { nextState: state, betaReply: '', replyLang: 'hi' };
  }

  const readyState: HouseholdState = {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        ...existing,
        coolingOffRemainingSec: 0,
      },
    },
  };

  const parsed: ParsedIntent = {
    tool: existing.tool,
    args: {
      payee: existing.payee,
      biller: existing.payee,
      amountPaise: existing.amountPaise,
      items: existing.items,
    },
    source: 'fallback',
  };

  const outcome = executeGuardedToolCall(readyState, parsed, existing.originalMessage, {
    approvalId,
    nowMs,
    skipParentMessageAppend: true,
    parentLang: existing.parentLang,
  });

  const finalStatus = outcome.auditEvent?.decision === 'ALLOW' ? 'executed' : 'cancelled';

  return {
    ...outcome,
    nextState: {
      ...outcome.nextState,
      approvals: {
        ...outcome.nextState.approvals,
        [approvalId]: {
          ...existing,
          status: finalStatus,
          coolingOffRemainingSec: 0,
        },
      },
    },
  };
}

export function cancelCoolingOff(
  state: HouseholdState,
  approvalId: string,
  nowMs: number = Date.now()
): ExecutionOutcome {
  const existing = state.approvals[approvalId];
  if (!existing || existing.status !== 'cooling_off') {
    return { nextState: state, betaReply: '', replyLang: 'hi' };
  }

  const replyLang = existing.parentLang;
  const replyDict = getDictionary(replyLang);
  const amountFmt = formatINR(existing.amountPaise);
  const betaReply = interpolate(replyDict.betaReplies.coolingCancelled, {
    payee: existing.payee,
    amount: amountFmt,
  });

  const cancelEvent: AuditEvent = {
    id: nextId('evt'),
    timestampMs: nowMs,
    tool: existing.tool,
    summary: {
      en: interpolate(en.events.cancelledCooling, { payee: existing.payee, amount: amountFmt }),
      hi: interpolate(hi.events.cancelledCooling, { payee: existing.payee, amount: amountFmt }),
    },
    amountPaise: existing.amountPaise,
    payee: existing.payee,
    riskLevel: 'R4',
    decision: 'DENY',
    reason: {
      en: en.policyReasons.cancelledCoolingOff,
      hi: hi.policyReasons.cancelledCoolingOff,
    },
    ruleId: 'forbid::guardian_cancelled_cooling_off',
    facts: {
      payee_known: state.savedPayees.some((p) => p.toLowerCase() === existing.payee.toLowerCase()),
      hour_ist: getISTHour(new Date(nowMs)),
      payments_last_hour: state.paymentsTimestamps.filter((ts) => ts >= nowMs - 3600000).length,
      approval_valid: false,
      scam_flag: false,
      frozen: state.frozen,
    },
    sanitizedArgs: { payee: existing.payee, amountPaise: existing.amountPaise },
    strippedUntrustedFields: [],
  };

  const nextState: HouseholdState = {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        ...existing,
        status: 'cancelled',
      },
    },
    feed: [cancelEvent, ...state.feed],
    transcript: [
      ...state.transcript,
      {
        id: nextId('msg_b'),
        sender: 'beta',
        text: betaReply,
        lang: replyLang,
        timestampMs: nowMs,
        intentTag: { en: en.tags.cancelled, hi: hi.tags.cancelled },
      },
    ],
    whatsappMessages: [
      ...state.whatsappMessages,
      {
        id: nextId('wa_cancel'),
        sender: 'ravi',
        timestampMs: nowMs,
        kind: 'info',
        body: {
          en: interpolate(en.waMessages.raviCancelled, {
            amount: amountFmt,
            payee: existing.payee,
          }),
          hi: interpolate(hi.waMessages.raviCancelled, {
            amount: amountFmt,
            payee: existing.payee,
          }),
        },
      },
    ],
  };

  return { nextState, betaReply, replyLang, auditEvent: cancelEvent };
}

export function declineGuardianRequest(
  state: HouseholdState,
  approvalId: string,
  via: 'console' | 'whatsapp',
  nowMs: number = Date.now()
): ExecutionOutcome {
  const existing = state.approvals[approvalId];
  if (!existing || existing.status !== 'pending') {
    return { nextState: state, betaReply: '', replyLang: 'hi' };
  }

  const replyLang = existing.parentLang;
  const replyDict = getDictionary(replyLang);
  const amountFmt = formatINR(existing.amountPaise);
  const betaReply = interpolate(replyDict.betaReplies.requestDeclined, {
    payee: existing.payee,
    amount: amountFmt,
  });

  const declineEvent: AuditEvent = {
    id: nextId('evt'),
    timestampMs: nowMs,
    tool: existing.tool,
    summary: {
      en: interpolate(en.events.declinedGuardian, { payee: existing.payee, amount: amountFmt }),
      hi: interpolate(hi.events.declinedGuardian, { payee: existing.payee, amount: amountFmt }),
    },
    amountPaise: existing.amountPaise,
    payee: existing.payee,
    riskLevel: 'R4',
    decision: 'DENY',
    reason: {
      en: interpolate(en.policyReasons.declinedByGuardian, {
        via: via === 'whatsapp' ? en.console.viaWhatsapp : en.console.viaConsole,
      }),
      hi: interpolate(hi.policyReasons.declinedByGuardian, {
        via: via === 'whatsapp' ? hi.console.viaWhatsapp : hi.console.viaConsole,
      }),
    },
    ruleId: 'forbid::guardian_declined',
    facts: {
      payee_known: state.savedPayees.some((p) => p.toLowerCase() === existing.payee.toLowerCase()),
      hour_ist: getISTHour(new Date(nowMs)),
      payments_last_hour: state.paymentsTimestamps.filter((ts) => ts >= nowMs - 3600000).length,
      approval_valid: false,
      scam_flag: false,
      frozen: state.frozen,
    },
    sanitizedArgs: { payee: existing.payee, amountPaise: existing.amountPaise },
    strippedUntrustedFields: [],
  };

  const nextState: HouseholdState = {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        ...existing,
        status: 'declined',
      },
    },
    feed: [declineEvent, ...state.feed],
    transcript: [
      ...state.transcript,
      {
        id: nextId('msg_b'),
        sender: 'beta',
        text: betaReply,
        lang: replyLang,
        timestampMs: nowMs,
        intentTag: { en: en.tags.declined, hi: hi.tags.declined },
      },
    ],
    whatsappMessages: [
      ...state.whatsappMessages,
      {
        id: nextId('wa_dec'),
        sender: 'ravi',
        timestampMs: nowMs,
        kind: 'info',
        body: {
          en: interpolate(en.waMessages.raviDeclined, {
            amount: amountFmt,
            payee: existing.payee,
          }),
          hi: interpolate(hi.waMessages.raviDeclined, {
            amount: amountFmt,
            payee: existing.payee,
          }),
        },
      },
    ],
  };

  return { nextState, betaReply, replyLang, auditEvent: declineEvent };
}

export function toggleHouseholdFreeze(
  state: HouseholdState,
  freeze: boolean,
  nowMs: number = Date.now()
): HouseholdState {
  if (state.frozen === freeze) return state;

  const waNotice: WhatsAppMessage = {
    id: nextId('wa_frz'),
    sender: 'beta',
    timestampMs: nowMs,
    kind: 'freeze_notice',
    title: freeze
      ? { en: en.waMessages.frozenOnTitle, hi: hi.waMessages.frozenOnTitle }
      : { en: en.waMessages.frozenOffTitle, hi: hi.waMessages.frozenOffTitle },
    body: freeze
      ? { en: en.waMessages.frozenOnBody, hi: hi.waMessages.frozenOnBody }
      : { en: en.waMessages.frozenOffBody, hi: hi.waMessages.frozenOffBody },
  };

  return {
    ...state,
    frozen: freeze,
    whatsappMessages: [...state.whatsappMessages, waNotice],
  };
}
