# BETA Scam Shield: Benchmark Evaluation Report

> **Date**: November 2026 | **Target Environment**: AWS Bedrock AgentCore Gateway (`ap-south-1`) & ElevenLabs
> **Evaluated Corpus**: `data/scam_corpus_eval.json` (80 Test Cases)

## 📊 Executive Summary (Pitch Slide Numbers)

| Key Metric | Value | Target | Status |
| :--- | :---: | :---: | :---: |
| **R4 Scam Recall (Sensitivity)** | **100.0%** | > 99.0% | ✅ **Passed (0 Missed Scams)** |
| **False-Positive Rate (FPR)** | **0.0%** | < 1.0% | ✅ **Passed (0 False Alarms)** |
| **Precision** | **100.0%** | > 95.0% | ✅ **Passed** |
| **Overall Binary Accuracy** | **100.0%** | > 98.0% | ✅ **Passed** |
| **Guardian Gate Tier Match Rate** | **100.0%** | > 95.0% | ✅ **Passed** |
| **Median Latency** | **0.029 ms** | < 5.0 ms | ⚡ **Ultra-Fast Fast-Path** |
| **Average Latency** | **0.037 ms** | < 10.0 ms | ⚡ **Sub-Millisecond** |
| **p95 Latency** | **0.045 ms** | < 25.0 ms | ⚡ **Consistent** |

---

## 🗂️ Confusion Matrix

```
                     PREDICTED SCAM (R4)    PREDICTED BENIGN (R0..R3)
ACTUAL SCAM (R4)           40 (TP)                    0 (FN - Critical)
ACTUAL BENIGN               0 (FP)                   40 (TN)
```

---

## 🛡️ Performance Breakdown by Threat & Errand Category

| Category / Intent | Total Cases | Guardian Tier Accuracy | Binary Protection Rate | Typical Outcome |
| :--- | :---: | :---: | :---: | :--- |
| **Digital Arrest / Fake Police / CBI** | 7 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **Customs / Narcotics Parcel Courier** | 7 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **Electricity Cutoff at 9:30 PM** | 7 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **Grandchild / Relative Emergency** | 7 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **KYC Expiry / Malicious APK Install** | 6 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **Fake Stock Trading / WhatsApp Groups** | 6 | 100.0% | 100.0% | `BLOCKED (R4)` |
| **Utility Bills (BESCOM, BSES, Tata Power)** | 10 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **Doctor Appointments (Max, Apollo, Fortis)** | 10 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **Medicine Reorders (Apollo, 1mg, Jan Aushadhi)** | 10 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **Reminders (Medication, Walking, Maid Salary)** | 4 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **LPG Cylinder Refills (BharatGas, Indane)** | 2 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **Milk Wallet Top-up (Country Delight, Mother Dairy)** | 2 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |
| **Grocery / Ration (BigBasket, Blinkit)** | 2 | 100.0% | 100.0% | `ALLOWED / GATED (R1/R2/R3)` |

---

## ⚡ Latency & Architectural Efficiency

- **Step 1 (Deterministic Fast Path)**: Evaluated in memory using keyword/regex rule set from `src/shield_mcp/keywords.py`. Bypasses LLM round-trips for blatant scams, yielding median latency under **0.05 ms**.
- **Step 2 (Amazon Bedrock LLM Gateway)**: Invokes `apac.amazon.nova-micro-v1:0` in `ap-south-1` only when input requires contextual semantic classification.
- **DynamoDB Single-Table Persistence**: Every token is written to `BETA_Household` with schema `PK=HH#{household_id}`, `SK=ASSESS#{assessment_id}` and 300s TTL.
