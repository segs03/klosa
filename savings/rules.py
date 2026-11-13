from decimal import Decimal

FLEX_RATE = Decimal("5.00")          # percent a year, placeholder
LOCKED_TERMS = {                      # days: percent a year, placeholders
    30: Decimal("7.00"),
    90: Decimal("9.00"),
    180: Decimal("11.00"),
    365: Decimal("13.00"),
}
MIN_FLEX = Decimal("100")
MIN_LOCKED = Decimal("1000")
MAX_ACTIVE_PLANS = 10