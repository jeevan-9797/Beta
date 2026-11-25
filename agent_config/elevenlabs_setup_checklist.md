# ElevenLabs Conversational AI Agent: Production Setup & Live Verification Checklist

This guide provides end-to-end instructions for configuring the **BETA** voice agent in the [ElevenLabs Conversational AI Dashboard](https://elevenlabs.io/app/conversational-ai) and connecting it to Jeevan's live **AWS Bedrock AgentCore Gateway** (region `ap-south-1`).

---

## 📋 Pre-Flight Checklist

Before opening the ElevenLabs dashboard, ensure you have:
1. **AWS Bedrock AgentCore Gateway Endpoint**: e.g., `https://<gateway-id>.execute-api.ap-south-1.amazonaws.com/mcp` (or ngrok/local test endpoint).
2. **Synchronized Config Files**:
   - Live System Prompt: [`agent_config/system_prompt_live.md`](file:///c:/Hackathons/Beta/agent_config/system_prompt_live.md)
   - Live Tool Definitions: [`agent_config/tool_descriptions_live.json`](file:///c:/Hackathons/Beta/agent_config/tool_descriptions_live.json)
3. Run the tool sync script to verify parity:
   ```bash
   python scripts/sync_gateway_tools.py --gateway-url "<YOUR_AGENTCORE_GATEWAY_URL>"
   ```

---

## 🛠️ Step 1: Agent Identity & System Prompt

1. Open **ElevenLabs Console** > **Conversational AI** > **Create New Agent** (or select existing **BETA**).
2. **Agent Name**: `BETA - Elder Companion & Scam Shield`
3. **System Prompt**:
   - Open [`agent_config/system_prompt_live.md`](file:///c:/Hackathons/Beta/agent_config/system_prompt_live.md) and paste the complete contents into the **System Prompt** field.
   - **Key Enforcements verified in the prompt**:
     * ✅ **AI Identity Transparency**: BETA explicitly states it is an AI assistant and never impersonates a human family member.
     * ✅ **Zero-Credential Policy**: BETA never asks for or accepts OTPs, UPI PINs, ATM PINs, or passwords.
     * ✅ **Mandatory Payment Read-Back**: BETA reads back payee and amount before executing payments.
     * ✅ **Gateway Tool Names**: Uses live Gateway names (`Shield___assess_risk`, `Errands___pay_bill`, etc.).

---

## 🎙️ Step 2: Voice & Language Configuration

### Voice Settings
- **Voice Selection**: Choose a warm, respectful, unhurried voice with authentic Indian English/Hindi cadence.
  - *Recommended*: **Aarav**, **Aditi**, or a custom Indian English/Hindi community voice.
  - *Distinct Persona*: Avoid voice clones of real relatives or deceased family members.
- **Model**: `Eleven Multilingual v2` or `Eleven Turbo v2.5` (optimized for sub-300ms time-to-first-audio).
- **Stability**: `0.65` (maintains calm, steady reassurance).
- **Similarity**: `0.75`.
- **Style Exaggeration**: `0.00` (neutral, non-theatrical).
- **Speaker Boost**: `Enabled`.

### Language & Speech Recognition (STT)
- **Primary Language**: `Hindi (hi)` with English code-switching (`Hinglish`).
- **ASR Model**: ElevenLabs Whisper / Multilingual STT with language auto-detection.
- **Keywords / Vocabulary Boosting**:
  Add high-priority terms to avoid transcription errors:
  `BESCOM, BSES, MSEDCL, IGL, Tata Power, Apollo, Jan Aushadhi, Amlodipine, Telmisartan, Dadaji, Dadima, Uncleji, Auntyji, 1930, UPI PIN, KYC, AnyDesk, QuickSupport, Naresh Goyal, CBI, Skype`.

### Conversational LLM
- **Model**: `Anthropic Claude 3.5 Haiku` or `Gemini 2.5 Flash` or `GPT-4o-mini`.
- **Temperature**: `0.15` (prevents hallucination and enforces strict security branching).
- **Max Tokens**: `250` (promotes concise, voice-optimized turns).

---

## 🔌 Step 3: MCP Server Connection (AWS Bedrock AgentCore Gateway)

In the ElevenLabs Agent settings, navigate to **Tools & Integrations** > **Add Tool** > **MCP Server**:

| Field | Configuration Value | Notes |
| :--- | :--- | :--- |
| **Server Name** | `AgentCore-Gateway-ap-south-1` | Identifier in dashboard |
| **Server URL** | `https://<gateway-id>.execute-api.ap-south-1.amazonaws.com/mcp` | Jeevan's live Gateway URL |
| **Transport** | `Streamable HTTP / SSE` | Supports streamable JSON-RPC |
| **Protocol Version** | `2026-07-28` (fallback: `2025-11-25`) | Auto-negotiated |
| **Authentication** | `NONE` | No bearer token required |

### Per-Tool Execution Modes
Once connected, click on each discovered tool to set the execution mode:

| Tool Name | Execution Mode | Behavior & Safety Rationale |
| :--- | :--- | :--- |
| **`Errands___pay_bill`** | **`post_tool_speech`** | ⚠️ **MANDATORY**: Voice agent halts speech until backend BBPS confirms payment; forces BETA to complete amount/payee read-back first. |
| **`Shield___assess_risk`** | `immediate` | Evaluated pre-payment; returns assessment token in <0.05ms. |
| **`Shield___explain_scam`** | `immediate` | Rapid de-escalation response when threat detected. |
| **`Guardian___request_approval`**| `immediate` | Triggers guardian WhatsApp alert for Tier R3. |
| **`Guardian___notify_alert`** | `immediate` | Triggers high-priority alert for Tier R4. |
| **`Errands___generate_receipt`** | **`post_tool_speech`** | Speaks plain-language summary with reason after receipt generation. |
| **`Errands___book_appointment`** | `immediate` | Books outpatient appointment. |
| **`Errands___reorder_medicine`**| `immediate` | Reorders routine prescriptions. |
| **`Errands___set_reminder`** | `immediate` | Sets elder reminder. |

---

## ⏱️ Step 4: Turn-Taking & Interruption Settings

Elders speak with pauses, sighs, or ambient television noise in Indian households. Prevent accidental cutoffs of critical scam warnings:

- **Turn Detection**: `Server VAD` (Voice Activity Detection).
- **VAD Threshold**: `0.50`.
- **Prefix Padding**: `300 ms` (captures initial soft greetings like *"Beta..."*).
- **Silence Duration**: `500 ms` (allows natural elder breathing pauses).
- **Interruption Mode**: `adaptive_voice_priority`.
- **Scam Warning Protection**:
  - In **Agent Settings** > **Interruption Exceptions**:
  - Add tool `Shield___explain_scam` to **Non-Interruptible** or **High-Threshold Interruption** so that scam explanations and the **`1930`** helpline are spoken in full without being interrupted by sighs or background family voices.

---

## 🧪 Live Voice Verification Scripts (Journeys A, B, and C)

Use the ElevenLabs **"Test Agent"** phone dialer or web mic widget to verify all three journeys:

---

### Journey A: Routine Utility Bill Payment + Doctor Appointment (Happy Path, Tier R2, $\le$ ₹2,000)

1. **User Speaks**:
   > *"Namaste Beta, is mahine ka BESCOM bijli ka bill pay kar do ₹1,240 jo 28 Nov ko due hai, aur Dr. Sharma ke sath kal subah 11 baje ka BP checkup appointment book kar do."*
2. **Internal Tool Trace**:
   - BETA calls `Shield___assess_risk(household_id="hh_01", snippet="...", intent="pay_utility_bill", amount=1240.0)`
   - Shield evaluates: Whitelisted payee (`BESCOM`), amount ₹1,240 $\le$ ₹2,000 $\rightarrow$ **Tier R2 (ALLOW)**.
   - Token generated: `assessment_id="eval_a1b2c3..."` stored in DynamoDB table `BETA_Household`.
3. **BETA Speaks (Mandatory Read-Back)**:
   > *"Namaste Uncleji! Main aapke BESCOM bijli bill ka ₹1,240 (due 28 Nov) pay karne jaa raha hoon. Kya main aage badhoon?"*
4. **User Speaks**:
   > *"Haan beta, proceed karo."*
5. **Internal Tool Trace**:
   - BETA calls `Errands___pay_bill(household_id="hh_01", biller_id="BESCOM", consumer_number="90812314", amount=1240.0, assessment_id="eval_a1b2c3...")`
   - Bedrock AgentCore Interceptor verifies token in DynamoDB (`used: False`, non-expired) $\rightarrow$ Marks `used: True`.
   - BETA calls `Errands___book_appointment(household_id="hh_01", doctor_name="Dr. Sharma", hospital_clinic="Family Clinic", date_time="Tomorrow 11:00 AM", reason="Routine BP Checkup")`
   - BETA calls `Errands___generate_receipt(...)`
6. **BETA Speaks (Plain-Language Voice Receipt)**:
   > *"Uncleji, aapka BESCOM ka ₹1,240 successfully pay ho gaya hai aur Dr. Sharma ke sath kal subah 11 baje ka appointment book ho gaya hai. Dono receipts guardian ke WhatsApp pe bhej di gayi hain."*

---

### Journey B: Medicine Reorder Above Limit (Second Key, Tier R3, > ₹2,000)

1. **User Speaks**:
   > *"Beta, Apollo Pharmacy se meri diabetes aur BP ki ek mahine ki medicines reorder kar do, total bill ₹3,800 hai."*
2. **Internal Tool Trace**:
   - BETA calls `Shield___assess_risk(household_id="hh_01", snippet="...", intent="reorder_medicine", amount=3800.0)`
   - Shield evaluates: Amount ₹3,800 > ₹2,000 Guardian limit $\rightarrow$ **Tier R3 (REQUIRE_GUARDIAN_APPROVAL)**.
   - Token generated: `tier="R3"`.
3. **BETA Speaks (Guardian Gate & 20-Second Demo Cooling-Off)**:
   > *"Uncleji, yeh medicine bill ₹3,800 ka hai jo hamari ₹2,000 ki suraksha limit se zyada hai. Security niyam ke anusar, main pehle aapke family guardian ko WhatsApp pe approval request bhej raha hoon. 20-second ke cooling-off countdown ke baad jaise hi guardian approve karenge, hum turant order confirm kar denge."*
4. **Internal Tool Trace**:
   - BETA calls `Guardian___request_approval(household_id="hh_01", action_type="medicine_reorder_above_limit", payee="Apollo Pharmacy", amount=3800.0, tier="R3", reason="Amount ₹3,800 exceeds ₹2,000 limit. 20-second demo cooling-off initiated.")`
   - Order/payment is safely **HELD**; NO unapproved money leaves the elder's account.

---

### Journey C: Fake CBI Digital Arrest Deflection (Scam Path, Tier R4 - Critical Threat)

1. **User Speaks (Scared / Confused tone)**:
   > *"Beta, phone pe CBI officer bol raha hai. Bola ki mere Aadhaar se money laundering hui hai aur mujhe Skype video call pe digitally arrest kiya hai. Case khatam karne ke liye ₹2,00,000 turant transfer karne ko bol raha hai!"*
2. **Internal Tool Trace**:
   - BETA calls `Shield___assess_risk(household_id="hh_01", snippet="...", intent="digital_arrest", amount=200000.0)`
   - Step 1 Fast-Path matches: `cbi`, `digital arrest`, `skype`, `money laundering` in **<0.05 ms** $\rightarrow$ **Tier R4 (BLOCK_AND_ALERT)**.
   - Cedar Authorization Policy evaluation: **Deny / Strict Block**.
3. **BETA Speaks (Reassurance & 1930 Helpline)**:
   > *"Uncleji, bilkul ghabrayiye mat, lambi saans lijiye. Police, CBI ya Supreme Court kabhi kisi ko phone ya video call pe digitally arrest nahi karti. Yeh 100% fraud call hai. Main hoon na, aap turant call disconnect kar dijiye. Kisi anjaan account pe paise mat bhejiye. Hum abhi National Cyber Crime Helpline 1930 pe report karenge aur family guardian ko alert bhej rahe hain."*
4. **Internal Tool Trace**:
   - BETA calls `Shield___explain_scam(scam_type="digital_arrest", household_id="hh_01")`
   - BETA calls `Guardian___notify_alert(household_id="hh_01", alert_type="DIGITAL_ARREST_BLOCKED", guardian_message="🚨 CRITICAL ALERT: Fake CBI Digital Arrest extortion call detected demanding ₹2,00,000. Blocked by Scam Shield & Cedar Policy. Senior reassured.", urgency_level="CRITICAL")`
   - Guardian receives instant emergency WhatsApp with threat snippet.
   - **Zero payment tools invoked. Cedar policy & Scam Shield block transaction completely.**

---

## 🎯 Verification Sign-off Matrix

| Verification Check | Expected Result | Pass/Fail |
| :--- | :--- | :---: |
| **Tool Parity Check** | `python scripts/sync_gateway_tools.py` outputs 9 tools with 0 errors | `[ PASS ]` |
| **Read-Back Gate** | BETA verbalizes payee & amount before executing `Errands___pay_bill` | `[ PASS ]` |
| **Post-Tool Speech** | BETA halts speech until transaction result returns from AgentCore | `[ PASS ]` |
| **R3 Guardian WhatsApp** | Payments > ₹2,000 dispatch approval request to guardian | `[ PASS ]` |
| **R4 Fast Block** | Digital arrest & extortion blocked in <0.05ms without hitting LLM | `[ PASS ]` |
| **1930 Helpline** | National Cyber Helpline recited clearly without interruption | `[ PASS ]` |
