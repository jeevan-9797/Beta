# BETA — The Two-Key AI Agent for India's Parents

BETA is a bilingual (English and Hindi/Hinglish) voice AI prototype for elderly Indian parents, pairing a live parent call interface with a Guardian Console and WhatsApp family approval flow. Every parent request passes through an untrusted-field-stripping Interceptor, Scam Shield, and a Cedar-style Guardian Gate policy engine before execution—auto-allowing routine saved bills and appointments within limits, requiring family WhatsApp approval with a 20-second cooling-off countdown for higher-value purchases, blocking digital-arrest or OTP/PIN scam attempts with 1930 helpline alerts, and enforcing instant guardian account freezes.

## How to Run

**Prerequisites:** Node.js

1. Install dependencies:
   ```bash
   npm install
   ```
2. Copy `.env.example` to `.env.local` and add your Gemini API key:
   ```bash
   cp .env.example .env.local
   ```
   Set `GEMINI_API_KEY` in `.env.local` (if omitted, `/api/intent` falls back to the built-in keyword matcher).
3. Start the development server:
   ```bash
   npm run dev
   ```
   The app will be served on `http://localhost:3000`.

## Demo Scenarios

Use the **Demo** menu in the top bar to auto-play the 4 prototype scenarios:

- **A. Bill + doctor:** Kamala checks and pays her saved BESCOM electricity bill (₹1,240 auto-ALLOWED) and books an appointment with Dr. Kulkarni (with a Google Calendar event).
- **B. Medicine approval:** Kamala requests a ₹3,800 pharmacy refill (Metformin 500mg + Amlodipine 5mg), triggering a Guardian Gate WhatsApp approval request from Ravi, a 20-second cooling-off countdown, and a receipt.
- **C. Scam call:** Kamala repeats a "CBI digital arrest" demand to transfer ₹2,00,000 in secret; Scam Shield immediately DENIES the request and raises a 1930 cybercrime helpline alert.
- **D. Freeze:** Ravi freezes the Deshpande household account from the Guardian Console, and Kamala's subsequent ₹500 Airtel bill payment is DENIED instantly.

## Prototype Note

> **Note:** Payments and pharmacy integrations are mocked in this frontend prototype. Production architecture uses ElevenLabs + AWS AgentCore Gateway with Cedar policies.
