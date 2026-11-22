// ==============================================================================
// BETA Cedar Authorization Policies
// AWS Bedrock AgentCore Gateway Guardrails for Senior Citizen Protection
// ==============================================================================

// ------------------------------------------------------------------------------
// Policy 1: Absolute Emergency Freeze / Kill Switch
// If the household is marked as frozen, forbid all financial actions immediately.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.security_facts.frozen == true
};

// ------------------------------------------------------------------------------
// Policy 2: Coercion & Scam Guardrail
// Forbid all actions if suspicious urgency or scam indicators are flagged.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action,
    resource
)
when {
    context.security_facts.scam_flag == true
};

// ------------------------------------------------------------------------------
// Policy 3: Night Curfew (11:00 PM to 6:00 AM IST)
// Senior citizens are vulnerable to late-night panic scams.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.security_facts.hour_ist >= 23 || context.security_facts.hour_ist < 6
};

// ------------------------------------------------------------------------------
// Policy 4: Hourly Transaction Frequency Limit
// Prevent rapid drain of funds (burst limit: 2 payments per hour).
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.security_facts.payments_last_hour >= 2
};

// ------------------------------------------------------------------------------
// Policy 5: Unknown Payee / Merchant Whitelist Enforcement
// Financial transactions can only proceed to pre-registered billers or payees.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.security_facts.payee_known == false
};

// ------------------------------------------------------------------------------
// Policy 6: Absolute Ceiling
// No single transaction may exceed ₹25,000 even with approval.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.amount > 25000
};

// ------------------------------------------------------------------------------
// Policy 7: Transactions Exceeding Auto-Limit Require Valid Guardian Approval
// If amount > auto_limit (₹2,000), require approval_valid == true.
// ------------------------------------------------------------------------------
forbid (
    principal,
    action in [
        Action::"execute_payment",
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.amount > 2000 && context.security_facts.approval_valid == false
};

// ------------------------------------------------------------------------------
// Policy 8: Allow Standard Errands within Auto-Limit
// Permit Kamala to pay bills and reorder medicines under auto_limit (₹2,000)
// ------------------------------------------------------------------------------
permit (
    principal == User::"KAMALA",
    action in [
        Action::"pay_utility_bill",
        Action::"reorder_medicines",
        Action::"check_bill_status",
        Action::"list_saved_billers"
    ],
    resource
)
when {
    context.amount <= 2000 &&
    context.security_facts.frozen == false &&
    context.security_facts.scam_flag == false &&
    context.security_facts.payee_known == true
};

// ------------------------------------------------------------------------------
// Policy 9: Allow Authorized Errands with Valid Guardian Approval
// ------------------------------------------------------------------------------
permit (
    principal == User::"KAMALA",
    action in [
        Action::"pay_utility_bill",
        Action::"reorder_medicines"
    ],
    resource
)
when {
    context.amount <= 25000 &&
    context.security_facts.approval_valid == true &&
    context.security_facts.frozen == false &&
    context.security_facts.scam_flag == false
};

// ------------------------------------------------------------------------------
// Policy 10: Guardian Permissions
// Arjun (Guardian) can inspect, authorize, alert, and freeze household state.
// ------------------------------------------------------------------------------
permit (
    principal == Guardian::"ARJUN",
    action in [
        Action::"approve_transaction",
        Action::"reject_transaction",
        Action::"emergency_freeze",
        Action::"unfreeze",
        Action::"view_audit_trail",
        Action::"query_status"
    ],
    resource
);
