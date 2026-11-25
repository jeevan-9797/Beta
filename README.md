# Beta
THE CODE FOR THIS PROJECT ARE IN RESPECTIVE BRANCHES, NOTHING IS MERGED TO MAIN YET.

# BETA — The Two-Key AI Agent for India's Parents

> **Scam Shield warns. The Guardian Gate guarantees.**

**BETA** is a bilingual (Hindi/English) voice-first assistant that lets elderly parents run everyday errands—paying utility bills, booking doctor slots, and reordering medicines—while protecting them from "digital arrest" and coercion scams using a **two-key authorization architecture**.

Here is the link for the **mvp functional prototype** - https://ai.studio/apps/3d28a6c4-34a7-4997-8dda-9ba92ea91b99 
---

## Core Idea

In a scam, the fraudster controls the parent, and the parent controls the AI. An AI that simply obeys its user becomes the scammer's accomplice.

BETA splits authority across two principals outside the LLM:
1. **Key 1 (Parent):** Speaks naturally over a call—no apps to install or screens to tap.
2. **Key 2 (Family):** Enforced deterministically via **Cedar Policy** at **Amazon Bedrock AgentCore Gateway**. High-value, unknown, or flagged actions require WhatsApp approval + a cooling-off timer, and a single **Freeze** tap locks all money tools instantly.

---

## Architecture

Parent (ElevenLabs Voice Agent: Hindi / English)
      │
      ▼  MCP JSON-RPC (tools/call)
AWS Bedrock AgentCore Gateway
  ├── 1. Request Interceptor (Lambda) ──► Strips spoofed fields; injects ground-truth facts from DynamoDB
  ├── 2. Policy Engine (Cedar)        ──► Default-deny rule wall (ALLOW / DENY)
  └── 3. Response Interceptor         ──► Hides money tools on tools/list when Frozen
      │
      ├──► errands-mcp   (Bills, Doctor Slots via Google Calendar, Receipts)
      ├──► guardian-mcp  (Twilio WhatsApp Approvals, Cooling-Off, Freeze, Audit)
      └──► shield-mcp    (Deterministic Rules + Amazon Bedrock Nova Micro Risk Scoring)
