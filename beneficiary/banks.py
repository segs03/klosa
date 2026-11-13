from django.conf import settings

# Standard 3-digit Nigerian bank codes. Check each one against the current
# CBN/NIBSS bank list before you go live, because codes and names do change.
BANKS = {
    "Access Bank": "044",
    "Citibank Nigeria": "023",
    "Ecobank Nigeria": "050",
    "Fidelity Bank": "070",
    "First Bank": "011",
    "FCMB": "214",
    "GTBank": "058",
    "Keystone Bank": "082",
    "Stanbic IBTC": "221",
    "Sterling Bank": "232",
    "UBA": "033",
    "Union Bank": "032",
    "Unity Bank": "215",
    "Wema Bank": "035",
    "Zenith Bank": "057",
}
KLOSA = "Klosa Bank"


def bank_choices():
    names = sorted(list(BANKS) + [KLOSA])
    return [("", "Select bank")] + [(n, n) for n in names]


def bank_code_for(name):
    if name == KLOSA:
        return settings.KLOSA_BANK_CODE
    return BANKS.get(name)