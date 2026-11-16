from decimal import Decimal

# Placeholders. Check them with your provider and your regulator.
DEPOSIT_MIN = Decimal("100")
DEPOSIT_MAX = Decimal("1000000")
WITHDRAW_MIN = Decimal("100")
WITHDRAW_MAX = Decimal("5000000")      # the tier limits below this are what actually bind
WITHDRAW_FEE = Decimal("0.00")