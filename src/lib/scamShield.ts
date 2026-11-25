import { en, hi, interpolate, type BilingualText } from '../i18n';

export type ScamPatternKey =
  | 'cbi'
  | 'police'
  | 'digitalArrest'
  | 'parcel'
  | 'secrecy'
  | 'urgentTransfer'
  | 'otp';

export interface ScamScanResult {
  scamFlag: boolean;
  isOtpOrPinAttempt: boolean;
  matchedPatternKeys: ScamPatternKey[];
  matchedPatterns: string[];
  severity: 'none' | 'otp' | 'scam';
  betaReply: string;
  betaReplyBilingual: BilingualText;
  alertTitle: BilingualText;
  alertDetails: BilingualText;
}

interface PatternRule {
  key: ScamPatternKey;
  regex: RegExp;
  category: 'scam' | 'otp';
}

const SCAM_RULES: PatternRule[] = [
  {
    key: 'cbi',
    regex: /(\b(cbi|ed|ncb|enforcement directorate|cyber cell)\b|सीबीआई|साइबर\s*सेल)/i,
    category: 'scam',
  },
  {
    key: 'police',
    regex: /(\b(police|warrant|arrest|giraf?taar|thane|thana|court notice|supreme court)\b|पुलिस|वारंट|गिरफ़्तार|गिरफ्तार|थाने|थाना|सुप्रीम\s*कोर्ट)/i,
    category: 'scam',
  },
  {
    key: 'digitalArrest',
    regex: /(\b(digital\s*arrest|video\s*call\s*inquiry|skype\s*inquiry|house\s*arrest)\b|डिजिटल\s*अरेस्ट)/i,
    category: 'scam',
  },
  {
    key: 'parcel',
    regex: /(\b(parcel|customs|fedex|dhl|drugs|narcotics|illegal\s*package|contraband)\b|पार्सल|कस्टम्स|ड्रग्स|नशीले)/i,
    category: 'scam',
  },
  {
    key: 'secrecy',
    regex: /(don['’]?t\s+tell\s+anyone|do\s+not\s+tell|kisi\s+ko\s+(mat\s+)?batana(\s+mat)?|keep\s+it\s+secret|chup\s+rahna|ravi\s+ko\s+mat\s+batana|किसी\s+को\s+(मत\s+)?बताना(\s+मत)?|रवि\s+को\s+मत\s+बता)/i,
    category: 'scam',
  },
  {
    key: 'urgentTransfer',
    regex: /(transfer\s+urgently|urgent\s+transfer|abhi\s+transfer|turant\s+(bhejo|transfer)|immediately\s+transfer|safe\s+account|rbi\s+verification\s+account|तुरंत\s*(ट्रांसफ़र|ट्रांसफर|भेज)|अभी\s*(ट्रांसफ़र|ट्रांसफर))/i,
    category: 'scam',
  },
  {
    key: 'otp',
    regex: /(\b(otp|pin|cvv|mpin|upi\s*pin|atm\s*pin|password)\b|ओटीपी|पिन)/i,
    category: 'otp',
  },
];

export const DIGITAL_ARREST_BETA_REPLY_EN = en.betaReplies.digitalArrest;
export const DIGITAL_ARREST_BETA_REPLY_HI = hi.betaReplies.digitalArrest;
export const DIGITAL_ARREST_BETA_REPLY = DIGITAL_ARREST_BETA_REPLY_HI;

export function scanForScam(message: string): ScamScanResult {
  const text = (message || '').trim();
  if (!text) {
    return {
      scamFlag: false,
      isOtpOrPinAttempt: false,
      matchedPatternKeys: [],
      matchedPatterns: [],
      severity: 'none',
      betaReply: '',
      betaReplyBilingual: { en: '', hi: '' },
      alertTitle: { en: '', hi: '' },
      alertDetails: { en: '', hi: '' },
    };
  }

  const matchedScamKeys: ScamPatternKey[] = [];
  const matchedOtpKeys: ScamPatternKey[] = [];

  for (const rule of SCAM_RULES) {
    if (rule.regex.test(text)) {
      if (rule.category === 'otp') {
        matchedOtpKeys.push(rule.key);
      } else {
        matchedScamKeys.push(rule.key);
      }
    }
  }

  const hasScam = matchedScamKeys.length > 0;
  const hasOtp = matchedOtpKeys.length > 0;

  if (hasScam) {
    const allKeys = [...matchedScamKeys, ...matchedOtpKeys];
    const patternsEn = allKeys.map((k) => en.scamPatterns[k]).join(', ');
    const patternsHi = allKeys.map((k) => hi.scamPatterns[k]).join(', ');

    return {
      scamFlag: true,
      isOtpOrPinAttempt: hasOtp,
      matchedPatternKeys: allKeys,
      matchedPatterns: allKeys.map((k) => en.scamPatterns[k]),
      severity: 'scam',
      betaReply: hi.betaReplies.digitalArrest,
      betaReplyBilingual: {
        en: en.betaReplies.digitalArrest,
        hi: hi.betaReplies.digitalArrest,
      },
      alertTitle: {
        en: en.waMessages.scamTitle,
        hi: hi.waMessages.scamTitle,
      },
      alertDetails: {
        en: interpolate(en.waMessages.scamBody, { patterns: patternsEn }),
        hi: interpolate(hi.waMessages.scamBody, { patterns: patternsHi }),
      },
    };
  }

  if (hasOtp) {
    return {
      scamFlag: true,
      isOtpOrPinAttempt: true,
      matchedPatternKeys: matchedOtpKeys,
      matchedPatterns: matchedOtpKeys.map((k) => en.scamPatterns[k]),
      severity: 'otp',
      betaReply: hi.betaReplies.otpRefusal,
      betaReplyBilingual: {
        en: en.betaReplies.otpRefusal,
        hi: hi.betaReplies.otpRefusal,
      },
      alertTitle: {
        en: en.waMessages.otpTitle,
        hi: hi.waMessages.otpTitle,
      },
      alertDetails: {
        en: en.waMessages.otpBody,
        hi: hi.waMessages.otpBody,
      },
    };
  }

  return {
    scamFlag: false,
    isOtpOrPinAttempt: false,
    matchedPatternKeys: [],
    matchedPatterns: [],
    severity: 'none',
    betaReply: '',
    betaReplyBilingual: { en: '', hi: '' },
    alertTitle: { en: '', hi: '' },
    alertDetails: { en: '', hi: '' },
  };
}
