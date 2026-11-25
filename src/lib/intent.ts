import { scanForScam } from './scamShield';
import type { HouseholdState, ToolName } from './store';

export interface ParsedIntent {
  tool: ToolName | 'general_help';
  args: Record<string, unknown>;
  source: 'gemini' | 'fallback';
}

function normalizeDevanagariDigits(text: string): string {
  return text.replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x0966));
}

function extractRupeesToPaise(rawMessage: string): number | undefined {
  const message = normalizeDevanagariDigits(rawMessage);
  const lower = message.toLowerCase();

  // Handle "2 lakh", "₹2 lakh", "2 लाख"
  const lakhMatch = lower.match(/(?:₹|rs\.?|rupees?)?\s*(\d+(?:\.\d+)?)\s*(?:lakh|लाख)/);
  if (lakhMatch) {
    return Math.round(parseFloat(lakhMatch[1]) * 100000 * 100);
  }

  // Handle "50 thousand", "20k", "5 हज़ार"
  const thousandMatch = lower.match(
    /(?:₹|rs\.?|rupees?)?\s*(\d+(?:\.\d+)?)\s*(?:thousand|hazaar|hazar|k\b|हज़ार|हजार)/
  );
  if (thousandMatch) {
    return Math.round(parseFloat(thousandMatch[1]) * 1000 * 100);
  }

  // Handle explicit currency amounts like ₹500, ₹1,240, 500 rupaye, 500 रुपये
  const rupeeMatch =
    message.match(/(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)/i) ||
    message.match(/([\d,]+(?:\.\d{1,2})?)\s*(?:rupaye|rupees|rs\b|रुपये|रुपए)/i);
  if (rupeeMatch) {
    const cleaned = rupeeMatch[1].replace(/,/g, '');
    const val = parseFloat(cleaned);
    if (Number.isFinite(val) && val > 0) {
      return Math.round(val * 100);
    }
  }

  return undefined;
}

function detectBiller(message: string, lastMentionedBiller?: 'BESCOM' | 'BWSSB' | 'Airtel') {
  const lower = message.toLowerCase();
  if (/(\b(bescom|bijli|electricity|light|power)\b|बिजली|बेसकॉम)/.test(lower)) {
    return 'BESCOM' as const;
  }
  if (/(\b(bwssb|pani|paani|water)\b|पानी)/.test(lower)) {
    return 'BWSSB' as const;
  }
  if (/(\b(airtel|phone|mobile|wifi|broadband|recharge)\b|एयरटेल|मोबाइल|रीचार्ज)/.test(lower)) {
    return 'Airtel' as const;
  }
  return lastMentionedBiller;
}

/**
 * Deterministic keyword fallback supporting English, Hinglish, and Devanagari Hindi
 * so the pitch demo never breaks even offline or if Gemini is unavailable.
 */
export function parseIntentFallback(rawMessage: string, state: HouseholdState): ParsedIntent {
  const text = normalizeDevanagariDigits(rawMessage.trim());
  const lower = text.toLowerCase();

  // 1. Scam / OTP detection routes directly to pay_bill / assess_risk with scam_flag
  const scam = scanForScam(text);
  if (scam.scamFlag) {
    const amountPaise = extractRupeesToPaise(text) ?? 20000000;
    const isTransfer = /(transfer|bhej|pay|send|paise|lakh|₹|ट्रांसफ़र|ट्रांसफर|भेज|लाख|पैसे)/i.test(text);
    return {
      tool: isTransfer ? 'pay_bill' : 'assess_risk',
      args: {
        payee: 'Unknown Caller (CBI Scam)',
        amountPaise: isTransfer ? amountPaise : 0,
        reason: scam.matchedPatterns.join(', '),
      },
      source: 'fallback',
    };
  }

  // 2. Medicine reorder
  if (
    /(dawai|dawa|medicine|medicines|tablet|metformin|amlodipine|pharmacy|medplus|दवाई|दवा|गोली|फ़ार्मेसी|फार्मेसी)/i.test(
      lower
    )
  ) {
    if (/(remind|reminder|yaad|alarm|रिमाइंडर|याद)/i.test(lower)) {
      const timeMatch = text.match(/(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|बजे))/i);
      return {
        tool: 'set_reminder',
        args: {
          reminderText: 'Metformin 500mg',
          reminderTime: timeMatch ? timeMatch[1] : '8:00 PM',
        },
        source: 'fallback',
      };
    }

    const totalMedPaise = state.meds.reduce((sum, m) => sum + m.packPricePaise, 0);
    return {
      tool: 'reorder_medicine',
      args: {
        payee: 'Pharmacy',
        items: state.meds.map((m) => m.name),
        amountPaise: extractRupeesToPaise(text) ?? totalMedPaise,
      },
      source: 'fallback',
    };
  }

  // 3. Doctor appointment / slots
  if (
    /(doctor|dr\.?\s*kulkarni|appointment|clinic|checkup|check-up|slot|डॉक्टर|कुलकर्णी|अपॉइंटमेंट|क्लिनिक|जाँच)/i.test(
      lower
    )
  ) {
    if (
      /(slot|available|kab\s+free|timing|timings|check\s+slots|खाली\s+समय|कब\s+खाली)/i.test(lower) &&
      !/(book|bhi|fix|kar\s+do|karo|schedule|बुक|भी|कर\s+दो|लगा)/i.test(lower)
    ) {
      return {
        tool: 'find_slots',
        args: { doctor: state.doctor.name },
        source: 'fallback',
      };
    }
    return {
      tool: 'book_appointment',
      args: {
        doctor: state.doctor.name,
        slot: state.doctor.availableSlots[0] || '29 Nov 11:00 AM',
      },
      source: 'fallback',
    };
  }

  // 4. Reminder
  if (/(remind|reminder|yaad\s+dila|alarm|रिमाइंडर|याद\s+दिला)/i.test(lower)) {
    const timeMatch = text.match(/(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|बजे))/i);
    return {
      tool: 'set_reminder',
      args: {
        reminderText: text,
        reminderTime: timeMatch ? timeMatch[1] : '8:00 PM',
      },
      source: 'fallback',
    };
  }

  // 5. Pay bill (explicit pay intent OR confirmation "haan, bhar do" / "हाँ, भर दो" / "yes pay it")
  const biller = detectBiller(text, state.lastMentionedBiller);
  const explicitPay =
    /(bhar\s*do|bharo|pay\b|jama\s*kar|clear\s*bill|transfer|send\s*₹|bhej\s*do|भर\s*दो|भर\s*दीजिए|भुगतान\s*कर|जमा\s*कर)/i.test(
      lower
    ) ||
    (/^(haan|han|yes|ok|okay|theek\s*hai|ji\s*haan|हाँ|जी\s*हाँ|ठीक\s*है)\b/i.test(lower) &&
      Boolean(state.lastMentionedBiller));

  if (explicitPay) {
    const matchedBill = state.bills.find(
      (b) => b.biller.toLowerCase() === (biller || 'BESCOM').toLowerCase()
    );
    const extractedAmount = extractRupeesToPaise(text);
    const targetPayee = biller || matchedBill?.biller || 'BESCOM';
    const amountPaise = extractedAmount ?? matchedBill?.amountPaise ?? 124000;

    return {
      tool: 'pay_bill',
      args: {
        payee: targetPayee,
        biller: targetPayee,
        amountPaise,
      },
      source: 'fallback',
    };
  }

  // 6. Check due bills
  if (
    /(bill|bijli|electricity|bescom|bwssb|water|airtel|due|kitna\s*aaya|aaya\s*kya|बिल|बिजली|पानी|एयरटेल|आया\s*क्या|बाक़ी)/i.test(
      lower
    )
  ) {
    return {
      tool: 'get_due_bills',
      args: biller ? { biller } : {},
      source: 'fallback',
    };
  }

  return {
    tool: 'general_help',
    args: {},
    source: 'fallback',
  };
}

/**
 * Primary intent parser: tries server-side Gemini API (/api/intent) first,
 * and falls back cleanly to deterministic keyword matching if unavailable or slow.
 */
export async function parseParentIntent(
  message: string,
  state: HouseholdState
): Promise<ParsedIntent> {
  const scam = scanForScam(message);
  if (scam.scamFlag) {
    return parseIntentFallback(message, state);
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const res = await fetch('/api/intent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        lastMentionedBiller: state.lastMentionedBiller ?? '',
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!res.ok) {
      return parseIntentFallback(message, state);
    }

    const data = await res.json();
    const geminiIntent = data?.intent;
    if (!geminiIntent || typeof geminiIntent.tool !== 'string') {
      return parseIntentFallback(message, state);
    }

    const args: Record<string, unknown> = {};
    if (typeof geminiIntent.payee === 'string' && geminiIntent.payee.trim()) {
      args.payee = geminiIntent.payee.trim();
      args.biller = geminiIntent.payee.trim();
    }
    if (typeof geminiIntent.amountPaise === 'number' && geminiIntent.amountPaise > 0) {
      args.amountPaise = Math.round(geminiIntent.amountPaise);
    }
    if (typeof geminiIntent.doctor === 'string' && geminiIntent.doctor.trim()) {
      args.doctor = geminiIntent.doctor.trim();
    }
    if (typeof geminiIntent.slot === 'string' && geminiIntent.slot.trim()) {
      args.slot = geminiIntent.slot.trim();
    }
    if (Array.isArray(geminiIntent.items) && geminiIntent.items.length > 0) {
      args.items = geminiIntent.items;
    }
    if (typeof geminiIntent.reminderText === 'string' && geminiIntent.reminderText.trim()) {
      args.reminderText = geminiIntent.reminderText.trim();
    }
    if (typeof geminiIntent.reminderTime === 'string' && geminiIntent.reminderTime.trim()) {
      args.reminderTime = geminiIntent.reminderTime.trim();
    }

    if (geminiIntent.tool === 'pay_bill') {
      const targetBiller =
        (typeof args.payee === 'string' && args.payee) ||
        detectBiller(message, state.lastMentionedBiller) ||
        'BESCOM';
      const bill = state.bills.find((b) => b.biller.toLowerCase() === targetBiller.toLowerCase());
      args.payee = bill?.biller ?? targetBiller;
      args.biller = args.payee;
      if (typeof args.amountPaise !== 'number' || args.amountPaise <= 0) {
        args.amountPaise = bill?.amountPaise ?? 124000;
      }
    } else if (geminiIntent.tool === 'reorder_medicine') {
      args.payee = 'Pharmacy';
      args.items = Array.isArray(args.items) ? args.items : state.meds.map((m) => m.name);
      if (typeof args.amountPaise !== 'number' || args.amountPaise <= 0) {
        args.amountPaise = state.meds.reduce((sum, m) => sum + m.packPricePaise, 0);
      }
    } else if (geminiIntent.tool === 'book_appointment') {
      args.doctor = typeof args.doctor === 'string' ? args.doctor : state.doctor.name;
      args.slot = typeof args.slot === 'string' ? args.slot : state.doctor.availableSlots[0];
    }

    return {
      tool: geminiIntent.tool as ToolName | 'general_help',
      args,
      source: 'gemini',
    };
  } catch {
    return parseIntentFallback(message, state);
  }
}
