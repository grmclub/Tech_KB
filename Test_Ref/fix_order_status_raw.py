import socket
import ssl
import time

# --- FIX Protocol Constants ---
SOH = "\x01"  # Start of Header (Tag Delimiter, ASCII 0x01)

# Tag 39 Order Status Mappings
ORD_STATUS_MAP = {
    "0": "NEW (Active)",
    "1": "PARTIALLY FILLED (Active)",
    "2": "FILLED (Terminal)",
    "3": "DONE FOR DAY (Active)",
    "4": "CANCELED (Terminal)",
    "5": "REPLACED (Active)",
    "6": "PENDING CANCEL (Active)",
    "7": "STOPPED (Terminal)",
    "8": "REJECTED (Terminal)",
    "9": "SUSPENDED (Active)",
    "C": "EXPIRED (Terminal)",
}

TERMINAL_STATUSES = {"2", "4", "7", "8", "C"}


class RawFIXClient:

    def __init__(
        self, host, port, sender_comp_id, target_comp_id, use_ssl=False
    ):
        self.host = host
        self.port = port
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.use_ssl = use_ssl

        self.msg_seq_num = 1
        self.sock = None

    # --- Helper Methods ---

    def _get_timestamp(self):
        """Generates UTC timestamp required for Tag 52 (SendingTime)."""
        return time.strftime("%Y%m%d-%H:%M:%S.000", time.gmtime())

    def _calc_checksum(self, msg_str):
        """Calculates modulo 256 checksum for Tag 10."""
        total = sum(ord(c) for c in msg_str) % 256
        return f"{total:03d}"

    def build_fix_message(self, msg_type, body_tags):
        """Constructs a complete, valid FIX message with header, body, and checksum."""
        # 1. Standard Header (without Tag 8 and Tag 9)
        header_list = [
            (35, msg_type),
            (49, self.sender_comp_id),
            (56, self.target_comp_id),
            (34, self.msg_seq_num),
            (52, self._get_timestamp()),
        ]

        self.msg_seq_num += 1

        # Combine header and body tags
        all_tags = header_list + body_tags
        body_str = "".join(f"{tag}={val}{SOH}" for tag, val in all_tags)

        # 2. Add Length (Tag 9) & Protocol Version (Tag 8)
        body_length = len(body_str)
        prefix = f"8=FIX.4.4{SOH}9={body_length}{SOH}"

        # 3. Calculate Checksum (Tag 10) over entire message string so far
        full_msg_before_checksum = prefix + body_str
        checksum = self._calc_checksum(full_msg_before_checksum)

        # Final framed message
        final_msg = f"{full_msg_before_checksum}10={checksum}{SOH}"
        return final_msg.encode("latin-1")

    # --- Network Connection ---

    def connect(self):
        """Connects to the FIX acceptor via raw TCP socket (SSL optional)."""
        raw_sock = socket.create_connection((self.host, self.port), timeout=10)
        if self.use_ssl:
            context = ssl.create_default_context()
            self.sock = context.wrap_socket(
                raw_sock, server_hostname=self.host
            )
        else:
            self.sock = raw_sock
        print(f"[+] Connected to FIX Acceptor at {self.host}:{self.port}")

    def send(self, data):
        self.sock.sendall(data)

    def receive_messages(self, timeout=5):
        """Receives and parses incoming FIX raw tags."""
        self.sock.settimeout(timeout)
        try:
            raw_data = self.sock.recv(4096).decode("latin-1")
            if not raw_data:
                return []
            # FIX messages are SOH-delimited
            messages = raw_data.split(f"10=")
            parsed_msgs = []
            for msg in messages:
                if not msg.strip():
                    continue
                # Re-add tag 10 prefix stripped by split
                full_msg = ("10=" + msg) if not msg.startswith("8=") else msg
                tag_dict = {}
                for field in full_msg.split(SOH):
                    if "=" in field:
                        tag, val = field.split("=", 1)
                        tag_dict[tag] = val
                parsed_msgs.append(tag_dict)
            return parsed_msgs
        except socket.timeout:
            return []

    # --- FIX Workflows ---

    def logon(self, heartbeat_int=30):
        """Sends Logon message (MsgType A)."""
        logon_tags = [
            (98, 0),  # EncryptMethod (0 = None)
            (108, heartbeat_int),  # HeartBtInt
        ]
        msg = self.build_fix_message("A", logon_tags)
        print("[+] Sending Logon (MsgType A)...")
        self.send(msg)

    def request_order_status(self, cl_ord_id, order_id="NONE", symbol="AAPL"):
        """Sends OrderStatusRequest (MsgType H)."""
        req_id = f"STAT_REQ_{int(time.time())}"
        status_req_tags = [
            (11, req_id),  # ClOrdID for the status request
            (37, order_id),  # OrderID (Broker order ID)
            (41, cl_ord_id),  # OrigClOrdID
            (55, symbol),  # Symbol
            (54, 1),  # Side (1 = Buy)
        ]
        msg = self.build_fix_message("H", status_req_tags)
        print(f"[+] Sending OrderStatusRequest for OrigClOrdID: {cl_ord_id}...")
        self.send(msg)

    def close(self):
        if self.sock:
            self.sock.close()
            print("[-] Connection closed.")


# --- Execution Example ---

if __name__ == "__main__":
    # --- Configuration ---
    HOST = "127.0.0.1"
    PORT = 9823
    SENDER_COMP_ID = "CLIENT_APP"
    TARGET_COMP_ID = "BROKER_FIX"

    # Order details to check
    CL_ORD_ID_TO_CHECK = "ORD_98765"

    client = RawFIXClient(
        HOST, PORT, SENDER_COMP_ID, TARGET_COMP_ID, use_ssl=False
    )

    try:
        client.connect()

        # Step 1: Send Logon
        client.logon()

        # Wait for Logon Response (MsgType A)
        responses = client.receive_messages(timeout=3)
        for resp in responses:
            if resp.get("35") == "A":
                print("[+] Logon acknowledged by counterparty.")

        # Step 2: Send Order Status Request
        client.request_order_status(cl_ord_id=CL_ORD_ID_TO_CHECK)

        # Step 3: Listen for Execution Report (MsgType 8)
        print("[+] Waiting for Execution Report...")
        start_time = time.time()
        final_state_found = False

        while time.time() - start_time < 10:  # 10s wait loop
            responses = client.receive_messages(timeout=2)
            for resp in responses:
                msg_type = resp.get("35")

                # MsgType 8 = ExecutionReport
                if msg_type == "8":
                    ord_status = resp.get("39")  # Tag 39 = OrdStatus
                    cl_id = resp.get("11")

                    print("\n" + "=" * 50)
                    print(" [!] EXECUTION REPORT RECEIVED")
                    print(f"  -> Client Order ID : {cl_id}")
                    print(
                        f"  -> Tag 39 Status   : {ORD_STATUS_MAP.get(ord_status, ord_status)}"
                    )

                    if ord_status in TERMINAL_STATUSES:
                        print("  -> Final State?    : YES (Terminal State)")
                    else:
                        print(
                            "  -> Final State?    : NO (Order Still Active)"
                        )
                    print("=" * 50 + "\n")

                    final_state_found = True
                    break

            if final_state_found:
                break

    except Exception as e:
        print(f"[!] Error: {e}")
    finally:
        client.close()