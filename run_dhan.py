from strategy.levels import build_order_levels


ANCHOR_PRICE = 25000.0
TRIGGER_DIST = 20.0
TP_DIST = 30.0
SL_DIST = 40.0


if __name__ == "__main__":
    levels = build_order_levels(
        anchor_price=ANCHOR_PRICE,
        trigger_dist=TRIGGER_DIST,
        tp_dist=TP_DIST,
        sl_dist=SL_DIST,
    )

    print("=== AUREON ARVION DHAN AGENT ===")
    print(f"Anchor: {levels.anchor}")
    print(f"LONG ENTRY:  {levels.long_entry}")
    print(f"LONG SL:     {levels.long_sl}")
    print(f"LONG TP:     {levels.long_tp}")
    print(f"SHORT ENTRY: {levels.short_entry}")
    print(f"SHORT SL:    {levels.short_sl}")
    print(f"SHORT TP:    {levels.short_tp}")
