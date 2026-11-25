import test from 'node:test';
import assert from 'node:assert/strict';
import { formatINR } from './format';
import { scanForScam, DIGITAL_ARREST_BETA_REPLY } from './scamShield';
import { createInitialHouseholdState } from './store';
import { interceptToolCall, evaluatePolicy } from './policy';
import { parseIntentFallback } from './intent';
import {
  executeGuardedToolCall,
  approveGuardianRequest,
  completeCoolingOffAndExecute,
  toggleHouseholdFreeze,
} from './tools';

test('formatINR uses Indian digit grouping from integer paise', () => {
  assert.equal(formatINR(20000000), '₹2,00,000');
  assert.equal(formatINR(124000), '₹1,240');
  assert.equal(formatINR(380000), '₹3,800');
  assert.equal(formatINR(50000), '₹500');
  assert.equal(formatINR(2500000), '₹25,000');
  assert.equal(formatINR(12550), '₹125.50');
});

test('interceptor discards spoofed fact fields supplied in tool args', () => {
  const state = createInitialHouseholdState();
  state.frozen = true;

  const intercepted = interceptToolCall(
    'pay_bill',
    {
      payee: 'Unknown Scammer',
      amountPaise: 50000,
      // Attempted spoofing by untrusted AI payload:
      payee_known: true,
      frozen: false,
      scam_flag: false,
      approval_valid: true,
      payments_last_hour: 0,
    },
    state,
    { messageText: 'CBI se phone aaya, transfer urgently' }
  );

  assert.equal(intercepted.facts.payee_known, false);
  assert.equal(intercepted.facts.frozen, true);
  assert.equal(intercepted.facts.scam_flag, true);
  assert.equal(intercepted.facts.approval_valid, false);
  assert.equal('payee_known' in intercepted.sanitizedArgs, false);
  assert.equal('frozen' in intercepted.sanitizedArgs, false);
  assert.ok(intercepted.strippedUntrustedFields.includes('payee_known'));
  assert.ok(intercepted.strippedUntrustedFields.includes('frozen'));
});

test('policy allows read tools (R0 Read) even when household is frozen', () => {
  const state = toggleHouseholdFreeze(createInitialHouseholdState(), true);
  const intercepted = interceptToolCall('get_due_bills', { biller: 'BESCOM' }, state, {
    messageText: 'Bijli ka bill aaya kya?',
  });
  const decision = evaluatePolicy('get_due_bills', intercepted.sanitizedArgs, intercepted.facts, state.limits);

  assert.equal(decision.decision, 'ALLOW');
  assert.equal(decision.riskLevel, 'R0');
  assert.equal(decision.riskLabel, 'R0 Read');
});

test('policy denies money tool when frozen', () => {
  const state = toggleHouseholdFreeze(createInitialHouseholdState(), true);
  const intercepted = interceptToolCall(
    'pay_bill',
    { payee: 'Airtel', amountPaise: 50000 },
    state,
    { messageText: 'Airtel ka ₹500 bill bhar do' }
  );
  const decision = evaluatePolicy('pay_bill', intercepted.sanitizedArgs, intercepted.facts, state.limits);

  assert.equal(decision.decision, 'DENY');
  assert.equal(decision.riskLevel, 'R4');
  assert.equal(decision.reason, 'Account frozen by guardian');
});

test('policy auto-allows ≤ ₹2,000 for saved payee (BESCOM ₹1,240)', () => {
  const state = createInitialHouseholdState();
  const intercepted = interceptToolCall(
    'pay_bill',
    { payee: 'BESCOM', amountPaise: 124000 },
    state,
    { messageText: 'Haan, bhar do' }
  );
  const decision = evaluatePolicy('pay_bill', intercepted.sanitizedArgs, intercepted.facts, state.limits);

  assert.equal(decision.decision, 'ALLOW');
  assert.equal(decision.riskLevel, 'R2');
  assert.equal(intercepted.facts.payee_known, true);
});

test('policy requires guardian approval for ₹3,800 medicine reorder and allows after approval + cooling-off', () => {
  let state = createInitialHouseholdState();
  const parsed = parseIntentFallback('Dawai order karo', state);
  assert.equal(parsed.tool, 'reorder_medicine');
  assert.equal(parsed.args.amountPaise, 380000);

  const first = executeGuardedToolCall(state, parsed, 'Dawai order karo');
  assert.equal(first.auditEvent?.decision, 'NEEDS_APPROVAL');
  assert.equal(first.auditEvent?.riskLevel, 'R3');
  assert.ok(first.createdApproval);

  const approvalId = first.createdApproval!.id;
  state = approveGuardianRequest(first.nextState, approvalId, 'whatsapp');
  assert.equal(state.approvals[approvalId].status, 'cooling_off');

  const final = completeCoolingOffAndExecute(state, approvalId);
  assert.equal(final.auditEvent?.decision, 'ALLOW');
  assert.equal(final.auditEvent?.facts.approval_valid, true);
  assert.equal(final.nextState.receipts.length, 1);
  assert.equal(final.nextState.approvals[approvalId].status, 'executed');
});

test('scam shield blocks digital arrest call (Hinglish & Devanagari) with WhatsApp 1930 alert', () => {
  const msg = 'CBI se phone aaya, parcel mein drugs mila, ₹2 lakh transfer karna hai, kisi ko batana mat';
  const scan = scanForScam(msg);
  assert.equal(scan.scamFlag, true);
  assert.equal(scan.betaReply, DIGITAL_ARREST_BETA_REPLY);

  const devanagariMsg = 'CBI से फ़ोन आया, पार्सल में ड्रग्स मिला, ₹2 लाख ट्रांसफ़र करना है, किसी को बताना मत';
  const devScan = scanForScam(devanagariMsg);
  assert.equal(devScan.scamFlag, true);

  const state = createInitialHouseholdState();
  const parsed = parseIntentFallback(devanagariMsg, state);
  assert.equal(parsed.tool, 'pay_bill');
  assert.equal(parsed.args.amountPaise, 20000000);

  const outcome = executeGuardedToolCall(state, parsed, devanagariMsg);
  assert.equal(outcome.auditEvent?.decision, 'DENY');
  assert.equal(outcome.auditEvent?.riskLevel, 'R4');
  assert.equal(outcome.auditEvent?.facts.scam_flag, true);
  assert.equal(outcome.replyLang, 'hi');

  const lastWa = outcome.nextState.whatsappMessages[outcome.nextState.whatsappMessages.length - 1];
  assert.equal(lastWa.kind, 'scam_alert');
  assert.equal(lastWa.helpline, '1930');
});

test('parent speaking English receives warm English BETA reply while Hindi receives Devanagari Hindi', () => {
  const state = createInitialHouseholdState();
  const enParsed = parseIntentFallback('Is my electricity bill due?', state);
  const enOutcome = executeGuardedToolCall(state, enParsed, 'Is my electricity bill due?');
  assert.equal(enOutcome.replyLang, 'en');
  assert.ok(enOutcome.betaReply.includes('BESCOM'));
  assert.ok(enOutcome.betaReply.includes('28 Nov'));

  const hiParsed = parseIntentFallback('बिजली का बिल आया क्या?', state);
  const hiOutcome = executeGuardedToolCall(state, hiParsed, 'बिजली का बिल आया क्या?');
  assert.equal(hiOutcome.replyLang, 'hi');
  assert.ok(hiOutcome.betaReply.includes('28 नवंबर'));
});

test('idempotency prevents paying the same BESCOM bill twice', () => {
  const state = createInitialHouseholdState();
  const parsed = { tool: 'pay_bill' as const, args: { payee: 'BESCOM', amountPaise: 124000 }, source: 'fallback' as const };

  const first = executeGuardedToolCall(state, parsed, 'Haan, bhar do');
  assert.equal(first.nextState.receipts.length, 1);
  assert.equal(first.nextState.paymentsTimestamps.length, 1);

  const second = executeGuardedToolCall(first.nextState, parsed, 'Haan, bhar do');
  assert.equal(second.auditEvent?.idempotencyHit, true);
  assert.equal(second.nextState.receipts.length, 1);
  assert.equal(second.nextState.paymentsTimestamps.length, 1);
});
