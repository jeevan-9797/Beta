import { formatINR, getISTHour } from './format';
import { scanForScam } from './scamShield';
import { en, hi, interpolate, type BilingualText } from '../i18n';
import type {
  GuardianFacts,
  HouseholdLimits,
  HouseholdState,
  PolicyDecision,
  RiskLevel,
  ToolName,
} from './store';

/**
 * Fields that the AI / tool caller is NEVER trusted to supply.
 * The Guardian Gate interceptor always discards these before policy evaluation.
 */
const UNTRUSTED_FACT_KEYS = new Set([
  'payee_known',
  'hour_ist',
  'payments_last_hour',
  'approval_valid',
  'scam_flag',
  'frozen',
  'risk_level',
  'decision',
  'bypass_policy',
  'auto_approve',
]);

export const READ_TOOLS: ReadonlySet<ToolName> = new Set([
  'get_due_bills',
  'find_slots',
  'assess_risk',
]);

export const LOW_RISK_TOOLS: ReadonlySet<ToolName> = new Set([
  'book_appointment',
  'set_reminder',
]);

export const MONEY_TOOLS: ReadonlySet<ToolName> = new Set([
  'pay_bill',
  'reorder_medicine',
]);

export interface InterceptContext {
  messageText: string;
  approvalId?: string;
  nowMs?: number;
}

export interface InterceptedRequest {
  tool: ToolName;
  sanitizedArgs: Record<string, unknown>;
  strippedUntrustedFields: string[];
  facts: GuardianFacts;
  scamDetails: ReturnType<typeof scanForScam>;
}

export interface PolicyEvaluation {
  decision: PolicyDecision;
  riskLevel: RiskLevel;
  riskLabel: string;
  reason: string;
  reasonBilingual: BilingualText;
  ruleId: string;
}

/**
 * Step 1 of the Guardian Gate:
 * Strips any untrusted fact fields from the AI tool payload and computes
 * the 6 authoritative facts strictly from the household store & Scam Shield.
 */
export function interceptToolCall(
  tool: ToolName,
  rawArgs: Record<string, unknown>,
  state: HouseholdState,
  context: InterceptContext
): InterceptedRequest {
  const nowMs = context.nowMs ?? Date.now();
  const sanitizedArgs: Record<string, unknown> = {};
  const strippedUntrustedFields: string[] = [];

  for (const [key, value] of Object.entries(rawArgs || {})) {
    if (UNTRUSTED_FACT_KEYS.has(key)) {
      strippedUntrustedFields.push(key);
    } else {
      sanitizedArgs[key] = value;
    }
  }

  const rawPayee =
    typeof sanitizedArgs.payee === 'string'
      ? sanitizedArgs.payee.trim()
      : typeof sanitizedArgs.biller === 'string'
        ? sanitizedArgs.biller.trim()
        : tool === 'reorder_medicine'
          ? 'Pharmacy'
          : '';

  const payee_known =
    rawPayee.length > 0 &&
    state.savedPayees.some((saved) => saved.toLowerCase() === rawPayee.toLowerCase());

  const hour_ist = getISTHour(new Date(nowMs));

  const oneHourAgo = nowMs - 60 * 60 * 1000;
  const payments_last_hour = state.paymentsTimestamps.filter((ts) => ts >= oneHourAgo).length;

  const scamDetails = scanForScam(context.messageText);
  const scam_flag = scamDetails.scamFlag;

  const frozen = state.frozen;

  let approval_valid = false;
  if (context.approvalId && state.approvals[context.approvalId]) {
    const req = state.approvals[context.approvalId];
    const notExpired = nowMs <= req.expiresAtMs;
    const coolingDone = req.status === 'cooling_off' && req.coolingOffRemainingSec <= 0;
    const requestedAmount =
      typeof sanitizedArgs.amountPaise === 'number' ? sanitizedArgs.amountPaise : 0;
    const amountMatches = requestedAmount === req.amountPaise;
    const payeeMatches =
      rawPayee.length === 0 || req.payee.toLowerCase() === rawPayee.toLowerCase();

    approval_valid = notExpired && coolingDone && amountMatches && payeeMatches;
  }

  return {
    tool,
    sanitizedArgs,
    strippedUntrustedFields,
    facts: {
      payee_known,
      hour_ist,
      payments_last_hour,
      approval_valid,
      scam_flag,
      frozen,
    },
    scamDetails,
  };
}

/**
 * Step 2 of the Guardian Gate:
 * Cedar-style policy engine. Makes the ONLY decision: ALLOW, DENY, or NEEDS_APPROVAL.
 */
export function evaluatePolicy(
  tool: ToolName,
  sanitizedArgs: Record<string, unknown>,
  facts: GuardianFacts,
  limits: HouseholdLimits
): PolicyEvaluation {
  // Rule 0: Scam Shield overrides everything dangerous
  if (
    facts.scam_flag &&
    (MONEY_TOOLS.has(tool) || tool === 'assess_risk' || tool === 'request_approval')
  ) {
    return {
      decision: 'DENY',
      riskLevel: 'R4',
      riskLabel: en.risk.R4,
      reason: en.policyReasons.scamFlag,
      reasonBilingual: { en: en.policyReasons.scamFlag, hi: hi.policyReasons.scamFlag },
      ruleId: 'forbid::scam_shield_triggered',
    };
  }

  // Rule 1: Read tools → ALLOW (R0 Read)
  if (READ_TOOLS.has(tool)) {
    return {
      decision: 'ALLOW',
      riskLevel: 'R0',
      riskLabel: en.risk.R0,
      reason: en.policyReasons.readOnly,
      reasonBilingual: { en: en.policyReasons.readOnly, hi: hi.policyReasons.readOnly },
      ruleId: 'permit::read_only_tools',
    };
  }

  // Rule 2: Non-financial low-risk errands (appointments, reminders)
  if (LOW_RISK_TOOLS.has(tool)) {
    if (facts.scam_flag) {
      return {
        decision: 'DENY',
        riskLevel: 'R4',
        riskLabel: en.risk.R4,
        reason: en.policyReasons.scamFlag,
        reasonBilingual: { en: en.policyReasons.scamFlag, hi: hi.policyReasons.scamFlag },
        ruleId: 'forbid::scam_shield_triggered',
      };
    }
    return {
      decision: 'ALLOW',
      riskLevel: 'R1',
      riskLabel: en.risk.R1,
      reason: en.policyReasons.lowRiskErrand,
      reasonBilingual: {
        en: en.policyReasons.lowRiskErrand,
        hi: hi.policyReasons.lowRiskErrand,
      },
      ruleId: 'permit::low_risk_household_errand',
    };
  }

  // Rule 3: Frozen account blocks ALL money movement
  if (facts.frozen && (MONEY_TOOLS.has(tool) || tool === 'request_approval')) {
    return {
      decision: 'DENY',
      riskLevel: 'R4',
      riskLabel: en.risk.R4,
      reason: en.policyReasons.frozen,
      reasonBilingual: { en: en.policyReasons.frozen, hi: hi.policyReasons.frozen },
      ruleId: 'forbid::account_frozen_by_guardian',
    };
  }

  // Rule 4: Explicit request_approval tool
  if (tool === 'request_approval') {
    return {
      decision: 'NEEDS_APPROVAL',
      riskLevel: 'R3',
      riskLabel: en.risk.R3,
      reason: en.policyReasons.requestApproval,
      reasonBilingual: {
        en: en.policyReasons.requestApproval,
        hi: hi.policyReasons.requestApproval,
      },
      ruleId: 'require::guardian_approval',
    };
  }

  // Rule 5+: Money tools (pay_bill, reorder_medicine)
  const amountPaise =
    typeof sanitizedArgs.amountPaise === 'number' && Number.isFinite(sanitizedArgs.amountPaise)
      ? Math.max(0, Math.round(sanitizedArgs.amountPaise))
      : 0;

  // Hard cap above absolute max (₹25,000)
  if (amountPaise > limits.absoluteMaxPaise) {
    const maxStr = formatINR(limits.absoluteMaxPaise);
    const reasonEn = interpolate(en.policyReasons.exceedsMax, { max: maxStr });
    const reasonHi = interpolate(hi.policyReasons.exceedsMax, { max: maxStr });
    return {
      decision: 'DENY',
      riskLevel: 'R4',
      riskLabel: en.risk.R4,
      reason: reasonEn,
      reasonBilingual: { en: reasonEn, hi: reasonHi },
      ruleId: 'forbid::exceeds_absolute_max',
    };
  }

  // Permit small known-payee auto-approve (≤ ₹2,000 AND payee_known)
  if (amountPaise <= limits.autoApproveLimitPaise && facts.payee_known) {
    const limitStr = formatINR(limits.autoApproveLimitPaise);
    const reasonEn = interpolate(en.policyReasons.smallKnownPayee, { limit: limitStr });
    const reasonHi = interpolate(hi.policyReasons.smallKnownPayee, { limit: limitStr });
    return {
      decision: 'ALLOW',
      riskLevel: 'R2',
      riskLabel: en.risk.R2,
      reason: reasonEn,
      reasonBilingual: { en: reasonEn, hi: reasonHi },
      ruleId: 'permit::small_known_payee',
    };
  }

  // Permit guardian-approved + cooling-off complete within absolute max
  if (facts.approval_valid && amountPaise <= limits.absoluteMaxPaise) {
    return {
      decision: 'ALLOW',
      riskLevel: 'R3',
      riskLabel: en.risk.R3,
      reason: en.policyReasons.guardianApproved,
      reasonBilingual: {
        en: en.policyReasons.guardianApproved,
        hi: hi.policyReasons.guardianApproved,
      },
      ruleId: 'permit::guardian_approved_within_max',
    };
  }

  // Default for all other money calls: NEEDS_APPROVAL
  const limitStr = formatINR(limits.autoApproveLimitPaise);
  const reasonBilingual: BilingualText = !facts.payee_known
    ? { en: en.policyReasons.unverifiedPayee, hi: hi.policyReasons.unverifiedPayee }
    : {
        en: interpolate(en.policyReasons.aboveAutoLimit, { limit: limitStr }),
        hi: interpolate(hi.policyReasons.aboveAutoLimit, { limit: limitStr }),
      };

  return {
    decision: 'NEEDS_APPROVAL',
    riskLevel: 'R3',
    riskLabel: en.risk.R3,
    reason: reasonBilingual.en,
    reasonBilingual,
    ruleId: 'require::two_key_guardian_approval',
  };
}
