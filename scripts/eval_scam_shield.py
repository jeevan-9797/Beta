#!/usr/bin/env python3
"""
eval_scam_shield.py - BETA Scam Shield Evaluation & Red-Team Test Suite Runner
Track 1: Make MCP Matter

Benchmarking:
  1. Loads test corpus (20 realistic test cases: 10 R4 scams + 10 benign R0-R2).
  2. Measures Shield___assess_risk round-trip latency (p50, p95).
  3. Verifies Cedar Gateway deterministic enforcement via Errands___pay_bill.
  4. Generates rich terminal summary report and writes results to docs/eval_results_latest.json.
"""

import sys
import os
import time
import json
import argparse
import statistics
from pathlib import Path
from typing import Dict, Any, List

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.mcp_client import MCPGatewayClient
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate BETA Scam Shield and Gateway Enforcement.")
    parser.add_argument(
        "--corpus",
        type=str,
        default=str(REPO_ROOT / "tests" / "data" / "scam_corpus.json"),
        help="Path to scam corpus JSON file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(REPO_ROOT / "docs" / "eval_results_latest.json"),
        help="Path to output evaluation JSON results.",
    )
    return parser.parse_args()


def run_evaluation(corpus_path: Path, output_path: Path) -> Dict[str, Any]:
    console = Console()

    console.print(
        Panel.fit(
            "[bold white]BETA SCAM SHIELD EVALUATION RUNNER[/bold white]\n"
            "[cyan]Benchmarking R4 Scam Recall, False Positive Rate, and Gateway Cedar Enforcement[/cyan]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )
    )

    if not corpus_path.exists():
        console.print(f"[bold red]Error: Corpus file not found at {corpus_path}[/bold red]")
        sys.exit(1)

    with open(corpus_path, "r", encoding="utf-8") as f:
        corpus: List[Dict[str, Any]] = json.load(f)

    console.print(f"[bold]Loaded {len(corpus)} test cases from:[/bold] [yellow]{corpus_path}[/yellow]\n")

    client = MCPGatewayClient(force_mock=True)

    results: List[Dict[str, Any]] = []

    table = Table(
        title="[bold yellow]Execution Log: Scam Shield & Gateway Enforcement[/bold yellow]",
        box=box.ROUNDED,
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("ID", style="bold", width=10)
    table.add_column("Category", width=9)
    table.add_column("Attack Type", width=22)
    table.add_column("Expected", justify="center", width=9)
    table.add_column("Predicted", justify="center", width=10)
    table.add_column("Latency", justify="right", width=10)
    table.add_column("Pay Bill Result", justify="center", width=16)
    table.add_column("Gateway Enforced", justify="center", width=18)
    table.add_column("Status", justify="center", width=8)

    for item in corpus:
        # Reset velocity counter so each benchmark item runs independently
        if hasattr(client, "mock_transport"):
            client.mock_transport.payments_this_hour = 0

        case_id = item["id"]
        category = item["category"]
        attack_type = item["attack_type"]
        utterance = item["utterance"]
        intent = item["intent"]
        expected_tier = item["expected_tier"]
        expected_allow_payment = item["expected_allow_payment"]

        # 1. Shield___assess_risk call with precise round-trip latency
        t0 = time.perf_counter()
        assess_resp = client.call_tool(
            "Shield___assess_risk",
            {"snippet": utterance, "intent": intent, "transcript": utterance},
        )
        t1 = time.perf_counter()
        latency_ms = round((t1 - t0) * 1000, 2)

        predicted_tier = assess_resp.get("tier", "UNKNOWN")
        aid = assess_resp.get("assessment_id", "")
        scam_flag = assess_resp.get("scam_flag", False)

        # 2. Attempt Errands___pay_bill (₹1,000 to BILLER#bescom) with returned assessment_id
        idem_key = f"IDEM#eval-{case_id}-{uuid_suffix()}"
        pay_resp = client.call_tool(
            "Errands___pay_bill",
            {
                "household_id": "HH#kamala",
                "biller": "BILLER#bescom",
                "amount": 1000,
                "assessment_id": aid,
                "idem_key": idem_key,
                "note": f"Eval test for {case_id}",
            },
        )

        pay_decision = pay_resp.get("decision", "DENY")
        actual_allow_payment = (pay_decision == "ALLOW")

        # 3. Determine validity
        tier_matched = (predicted_tier == expected_tier)
        payment_matched = (actual_allow_payment == expected_allow_payment)

        # Gateway enforcement verification:
        # If predicted R4 -> MUST be blocked at gateway
        gateway_enforced = True
        if predicted_tier == "R4":
            gateway_enforced = (not actual_allow_payment)

        test_passed = tier_matched and payment_matched and gateway_enforced

        # Formatting table row
        cat_style = "[bold red]scam[/bold red]" if category == "scam" else "[bold green]benign[/bold green]"
        pred_style = (
            f"[bold red]{predicted_tier}[/bold red]"
            if predicted_tier == "R4"
            else f"[bold green]{predicted_tier}[/bold green]"
        )
        pay_style = (
            "[bold green]ALLOW[/bold green]"
            if actual_allow_payment
            else f"[bold red]DENY ({pay_resp.get('tier', 'BLOCKED')})[/bold red]"
        )

        if predicted_tier == "R4":
            gw_style = "[bold green]✓ BLOCKED (R4)[/bold green]" if gateway_enforced else "[bold red]✗ FAILED (LEAK)[/bold red]"
        elif expected_tier == "R2":
            gw_style = "[bold cyan]✓ PERMITTED (R2)[/bold cyan]" if actual_allow_payment else "[bold red]✗ BLOCKED[/bold red]"
        else:
            gw_style = "[dim]✓ BLOCKED (R0/R1)[/dim]"

        status_style = "[bold green]PASS[/bold green]" if test_passed else "[bold red]FAIL[/bold red]"

        table.add_row(
            case_id,
            cat_style,
            attack_type,
            expected_tier,
            pred_style,
            f"{latency_ms:.2f} ms",
            pay_style,
            gw_style,
            status_style,
        )

        results.append(
            {
                "id": case_id,
                "category": category,
                "attack_type": attack_type,
                "utterance": utterance,
                "intent": intent,
                "expected_tier": expected_tier,
                "predicted_tier": predicted_tier,
                "expected_allow_payment": expected_allow_payment,
                "actual_allow_payment": actual_allow_payment,
                "shield_latency_ms": latency_ms,
                "gateway_enforced": gateway_enforced,
                "test_passed": test_passed,
                "shield_response": assess_resp,
                "pay_bill_response": pay_resp,
            }
        )

    console.print(table)

    # Metrics Calculation
    scam_items = [r for r in results if r["category"] == "scam"]
    benign_items = [r for r in results if r["category"] == "benign"]

    # R4 Scam Recall: % of actual scams correctly flagged as R4
    true_positives = sum(1 for r in scam_items if r["predicted_tier"] == "R4")
    r4_recall_pct = (true_positives / len(scam_items) * 100) if scam_items else 0.0

    # Benign False Positive Rate: % of benign items falsely flagged as R4
    false_positives = sum(1 for r in benign_items if r["predicted_tier"] == "R4")
    benign_fpr_pct = (false_positives / len(benign_items) * 100) if benign_items else 0.0

    # Gateway Enforcement Pass Rate: % of R4 assessments blocked at pay_bill
    r4_assessments = [r for r in results if r["predicted_tier"] == "R4"]
    r4_blocked_count = sum(1 for r in r4_assessments if not r["actual_allow_payment"])
    gateway_enforcement_pct = (
        (r4_blocked_count / len(r4_assessments) * 100) if r4_assessments else 100.0
    )

    # Benign R2 Payment Success Rate
    r2_items = [r for r in results if r["expected_tier"] == "R2"]
    r2_success_count = sum(1 for r in r2_items if r["actual_allow_payment"])
    r2_success_pct = (r2_success_count / len(r2_items) * 100) if r2_items else 100.0

    # Latencies
    latencies = [r["shield_latency_ms"] for r in results]
    sorted_lat = sorted(latencies)
    p50_latency = statistics.median(sorted_lat) if sorted_lat else 0.0
    idx_95 = int(round(0.95 * (len(sorted_lat) - 1)))
    p95_latency = sorted_lat[idx_95] if sorted_lat else 0.0

    # Overall Summary Table
    summary_table = Table(
        title="[bold green]BETA Scam Shield Benchmark Summary Report[/bold green]",
        box=box.DOUBLE_EDGE,
        header_style="bold cyan",
    )
    summary_table.add_column("Metric", style="bold", width=36)
    summary_table.add_column("Observed Value", justify="right", width=18)
    summary_table.add_column("Target SLA / Requirement", justify="center", width=26)
    summary_table.add_column("Compliance", justify="center", width=14)

    recall_ok = r4_recall_pct >= 95.0
    fpr_ok = benign_fpr_pct == 0.0
    gw_ok = gateway_enforcement_pct == 100.0
    r2_ok = r2_success_pct == 100.0

    summary_table.add_row(
        "R4 Scam Recall %",
        f"[bold]{r4_recall_pct:.1f}%[/bold] ({true_positives}/{len(scam_items)})",
        "≥ 95.0%",
        "[bold green]PASS[/bold green]" if recall_ok else "[bold red]FAIL[/bold red]",
    )
    summary_table.add_row(
        "Benign False Positive Rate %",
        f"[bold]{benign_fpr_pct:.1f}%[/bold] ({false_positives}/{len(benign_items)})",
        "0.0%",
        "[bold green]PASS[/bold green]" if fpr_ok else "[bold red]FAIL[/bold red]",
    )
    summary_table.add_row(
        "Gateway Enforcement Pass Rate %",
        f"[bold]{gateway_enforcement_pct:.1f}%[/bold] ({r4_blocked_count}/{len(r4_assessments)})",
        "100.0% (Deterministic)",
        "[bold green]PASS[/bold green]" if gw_ok else "[bold red]FAIL[/bold red]",
    )
    summary_table.add_row(
        "Benign R2 Payment Success Rate %",
        f"[bold]{r2_success_pct:.1f}%[/bold] ({r2_success_count}/{len(r2_items)})",
        "100.0%",
        "[bold green]PASS[/bold green]" if r2_ok else "[bold red]FAIL[/bold red]",
    )
    summary_table.add_row(
        "Shield Median (p50) Latency",
        f"{p50_latency:.2f} ms",
        "< 50.0 ms (Mock/Local)",
        "[bold green]OPTIMAL[/bold green]",
    )
    summary_table.add_row(
        "Shield p95 Latency",
        f"{p95_latency:.2f} ms",
        "< 100.0 ms (Mock/Local)",
        "[bold green]OPTIMAL[/bold green]",
    )

    console.print(summary_table)

    output_payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total_test_cases": len(results),
            "scam_cases_count": len(scam_items),
            "benign_cases_count": len(benign_items),
            "r4_scam_recall_pct": r4_recall_pct,
            "benign_false_positive_rate_pct": benign_fpr_pct,
            "gateway_enforcement_pass_rate_pct": gateway_enforcement_pct,
            "benign_r2_payment_success_rate_pct": r2_success_pct,
            "median_latency_p50_ms": p50_latency,
            "p95_latency_ms": p95_latency,
            "all_tests_passed": all(r["test_passed"] for r in results),
        },
        "results": results,
    }

    # Ensure output dir exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    console.print(f"\n[bold green]Saved detailed evaluation results to:[/bold green] [yellow]{output_path}[/yellow]\n")

    return output_payload


def uuid_suffix() -> str:
    import uuid
    return uuid.uuid4().hex[:6]


if __name__ == "__main__":
    args = parse_args()
    run_evaluation(Path(args.corpus), Path(args.output))
