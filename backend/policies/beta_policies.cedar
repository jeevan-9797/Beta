// ==============================================================================
// BETA Cedar Authorization Policies
// AWS Bedrock AgentCore Gateway Guardrails for Senior Citizen Protection
// Target Action: AgentCore::Action::"Errands___pay_bill"
// Target Resource: AgentCore::Gateway::"<GATEWAY_ARN>"
// ==============================================================================

// ------------------------------------------------------------------------------
// Rule 1: Emergency Freeze Kill Switch
// Forbid all actions when household frozen flag is True
// ------------------------------------------------------------------------------
forbid (
    principal,
    action,
    resource
)
when {
    context has input && context.input has frozen && context.input.frozen
};

// ------------------------------------------------------------------------------
// Rule 2: Coercion & Scam Detection Guardrail
// Forbid all actions when scam_flag is True
// ------------------------------------------------------------------------------
forbid (
    principal,
    action,
    resource
)
when {
    context has input && context.input has scam_flag && context.input.scam_flag
};

// ------------------------------------------------------------------------------
// Rule 3: R2 Auto-Pay for Errands___pay_bill
// Allows automated utility and errand payment under ₹2,000 to registered payees
// during daytime hours (06:00 to 23:00 IST), within hourly rate limit (<2),
// and with a valid unconsumed voice assessment.
// ------------------------------------------------------------------------------
permit (
    principal,
    action == AgentCore::Action::"Errands___pay_bill",
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
// Rule 4: R3 Approved Pay for Errands___pay_bill
// Allows payments up to ₹25,000 if guardian approval is valid & cooling-off elapsed
// ------------------------------------------------------------------------------
permit (
    principal,
    action == AgentCore::Action::"Errands___pay_bill",
    resource
)
when {
    context.input.approval_valid &&
    context.input.assessment_valid &&
    context.input.amount <= 25000
};

// ------------------------------------------------------------------------------
// Rule 5: Baseline Read-Only & Informational Tools (R0 - R1)
// Permitted unconditionally when not frozen
// ------------------------------------------------------------------------------
permit (
    principal,
    action in [
        AgentCore::Action::"Errands___check_bill_status",
        AgentCore::Action::"Errands___list_saved_billers",
        AgentCore::Action::"Guardian___check_approval_status",
        AgentCore::Action::"Guardian___get_household_rules",
        AgentCore::Action::"Shield___verify_payee_trust",
        AgentCore::Action::"Shield___evaluate_cooling_off"
    ],
    resource
);
