BASE = ["view_account", "view_transaction", "view_beneficiary", "view_bankcard"]
ROLES = {
    "Support": BASE + ["view_customers", "manage_customers"],
    "Compliance": BASE + ["view_kycapplication", "change_kycapplication", "view_verifiedidentity",
                          "review_kycapplication", "view_customers"],
    "Operations": BASE + ["approve_cardorder", "view_cardorder", "view_operations", "view_customers",
                          "view_creditaccount"],
    "Credit officer": BASE + ["assess_creditapplication", "view_creditapplication", "view_creditaccount", "view_customers"],
    "Credit approver": BASE + ["approve_creditapplication", "view_creditapplication", "view_creditaccount", "view_customers"],
    "Teller": ["record_cash", "pay_cashout", "view_cashrequest", "view_cashout"],
    "Cash approver": ["approve_cash", "view_cashrequest", "view_cashout"],
    "Auditor": ["view_audit", "view_customers", "view_operations"],
}