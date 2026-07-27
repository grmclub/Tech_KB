import sys

# ASCII 0x01 is the standard FIX SOH delimiter
SOH = "\x01"

# Tag 39 (OrdStatus) Meanings & Terminal State Map
ORD_STATUS_MAP = {
    "0": "NEW",
    "1": "PARTIALLY FILLED",
    "2": "FILLED",
    "3": "DONE FOR DAY",
    "4": "CANCELED",
    "5": "REPLACED",
    "6": "PENDING CANCEL",
    "7": "STOPPED",
    "8": "REJECTED",
    "9": "SUSPENDED",
    "C": "EXPIRED",
}

# Terminal states (Order cannot change once reaching these)
TERMINAL_STATUSES = {"2", "4", "7", "8", "C"}


def parse_raw_fix_message(raw_msg):
    """
    Parses a raw FIX string into a dictionary of Tag -> Value.
    Handles | or \x01 delimited messages.
    """
    # Replace pipe '|' with SOH if pipes were used in logs
    normalized_msg = raw_msg.strip().replace("|", SOH)

    tag_dict = {}
    fields = normalized_msg.split(SOH)

    for field in fields:
        if "=" in field:
            tag, val = field.split("=", 1)
            tag_dict[tag.strip()] = val.strip()

    return tag_dict


def track_final_order_statuses(fix_messages):
    """
    Processes a list of raw FIX messages and returns the final known status
    for each Client Order ID (ClOrdID).
    """
    # Map of ClOrdID -> Order Details Dictionary
    orders = {}

    for raw_msg in fix_messages:
        if not raw_msg.strip():
            continue

        tags = parse_raw_fix_message(raw_msg)

        # We are specifically interested in ExecutionReports (MsgType 35=8)
        # or OrderCancelReject (MsgType 35=9)
        msg_type = tags.get("35")

        if msg_type == "8":  # ExecutionReport
            cl_ord_id = tags.get("11")  # Tag 11 = ClOrdID
            orig_cl_ord_id = tags.get("41")  # Tag 41 = OrigClOrdID
            ord_status = tags.get("39")  # Tag 39 = OrdStatus
            order_id = tags.get("37", "N/A")  # Tag 37 = Exchange OrderID
            symbol = tags.get("55", "N/A")  # Tag 55 = Symbol
            exec_id = tags.get("17", "N/A")  # Tag 17 = ExecID
            cum_qty = tags.get("14", "0")  # Tag 14 = CumQty

            # Resolve target ID (use OrigClOrdID if present for cancels/replaces)
            target_id = cl_ord_id or orig_cl_ord_id

            if target_id and ord_status:
                status_desc = ORD_STATUS_MAP.get(
                    ord_status, f"UNKNOWN ({ord_status})"
                )
                is_terminal = ord_status in TERMINAL_STATUSES

                # Update the order state history
                orders[target_id] = {
                    "cl_ord_id": target_id,
                    "order_id": order_id,
                    "symbol": symbol,
                    "status_code": ord_status,
                    "status_desc": status_desc,
                    "is_terminal": is_terminal,
                    "cum_qty": cum_qty,
                    "last_exec_id": exec_id,
                }

    return orders


def print_summary(orders):
    """Prints a clean, formatted table of the final order states."""
    print("\n" + "=" * 80)
    print(
        f"{'ClOrdID':<15} | {'Symbol':<8} | {'OrderID':<15} | {'Final Status':<20} | {'Terminal?'}"
    )
    print("=" * 80)

    for cl_ord_id, details in orders.items():
        status_str = f"{details['status_code']} ({details['status_desc']})"
        terminal_str = "YES" if details["is_terminal"] else "NO (Active)"

        print(
            f"{details['cl_ord_id']:<15} | "
            f"{details['symbol']:<8} | "
            f"{details['order_id']:<15} | "
            f"{status_str:<20} | "
            f"{terminal_str}"
        )

    print("=" * 80 + "\n")


# --- Example Execution & Test Run ---

if __name__ == "__main__":
    # Sample raw FIX log entries using standard pipe '|' formatting
    sample_raw_fix_logs = [
        # Order 1: New -> Partially Filled -> Filled
        "8=FIX.4.4|9=120|35=8|49=BROKER|56=CLIENT|11=ORD_001|37=EX_101|55=AAPL|39=0|150=0|10=050|",
        "8=FIX.4.4|9=130|35=8|49=BROKER|56=CLIENT|11=ORD_001|37=EX_101|55=AAPL|39=1|150=1|14=50|10=060|",
        "8=FIX.4.4|9=130|35=8|49=BROKER|56=CLIENT|11=ORD_001|37=EX_101|55=AAPL|39=2|150=2|14=100|10=070|",
        # Order 2: New -> Canceled
        "8=FIX.4.4|9=120|35=8|49=BROKER|56=CLIENT|11=ORD_002|37=EX_102|55=MSFT|39=0|150=0|10=050|",
        "8=FIX.4.4|9=125|35=8|49=BROKER|56=CLIENT|11=ORD_002|41=ORD_002|37=EX_102|55=MSFT|39=4|150=4|10=080|",
        # Order 3: Rejected immediately
        "8=FIX.4.4|9=115|35=8|49=BROKER|56=CLIENT|11=ORD_003|37=NONE|55=GOOGL|39=8|150=8|58=Invalid Price|10=090|",
        # Order 4: Still Active (Partially Filled)
        "8=FIX.4.4|9=130|35=8|49=BROKER|56=CLIENT|11=ORD_004|37=EX_104|55=TSLA|39=1|150=1|14=20|10=065|",
    ]

    print("[+] Processing raw FIX messages...")
    final_order_states = track_final_order_statuses(sample_raw_fix_logs)
    print_summary(final_order_states)