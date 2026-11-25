import { en, hi, type BilingualText, type Locale } from '../i18n';

export type RiskLevel = 'R0' | 'R1' | 'R2' | 'R3' | 'R4';
export type PolicyDecision = 'ALLOW' | 'DENY' | 'NEEDS_APPROVAL';

export type ToolName =
  | 'get_due_bills'
  | 'pay_bill'
  | 'find_slots'
  | 'book_appointment'
  | 'reorder_medicine'
  | 'set_reminder'
  | 'request_approval'
  | 'assess_risk';

export interface GuardianFacts {
  payee_known: boolean;
  hour_ist: number;
  payments_last_hour: number;
  approval_valid: boolean;
  scam_flag: boolean;
  frozen: boolean;
}

export interface HouseholdLimits {
  autoApproveLimitPaise: number; // ₹2,000 = 200000
  absoluteMaxPaise: number; // ₹25,000 = 2500000
  coolingOffDemoSec: number; // 20s in demo
  coolingOffDefaultMin: number; // 30 min production default
  approvalExpiryHours: number; // 2 h
}

export interface Bill {
  id: string;
  biller: 'BESCOM' | 'BWSSB' | 'Airtel';
  category: 'Electricity' | 'Water' | 'Mobile & Fiber';
  accountNo: string;
  amountPaise: number;
  dueDate: '28 Nov' | '02 Dec' | '30 Nov';
  status: 'due' | 'paid';
  paidAtMs?: number;
}

export interface Appointment {
  id: string;
  doctor: string;
  specialty: string;
  clinic: string;
  slot: string;
  calendarAdded: boolean;
  bookedAtMs: number;
}

export interface Reminder {
  id: string;
  text: string;
  time: string;
  createdAtMs: number;
}

export interface CalendarCardData {
  title: BilingualText;
  dateTime: BilingualText;
  location: BilingualText;
  attendees: BilingualText;
}

export interface AuditEvent {
  id: string;
  timestampMs: number;
  tool: ToolName;
  summary: BilingualText;
  amountPaise?: number;
  payee?: string;
  riskLevel: RiskLevel;
  decision: PolicyDecision;
  reason: BilingualText;
  ruleId: string;
  facts: GuardianFacts;
  sanitizedArgs: Record<string, unknown>;
  strippedUntrustedFields: string[];
  idempotencyHit?: boolean;
}

export interface Receipt {
  id: string;
  timestampMs: number;
  title: BilingualText;
  category: 'payment' | 'medicine' | 'appointment' | 'reminder';
  amountPaise?: number;
  englishSummary: string;
  hindiSummary: string;
  deliveredTo: {
    parent: boolean;
    guardian: boolean;
  };
  calendarCard?: CalendarCardData;
  auditTrail: {
    tool: string;
    factsSummary: string;
    decision: PolicyDecision;
    ruleId: string;
    executed: BilingualText;
    idempotencyKey?: string;
  };
}

export type ApprovalStatus =
  | 'pending'
  | 'cooling_off'
  | 'executed'
  | 'declined'
  | 'cancelled'
  | 'expired';

export interface ApprovalRequest {
  id: string;
  createdAtMs: number;
  expiresAtMs: number;
  tool: ToolName;
  payee: string;
  amountPaise: number;
  summary: BilingualText;
  items?: string[];
  reason: BilingualText;
  status: ApprovalStatus;
  coolingOffRemainingSec: number;
  coolingOffTotalSec: number;
  approvedAtMs?: number;
  approvedVia?: 'console' | 'whatsapp';
  idempotencyKey: string;
  originalMessage: string;
  parentLang: Locale;
}

export interface TranscriptMessage {
  id: string;
  sender: 'parent' | 'beta';
  text: string;
  lang: Locale;
  timestampMs: number;
  intentTag?: BilingualText;
  calendarCard?: CalendarCardData;
  isScamWarning?: boolean;
}

export interface WhatsAppMessage {
  id: string;
  sender: 'beta' | 'ravi';
  timestampMs: number;
  kind: 'info' | 'receipt' | 'approval' | 'scam_alert' | 'calendar' | 'freeze_notice';
  title?: BilingualText;
  body: BilingualText;
  secondaryBody?: BilingualText;
  approvalId?: string;
  amountPaise?: number;
  calendarCard?: CalendarCardData;
  helpline?: string;
}

export interface HouseholdState {
  parent: {
    name: string;
    shortName: string;
    age: number;
    city: string;
    avatar: string;
  };
  guardian: {
    name: string;
    relation: string;
    city: string;
    avatar: string;
  };
  frozen: boolean;
  limits: HouseholdLimits;
  savedPayees: string[];
  meds: {
    name: string;
    dosage: string;
    packPricePaise: number;
  }[];
  doctor: {
    name: string;
    nameHi: string;
    specialty: string;
    clinic: string;
    clinicHi: string;
    availableSlots: string[];
  };
  bills: Bill[];
  appointments: Appointment[];
  reminders: Reminder[];
  paidIdempotencyKeys: string[];
  paymentsTimestamps: number[];
  approvals: Record<string, ApprovalRequest>;
  feed: AuditEvent[];
  receipts: Receipt[];
  transcript: TranscriptMessage[];
  whatsappMessages: WhatsAppMessage[];
  lastMentionedBiller?: 'BESCOM' | 'BWSSB' | 'Airtel';
}

export function createInitialHouseholdState(
  now: Date = new Date(),
  initialParentLang: Locale = 'hi'
): HouseholdState {
  const nowMs = now.getTime();

  return {
    parent: {
      name: 'Kamala Deshpande',
      shortName: 'Kamala',
      age: 74,
      city: 'Dharwad',
      avatar: '/src/assets/images/avatar_kamala_1795422885778.jpg',
    },
    guardian: {
      name: 'Ravi Deshpande',
      relation: 'Son',
      city: 'Bengaluru',
      avatar: '/src/assets/images/avatar_ravi_1795422885746.jpg',
    },
    frozen: false,
    limits: {
      autoApproveLimitPaise: 200000, // ₹2,000
      absoluteMaxPaise: 2500000, // ₹25,000
      coolingOffDemoSec: 20, // 20 s demo
      coolingOffDefaultMin: 30, // 30 min default
      approvalExpiryHours: 2, // 2 h
    },
    savedPayees: ['BESCOM', 'BWSSB', 'Airtel', 'Pharmacy'],
    meds: [
      { name: 'Metformin 500mg', dosage: '60 tablets · Morning & Night', packPricePaise: 195000 },
      { name: 'Amlodipine 5mg', dosage: '60 tablets · After Breakfast', packPricePaise: 185000 },
    ],
    doctor: {
      name: 'Dr. Kulkarni',
      nameHi: 'डॉ. कुलकर्णी',
      specialty: 'Senior Physician & Heart Care',
      clinic: 'SDM Clinic, Dharwad',
      clinicHi: 'SDM क्लिनिक, धारवाड़',
      availableSlots: ['29 Nov 11:00 AM', '29 Nov 4:30 PM', '30 Nov 10:30 AM'],
    },
    bills: [
      {
        id: 'bill_bescom_nov',
        biller: 'BESCOM',
        category: 'Electricity',
        accountNo: 'BES-88412',
        amountPaise: 124000, // ₹1,240
        dueDate: '28 Nov',
        status: 'due',
      },
      {
        id: 'bill_bwssb_dec',
        biller: 'BWSSB',
        category: 'Water',
        accountNo: 'BWS-20194',
        amountPaise: 42000, // ₹420
        dueDate: '02 Dec',
        status: 'due',
      },
      {
        id: 'bill_airtel_nov',
        biller: 'Airtel',
        category: 'Mobile & Fiber',
        accountNo: 'AIR-98450',
        amountPaise: 50000, // ₹500
        dueDate: '30 Nov',
        status: 'due',
      },
    ],
    appointments: [],
    reminders: [],
    paidIdempotencyKeys: [],
    paymentsTimestamps: [],
    approvals: {},
    feed: [],
    receipts: [],
    transcript: [
      {
        id: 'msg_welcome',
        sender: 'beta',
        text: initialParentLang === 'hi' ? hi.parentPanel.welcomeMessage : en.parentPanel.welcomeMessage,
        lang: initialParentLang,
        timestampMs: nowMs,
      },
    ],
    whatsappMessages: [
      {
        id: 'wa_welcome',
        sender: 'beta',
        timestampMs: nowMs,
        kind: 'info',
        title: {
          en: en.whatsapp.welcomeTitle,
          hi: hi.whatsapp.welcomeTitle,
        },
        body: {
          en: en.whatsapp.welcomeBody,
          hi: hi.whatsapp.welcomeBody,
        },
      },
    ],
    lastMentionedBiller: undefined,
  };
}
