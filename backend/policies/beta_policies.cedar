// ==============================================================================
// BETA Cedar Authorization Policies
// AWS Bedrock AgentCore Gateway Guardrails for Senior Citizen Protection
// Target Actions: Errands___pay_bill, Errands___reorder_medicine, Guardian___*, Shield___*
// Target Resource: AgentCore::Gateway::"<GATEWAY_ARN>"
// ==============================================================================

// ------------------------------------------------------------------------------
// Rule 1: Emergency Freeze Kill Switch
// Forbid financial money-movement actions when household frozen flag is True
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        AgentCore::Action::"Errands___pay_bill",
        AgentCore::Action::"Errands___reorder_medicine"
    ],
    resource
)
when {
    context has input && context.input has frozen && context.input.frozen
};

// ------------------------------------------------------------------------------
// Rule 2: Coercion & Scam Detection Guardrail
// Forbid financial money-movement actions when scam_flag is True
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        AgentCore::Action::"Errands___pay_bill",
        AgentCore::Action::"Errands___reorder_medicine"
    ],
    resource
)
when {
    context has input && context.input has scam_flag && context.input.scam_flag
};

// ------------------------------------------------------------------------------
// Rule 3: R2 Auto-Pay for Errands___pay_bill and Errands___reorder_medicine
// Allows automated utility and errand payment under ₹2,000 to registered payees
// during daytime hours (06:00 to 23:00 IST), within hourly rate limit (<2),
// and with a valid unconsumed voice assessment.
// ------------------------------------------------------------------------------
permit (
    principal,
    action in [
        AgentCore::Action::"Errands___pay_bill",
        AgentCore::Action::"Errands___reorder_medicine"
    ],
    resource
)
when {
    context.input.amount <= 2000 &&
    context.input.payee_known &&
    context.input.assessment_valid &&
    context.input.hour_ist >= 6 &&
    context.input.hour_ist < 23 &&
    context.input.payments_last_hour < 2
};

// ------------------------------------------------------------------------------
// Rule 4: R3 Approved Pay for Errands___pay_bill and Errands___reorder_medicine
// Allows payments up to ₹25,000 if guardian approval is valid & cooling-off elapsed
// ------------------------------------------------------------------------------
permit (
    principal,
    action in [
        AgentCore::Action::"Errands___pay_bill",
        AgentCore::Action::"Errands___reorder_medicine"
    ],
    resource
)
when {
    context.input.approval_valid &&
    context.input.assessment_valid &&
    context.input.amount <= 25000
};

// ------------------------------------------------------------------------------
// Rule 5: Baseline Read-Only & Informational Errand Tools (R0 - R1)
// Permitted unconditionally
// ------------------------------------------------------------------------------
permit (
    principal,
    action in [
        AgentCore::Action::"Errands___get_profile",
        AgentCore::Action::"Errands___list_billers",
        AgentCore::Action::"Errands___list_payees",
        AgentCore::Action::"Errands___list_meds",
        AgentCore::Action::"Errands___get_due_bills",
        AgentCore::Action::"Errands___find_slots",
        AgentCore::Action::"Errands___book_appointment",
        AgentCore::Action::"Errands___set_reminder",
        AgentCore::Action::"Errands___check_bill_status",
        AgentCore::Action::"Errands___list_saved_billers"
    ],
    resource
);

// ------------------------------------------------------------------------------
// Rule 6: Guardian Oversight and Shield Assessment Tools
// Permitted unconditionally (even during emergency freeze or scam alerts)
// so that emergency alerts, status queries, and risk analysis are never blocked.
// ------------------------------------------------------------------------------
permit (
    principal,
    action in [
        AgentCore::Action::"Guardian___request_approval",
        AgentCore::Action::"Guardian___get_approval_status",
        AgentCore::Action::"Guardian___notify_guardian",
        AgentCore::Action::"Guardian___get_guard_status",
        AgentCore::Action::"Guardian___request_guardian_approval",
        AgentCore::Action::"Guardian___check_approval_status",
        AgentCore::Action::"Guardian___send_guardian_alert",
        AgentCore::Action::"Guardian___get_household_rules",
        AgentCore::Action::"Shield___assess_risk",
        AgentCore::Action::"Shield___explain_scam",
        AgentCore::Action::"Shield___verify_payee_trust",
        AgentCore::Action::"Shield___evaluate_cooling_off"
    ],
    resource
);
