import test from 'node:test';
import assert from 'node:assert/strict';
import {
  en,
  hi,
  detectParentLanguage,
  formatLocalizedDate,
  formatLocalizedSlot,
  formatLocalizedTimeFromHoursMinutes,
} from './index';

function collectKeys(obj: Record<string, unknown>, prefix = ''): string[] {
  const keys: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      keys.push(...collectKeys(v as Record<string, unknown>, full));
    } else {
      keys.push(full);
    }
  }
  return keys.sort();
}

test('en.ts and hi.ts have identical translation keys with zero missing or empty values', () => {
  const enKeys = collectKeys(en as unknown as Record<string, unknown>);
  const hiKeys = collectKeys(hi as unknown as Record<string, unknown>);
  assert.deepEqual(enKeys, hiKeys);

  // Check every leaf string is non-empty
  for (const key of enKeys) {
    const parts = key.split('.');
    let enVal: unknown = en;
    let hiVal: unknown = hi;
    for (const p of parts) {
      enVal = (enVal as Record<string, unknown>)[p];
      hiVal = (hiVal as Record<string, unknown>)[p];
    }
    assert.equal(typeof enVal, 'string');
    assert.equal(typeof hiVal, 'string');
    assert.ok((enVal as string).trim().length > 0, `Empty en key: ${key}`);
    assert.ok((hiVal as string).trim().length > 0, `Empty hi key: ${key}`);
  }
});

test('Hindi glossary terms match specification exactly', () => {
  assert.equal(hi.console.tabLiveFeed, 'लाइव गतिविधि');
  assert.equal(hi.console.approveBtn, 'मंज़ूर करें');
  assert.equal(hi.whatsapp.approveBtn, 'मंज़ूर करें');
  assert.equal(hi.console.declineBtn, 'मना करें');
  assert.equal(hi.whatsapp.declineBtn, 'मना करें');
  assert.equal(hi.console.cancelPaymentBtn, 'भुगतान रद्द करें');
  assert.equal(hi.console.freezeBtn, 'फ़्रीज़ करें');
  assert.equal(hi.console.unfreezeBtn, 'फ़्रीज़ हटाएँ');
  assert.equal(hi.console.statusFrozen, 'फ़्रीज़ है');
  assert.equal(hi.console.statusProtected, 'सुरक्षित');
  assert.equal(hi.decision.ALLOW, 'अनुमति मिली');
  assert.equal(hi.decision.DENY, 'रोका गया');
  assert.equal(hi.decision.NEEDS_APPROVAL, 'आपकी मंज़ूरी चाहिए');
  assert.equal(hi.console.limits.coolingOff, 'प्रतीक्षा समय');
});

test('Hindi uses respectful आप and never तुम', () => {
  const serialized = JSON.stringify(hi);
  assert.equal(/तुम/.test(serialized), false, 'Hindi strings must use आप, never तुम');
});

test('date and 12-hour time formatters use Latin digits and morning/afternoon/evening/night in Hindi', () => {
  assert.equal(formatLocalizedDate('28 Nov', 'en'), '28 Nov');
  assert.equal(formatLocalizedDate('28 Nov', 'hi'), '28 नवंबर');
  assert.equal(formatLocalizedDate('02 Dec', 'hi'), '02 दिसंबर');

  assert.equal(formatLocalizedTimeFromHoursMinutes(9, 15, 'en'), '9:15 AM');
  assert.equal(formatLocalizedTimeFromHoursMinutes(9, 15, 'hi'), 'सुबह 9:15');
  assert.equal(formatLocalizedTimeFromHoursMinutes(14, 5, 'en'), '2:05 PM');
  assert.equal(formatLocalizedTimeFromHoursMinutes(14, 5, 'hi'), 'दोपहर 2:05');
  assert.equal(formatLocalizedTimeFromHoursMinutes(17, 30, 'hi'), 'शाम 5:30');
  assert.equal(formatLocalizedTimeFromHoursMinutes(21, 0, 'hi'), 'रात 9:00');

  assert.equal(formatLocalizedSlot('29 Nov 11:00 AM', 'hi'), '29 नवंबर, सुबह 11:00');
});

test('detectParentLanguage accurately distinguishes Devanagari/Hinglish vs English', () => {
  assert.equal(detectParentLanguage('बिजली का बिल आया क्या?'), 'hi');
  assert.equal(detectParentLanguage('Bijli ka bill aaya kya?'), 'hi');
  assert.equal(detectParentLanguage('Dawai order karo'), 'hi');
  assert.equal(detectParentLanguage('Is my electricity bill due?'), 'en');
  assert.equal(detectParentLanguage('Please reorder my medicines'), 'en');
});
