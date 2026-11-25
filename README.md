# BETA: AI Voice Agent & Scam Shield for Indian Seniors

BETA is an intelligent voice companion and fraud protection shield designed specifically for Indian senior citizens. Combining **ElevenLabs Conversational AI Agents** (voice, emotional intelligence, ultra-low latency Hinglish speech) and **AWS Bedrock AgentCore Gateway (ap-south-1 Mumbai)** with a high-speed **Shield MCP Server** and **DynamoDB Single-Table Persistence**, BETA empowers elders to independently handle everyday errands while guarding them against prevalent digital fraud schemes.

---

## 🛡️ Architecture & Components

```
                    ┌───────────────────────────────────────────────┐
                    │          Indian Senior Citizen                │
                    │   (Dadaji / Dadima / Uncleji / Auntyji)       │
                    └───────────────────────┬───────────────────────┘
                                            │ Voice (Hinglish/English)
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │      ElevenLabs Conversational AI Agent       │
                    │  - Agent Config: agent_config/system_prompt.md│
                    │  - 3 Namespaces: errands-mcp, guardian-mcp,   │
                    │                  shield-mcp                   │
                    │  - execution_mode: post_tool_speech (pay_bill)│
                    │  - Mandatory Payee & Amount Read-Back         │
                    └───────────────────────┬───────────────────────┘
                                            │ MCP / REST / Lambda Targets
                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │       AWS Bedrock AgentCore Gateway           │
                    │               (ap-south-1)                    │
                    └───────┬───────────────────────────────┬───────┘
                            │                               │
                            ▼                               ▼
       ┌─────────────────────────────┐   ┌─────────────────────────────────────┐
       │      shield-mcp Server      │   │ Bedrock Plain-Language Receipt Gen  │
       │  - FastMCP HTTP/SSE         │   │  - Spoken Hinglish Summary for Voice│
       │  - AWS Lambda Target        │   │  - Structured WhatsApp for Guardian │
       │    (lambda_handler)         │   │  - 1-Sentence "Why BETA did this"   │
       │  - Step 1: 0.03ms Fast Path │   │    (ap-south-1 Amazon Nova/Bedrock) │
       │  - Step 2: Bedrock Fallback │   └─────────────────────────────────────┘
       │  - DynamoDB: BETA_Household │
       │    PK=HH#<id>, SK=ASSESS#<id│
       └──────────────┬──────────────┘
                      │
                      ▼
       ┌─────────────────────────────┐
       │ Scam Shield Eval Benchmark  │
       │  data/scam_corpus_eval.json │
       │  (80 Indian Senior Cases)   │
       │  eval_results.md            │
       └─────────────────────────────┘
```

---

## 📂 Project Structure

```
Beta/
├── pyproject.toml                     # Modern Python project configuration
├── requirements.txt                   # Frozen dependencies (mcp, fastmcp, boto3, pydantic, pytest)
├── README.md                          # Architecture documentation & setup guide
├── eval_results.md                    # Benchmark evaluation report (pitch slide numbers)
├── .gitignore                         # Python/IDE ignore rules
├── agent_config/
│   ├── system_prompt.md               # Canonical Hinglish/English system prompt with de-escalation
│   ├── system_prompt_live.md          # Live prompt mapped to exact Gateway-prefixed tool names
│   ├── tool_descriptions.json         # Base tool definitions (shield-mcp, errands-mcp, guardian-mcp)
│   ├── tool_descriptions_live.json    # Live ElevenLabs tool schema synced from Gateway tools/list
│   └── elevenlabs_setup_checklist.md  # Production dashboard setup & Journeys A, B, C voice verification
├── scripts/
│   └── sync_gateway_tools.py          # Auto-sync Gateway tools/list via MCP Streamable HTTP/SSE
├── data/
│   └── scam_corpus_eval.json          # 80 realistic benchmark test cases (40 scams, 40 benign)
├── src/
│   ├── shared/
│   │   ├── __init__.py
│   │   └── contracts.py               # Pydantic v2 schemas: RiskTier, RiskLevel, AssessRisk, ExplainScam, Receipt
│   ├── shield_mcp/
│   │   ├── __init__.py
│   │   ├── keywords.py                # Deterministic scam keyword lists & whitelists for Request Interceptor
│   │   └── server.py                  # FastMCP & AWS Lambda handler with DynamoDB single-table persistence
│   ├── bedrock/
│   │   ├── __init__.py
│   │   └── receipt_generator.py       # Plain-language receipt generator with 1-sentence "Why did BETA do this?"
│   └── eval/
│       ├── __init__.py
│       └── run_scam_eval.py           # Evaluation harness runner (recall, confusion matrix, latency)
└── tests/
    ├── __init__.py
    ├── test_contracts.py              # Pydantic contract validation tests
    ├── test_eval_corpus.py            # Dataset structure and schema compliance tests
    ├── test_shield_mcp.py             # Shield MCP, DynamoDB single-table & Lambda handler unit tests
    └── test_receipt.py                # Bedrock Receipt Generator unit tests
```

---

## 🚦 Guardian Gate Risk Tiers (`src/shared/contracts.py`)

| Tier | Category | Operational Policy | Tool Action |
| :--- | :--- | :--- | :--- |
| **R0** | **Read** | Pure informational query (check balance, read bill, calendar lookup) | Read info to elder directly. |
| **R1** | **Low Errand** | Low reversible errands / calendar (doctor appointment, reminder) | Call `errands.book_appointment` or `errands.set_reminder`. |
| **R2** | **Small Known Payee** | Routine payment to whitelisted biller with **amount <= ₹2,000** | Read back payee/amount -> Call `shield.assess_risk` -> `errands.pay_bill`. |
| **R3** | **Guardian Approval** | High amount (**> ₹2,000**), new payee, or odd hour | Call `shield.assess_risk` -> Call `guardian.request_approval` via WhatsApp. |
| **R4** | **Danger / Scam** | Coercive fraud (Digital arrest, fake police/CBI, 9:30 PM power cut, APK install) | **STRICT BLOCK**. Call `shield.assess_risk` -> `shield.explain_scam` -> `guardian.notify_alert`. |

---

## 🗄️ DynamoDB Single-Table Schema (`BETA_Household`)

Managed in `src/shield_mcp/server.py` with support for local `MOCK_DB=true`:
- **Partition Key (`PK`)**: `f"HH#{household_id}"`
- **Sort Key (`SK`)**: `f"ASSESS#{assessment_id}"`
- **Attributes**:
  - `assessment_id`: Cryptographic hex token
  - `household_id`: Senior household identifier
  - `risk_level`: `LOW` | `MEDIUM` | `HIGH`
  - `tier`: `R0` | `R1` | `R2` | `R3` | `R4`
  - `signals`: Array of detected heuristic strings
  - `used`: `False` (marks `True` on execution to prevent token replay)
  - `created_at`: Unix epoch timestamp (seconds)
  - `expires_at`: Unix epoch timestamp (`now + 300s`, 5-minute TTL)
  - `recommended_action`: `ALLOW` | `CONFIRM_WITH_USER` | `REQUIRE_GUARDIAN_APPROVAL` | `BLOCK_AND_ALERT`

Jeevan's **Request Interceptor Lambda** can import `get_assessment_record(hh, token)` and `mark_assessment_used(hh, token)` to verify unused, non-expired tokens.

---

## ⚡ Multi-Transport Deployment (FastMCP + AWS Lambda)

`src/shield_mcp/server.py` exposes:
1. **FastMCP Server**: Run locally or via SSE/HTTP (`python src/shield_mcp/server.py`).
2. **AWS Lambda Handler (`lambda_handler`)**: Attach directly as an Action Group target in AWS Bedrock AgentCore Gateway in `ap-south-1`.

---

## 🎙️ Official Demo Seed Journeys

| Journey | Path & Gate | Scenario & Seed Figure | Expected Outcome |
| :--- | :--- | :--- | :--- |
| **Journey A** | **Happy Path**<br>(Tier R2, $\le$ ₹2,000) | **BESCOM electricity bill for ₹1,240 (due 28 Nov)** + Dr. Sharma routine BP checkup appointment | Payee & amount read-back completed $\rightarrow$ payment executed via `Errands___pay_bill` $\rightarrow$ appointment booked $\rightarrow$ plain-language audio receipt. |
| **Journey B** | **Second Key**<br>(Tier R3, > ₹2,000) | **Apollo Pharmacy chronic medicine reorder for ₹3,800** (above ₹2,000 limit) | Payment held $\rightarrow$ 20-second demo cooling-off countdown initiated $\rightarrow$ interactive approval request dispatched to family guardian's WhatsApp via `Guardian___request_approval`. |
| **Journey C** | **Scam Path**<br>(Tier R4, Threat) | **Fake CBI digital arrest call demanding ₹2,00,000** over Skype video call | Instantly blocked in <0.05ms by Scam Shield & Cedar Policy $\rightarrow$ `Shield___explain_scam` calms elder $\rightarrow$ `Guardian___notify_alert` $\rightarrow$ National Cyber Helpline **1930** recited. |

For the complete ElevenLabs dashboard setup and live voice test scripts, see [`agent_config/elevenlabs_setup_checklist.md`](agent_config/elevenlabs_setup_checklist.md).

---

## 🔄 Live ElevenLabs Gateway Tool Synchronization

Synchronize tools directly from Jeevan's live AWS Bedrock AgentCore Gateway:
```bash
python scripts/sync_gateway_tools.py --gateway-url "https://<gateway-id>.execute-api.ap-south-1.amazonaws.com/mcp"
```
Or run in offline/mock mode:
```bash
python scripts/sync_gateway_tools.py --mock
```
This automatically maps dot-notation names to Gateway-prefixed names (e.g. `Errands___pay_bill`, `Shield___assess_risk`), outputs `agent_config/tool_descriptions_live.json`, and updates `agent_config/system_prompt_live.md`.

For dashboard setup and Journeys A, B, and C voice verification scripts, see [`agent_config/elevenlabs_setup_checklist.md`](agent_config/elevenlabs_setup_checklist.md).

---

## 📊 Benchmark Evaluation Report (`eval_results.md`)

Run the automated evaluation harness on the 80 test cases:
```bash
.venv\Scripts\python src/eval/run_scam_eval.py
```

### Pitch Slide Metrics:
- **R4 Scam Recall (Sensitivity)**: **100.0%** (40 / 40 Scams Blocked, 0 Missed)
- **False-Positive Rate (FPR)**: **0.0%** (0 False Alarms on Benign Errands)
- **Guardian Gate Tier Accuracy**: **100.0%** (80 / 80 Cases Matched Exact Tier)
- **Median Fast-Path Latency**: **0.029 ms**
- **Average Shield Latency**: **0.037 ms**
- **p95 Latency**: **0.045 ms**

---

## 🧪 Running Tests

```bash
.venv\Scripts\python -m pytest
```
```text
======================= 19 passed in 4.41s =======================
```
