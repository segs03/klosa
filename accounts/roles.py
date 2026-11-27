BASE = ["view_account", "view_transaction", "view_beneficiary", "view_bankcard"]
ROLES = {
    "Support": BASE + ["view_customers", "manage_customers"],
    "Compliance": BASE + ["view_kycapplication", "change_kycapplication", "view_verifiedidentity",
                          "review_kycapplication", "view_customers", "review_facecheck", "view_facecheck"],
    "Operations": BASE + ["approve_cardorder", "view_cardorder", "view_operations", "view_customers",
                          "view_creditaccount"],
    "Credit officer": BASE + ["assess_creditapplication", "view_creditapplication", "view_creditaccount", "view_customers", "propose_creditsettlement", "view_creditsettlement"],
    "Credit approver": BASE + ["approve_creditapplication", "view_creditapplication", "view_creditaccount", "view_customers", "approve_creditsettlement", "view_creditsettlement"],
    "Teller": ["record_cash", "pay_cashout", "view_cashrequest", "view_cashout"],
    "Cash approver": ["approve_cash", "view_cashrequest", "view_cashout"],
    "Auditor": ["view_audit", "view_customers", "view_operations"],
        "Finance": ["view_fundingperiod", "review_fundingperiod", "add_capitalcontribution", "view_capitalcontribution",
                "view_investorallocation", "propose_investorallocation", "add_investorcapitalentry", "add_fundingperiod", "view_investor", "add_investor", "view_investoragreement", "add_investoragreement"],
    "Finance approver": ["verify_capitalcontribution", "approve_investorallocation", "close_fundingperiod",
                         "view_capitalcontribution", "view_investorallocation", "view_fundingperiod", "confirm_contributionintent", "view_contributionintent", "add_investorcapitalentry"],
    "Finance head": ["reopen_fundingperiod", "view_fundingperiod"],
}