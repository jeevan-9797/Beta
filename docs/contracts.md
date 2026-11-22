# BETA Backend API & Interface Contracts

**Project:** BETA - AWS Bedrock AgentCore Voice Assistant for Elderly Parents in India  
**Target AWS Region:** `ap-south-1` (Mumbai)  
**Primary Language/Stack:** Python 3.12+, Boto3, Bedrock AgentCore, Cedar Policy Engine, FastAPI, Twilio WhatsApp  

---

## 1. Architecture Overview

BETA bridges elderly parents (e.g., Kamala, 74, Dharwad) and adult children / guardians (e.g., Arjun, Bengaluru) through voice-guided assistance backed by strict financial and security guardrails.

```
                  ┌──────────────────────────────────────────────┐
                  │          Kamala (Elderly Parent)             │
                  │   Voice Input (Kannada / English / Hindi)    │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │    AWS Bedrock AgentCore Voice Gateway       │
                  └──────┬────────────────────────────────┬──────┘
                         │                                │
        (Pre-Tool Execution)                     (Post-Execution)
                         ▼                                ▼
              ┌─────────────────────┐          ┌─────────────────────┐
              │ Request Interceptor │          │Response Interceptor │
              │ • 6 Security Facts  │          │ • PII Sanitization  │
              │ • Cedar Guardrails  │          │ • Tone Adaptation   │
              │ • Cooling-off Delay │          │ • Audit Persistence │
              └──────────┬──────────┘          └─────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────────┐ ┌─────────┐ ┌─────────────────┐
│   errands-mcp   │ │shield-mcp│ │  guardian-mcp   │
│ • pay_utility   │ │• screen │ │ • req_approval  │
│ • reorder_meds  │ │  scam   │ │ • check_status  │
│ • check_bills   │ │• verify │ │ • send_alert    │
│ • list_billers  │ │  payee  │ │ • get_rules     │
└────────┬────────┘ └────┬────┘ └────────┬────────┘
         │               │               │
         └───────────────┼───────────────┘
                         ▼
             ┌───────────────────────┐             ┌─────────────────────────┐
             │ DynamoDB (BETA_Main)  │◄────────────┤  Guardian REST API &    │
             │   Single-Table Store  │             │ Twilio WhatsApp Webhook │
             └───────────────────────┘             └────────────▲────────────┘
                                                                │
                                                   ┌────────────┴────────────┐
                                                   │    Arjun (Guardian)     │
                                                   │   WhatsApp (+91-98765)  │
                                                   └─────────────────────────┘
```

---

## 2. DynamoDB Single-Table Schema (`BETA_Main`)

- **Table Name:** `BETA_Main`
- **Billing Mode:** `PAY_PER_REQUEST`
- **Partition Key (PK):** `HH#<household_id>` (String, e.g. `HH#KAMALA_01`)
- **Sort Key (SK):** Prefixed String
- **TTL Attribute:** `ttl` (Unix epoch timestamp in seconds)

### Sort Key (SK) Prefixes & Entity Models

| Entity | SK Pattern | Key Attributes | Description |
|---|---|---|---|
| **Member** | `MEMBER#<member_id>` | `name`, `role`, `age`, `location`, `phone`, `relation` | Household profile (Parent vs Guardian). |
| **Rules** | `RULE#FINANCIAL` | `auto_limit`, `max_with_approval`, `frozen`, `cooling_off_seconds`, `night_start`, `night_end`, `max_payments_per_hour` | Security policies and spending limits. |
| **Biller** | `BILLER#<biller_id>` | `name`, `utility_type`, `bill_id`, `amount`, `due_date`, `account_no`, `status` | Registered utility providers (BESCOM, BWSSB). |
| **Payee** | `PAYEE#<payee_id>` | `name`, `category`, `upi_id`, `verified`, `trusted` | Whitelisted merchants (e.g. Apollo Pharmacy). |
| **Medicine** | `MED#<med_id>` | `name`, `dosage`, `schedule`, `refill_qty`, `unit_price`, `reorder_total`, `prescribed_by` | Chronic prescription catalog. |
| **Approval** | `APPROVAL#<approval_id>` | `approval_id`, `errand_type`, `payee_name`, `amount`, `status`, `requested_at`, `cooling_off_until`, `expires_at`, `guardian_response` | Guardian authorization transaction state. |
| **Assessment** | `ASSESSMENT#<session_id>` | `session_id`, `sentiment`, `confusion_score`, `audio_clarity`, `timestamp` | Voice interaction telemetry. |
| **Audit** | `AUDIT#<iso_timestamp>` | `action`, `actor`, `details`, `risk_score`, `status`, `timestamp`, `ttl` | Immutable audit trail with 90-day TTL. |
| **Receipt** | `RECEIPT#<tx_id>` | `receipt_id`, `biller_id`, `amount`, `bill_id`, `paid_at`, `status` | Completed transaction receipt. |
| **Payment** | `PAYMENT#<idem_key>` | `tx_id`, `payee_name`, `amount`, `status`, `timestamp`, `approval_id` | Idempotency record for payment deduplication. |

---

## 3. The 6 Injected Security Fact Fields

Evaluated by the Bedrock AgentCore **Request Interceptor** before any sensitive tool call:

| Field | Type | Description | Source / Computation |
|---|---|---|---|
| `payee_known` | `boolean` | `true` if payee or utility biller is present on the household whitelist (`BILLER#` or `PAYEE#`). | Checked against `BILLER#<id>` and `PAYEE#<id>`. |
| `hour_ist` | `integer` | Current hour in Indian Standard Time (UTC+5:30), 0 through 23. | `(UTC_now + 5h30m).hour`. Used for night curfew (23:00 to 06:00). |
| `payments_last_hour`| `integer` | Count of completed payments executed within the past 60 minutes. | Count of `PAYMENT#*` with `timestamp >= now - 1hr` and `status == SUCCESS`. |
| `approval_valid` | `boolean` | `true` if an approval exists in status `APPROVED`, not expired, and past cooling-off period. | Checked against `APPROVAL#<id>`. |
| `scam_flag` | `boolean` | `true` if the utterance contains high-risk Indian senior citizen extortion keywords. | Regex scan for "digital arrest", "police verification", "KYC suspended", "urgent APK download", etc. |
| `frozen` | `boolean` | `true` if household transactions are administratively locked. | Read from `RULE#FINANCIAL` attribute `frozen`. |

---

## 4. MCP Server 1: `errands-mcp`

**Endpoint/Server ID:** `errands-mcp`  
**Description:** Errand execution tools for utility bills and pharmacy reorders.

### Tool 1: `pay_utility_bill`
Executes payment for a verified utility biller (e.g. BESCOM, BWSSB).

- **Input Parameters:**
  ```json
  {
    "biller_id": "BESCOM",
    "amount": 1240.0,
    "household_id": "HH#KAMALA_01",
    "approval_id": "APP_9A4B12C3"
  }
  ```
- **Output Schema:**
  ```json
  {
    "success": true,
    "tx_id": "TX_4E21D0A8B731",
    "biller_name": "BESCOM Electricity",
    "amount_paid": 1240.0,
    "bill_id": "BILL_BESCOM_NOV",
    "receipt_id": "TX_4E21D0A8B731",
    "message": "Successfully paid ₹1240.0 to BESCOM Electricity. Receipt #TX_4E21D0A8B731."
  }
  ```

### Tool 2: `reorder_medicines`
Refills prescription medications from trusted pharmacy partners (e.g. Apollo Pharmacy).

- **Input Parameters:**
  ```json
  {
    "medicine_ids": ["METFORMIN_500MG", "AMLODIPINE_5MG"],
    "pharmacy_name": "Apollo Pharmacy",
    "household_id": "HH#KAMALA_01",
    "approval_id": "APP_9A4B12C3"
  }
  ```
- **Output Schema:**
  ```json
  {
    "success": true,
    "order_id": "MED_1F8A90B2C4",
    "pharmacy": "Apollo Pharmacy",
    "total_amount": 3800.0,
    "items": ["Metformin 500mg (500mg)", "Amlodipine 5mg (5mg)"],
    "delivery_estimate": "Tomorrow by 2:00 PM to Dharwad home",
    "message": "Placed order with Apollo Pharmacy for ₹3800.0. Delivery expected tomorrow."
  }
  ```

### Tool 3: `check_bill_status`
Queries pending amounts and due dates for utilities.

- **Input Parameters:** `{"biller_id": "BESCOM", "household_id": "HH#KAMALA_01"}`
- **Output:** Current bill status, amount due, and due date.

### Tool 4: `list_saved_billers`
Lists all whitelisted utility billers and merchants for the household.

---

## 5. MCP Server 2: `guardian-mcp`

**Endpoint/Server ID:** `guardian-mcp`  
**Description:** Guardian authorization workflows, WhatsApp notifications, and security alerts.

### Tool 1: `request_guardian_approval`
Generates a new approval request, applies cooling-off delay, and notifies Arjun on WhatsApp.

- **Input Parameters:**
  ```json
  {
    "errand_type": "MEDICINE_REORDER",
    "payee_name": "Apollo Pharmacy",
    "amount": 3800.0,
    "reason": "Kamala monthly diabetes and BP tablets",
    "household_id": "HH#KAMALA_01"
  }
  ```
- **Output Schema:**
  ```json
  {
    "success": true,
    "approval_id": "APP_C180EFA2",
    "status": "COOLING_OFF",
    "amount": 3800.0,
    "payee_name": "Apollo Pharmacy",
    "cooling_off_seconds": 20,
    "cooling_off_until": "2026-11-22T14:45:20Z",
    "message": "Sent approval request to Arjun on WhatsApp for ₹3800.0 to Apollo Pharmacy. Cooling-off delay of 20 seconds is active."
  }
  ```

### Tool 2: `check_approval_status`
Checks if the guardian has approved the request via WhatsApp or Dashboard.

- **Input:** `{"approval_id": "APP_C180EFA2", "household_id": "HH#KAMALA_01"}`
- **Output:** `status` (`COOLING_OFF`, `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`).

### Tool 3: `send_guardian_alert`
Sends high-priority WhatsApp alert for security or health anomalies.

- **Input:**
  ```json
  {
    "alert_type": "SCAM_SUSPECTED",
    "message": "Caller claimed to be CBI officer threatening digital arrest.",
    "severity": "CRITICAL",
    "household_id": "HH#KAMALA_01"
  }
  ```

### Tool 4: `get_household_rules`
Fetches active spending limits, curfew hours, and frozen flag.

---

## 6. MCP Server 3: `shield-mcp`

**Endpoint/Server ID:** `shield-mcp`  
**Description:** Protective fraud prevention, scam pattern detection, and emergency freeze.

### Tool 1: `screen_scam_risk`
Screens speech transcripts or text for extortion patterns.

- **Input:** `{"text": "Police officer demanding urgent transfer under digital arrest", "household_id": "HH#KAMALA_01"}`
- **Output Schema:**
  ```json
  {
    "success": true,
    "scam_detected": true,
    "risk_score": 0.95,
    "recommendation": "BLOCK_AND_ALERT_GUARDIAN"
  }
  ```

### Tool 2: `verify_payee_trust`
Validates whether a payee or UPI handle is approved for transactions.

- **Input:** `{"payee_name": "Apollo Pharmacy", "upi_id": "apollopharmacy@icici"}`
- **Output:** `{"is_known": true, "status": "APPROVED_PAYEE"}`

### Tool 3: `evaluate_cooling_off`
Calculates seconds remaining on a cooling-off timer.

### Tool 4: `trigger_emergency_freeze`
Immediately sets `frozen = true` in DynamoDB.

---

## 7. Guardian REST API & Twilio WhatsApp Webhook

Base URL: `http://localhost:8000` (or API Gateway URL)

### Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | Health check probe. |
| `GET` | `/api/household/{household_id}/status` | Complete household status: members, rules, pending approvals, billers. |
| `GET` | `/api/household/{household_id}/approvals` | List all historical and pending approvals. |
| `POST` | `/api/household/{household_id}/approvals/{approval_id}/action` | Guardian dashboard approve/reject action. |
| `POST` | `/api/household/{household_id}/emergency_freeze` | Toggle emergency freeze kill switch. |
| `GET` | `/api/household/{household_id}/audit_log` | Retrieve compliance audit trail (newest first). |
| `POST` | `/api/webhook/whatsapp` | Twilio incoming WhatsApp webhook. |

### Twilio WhatsApp Command Protocol

When Arjun replies to Twilio WhatsApp messages:

| Inbound WhatsApp Body | System Reaction |
|---|---|
| `APPROVE <approval_id>` or `YES` | Sets approval status to `APPROVED` (only if cooling-off timer has elapsed). |
| `REJECT <approval_id>` or `NO` | Sets approval status to `REJECTED`. |
| `FREEZE` | Immediately sets `frozen = true` and halts all transactions. |
| `UNFREEZE` | Restores normal transaction operations. |
| `STATUS` | Replies with count and IDs of pending approval requests. |
