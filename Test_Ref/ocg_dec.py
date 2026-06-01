import struct
from typing import Dict, List, Tuple, Any, BinaryIO

###############################################################################
# 1. Configuration: message types, header, and field schemas
###############################################################################

# Example: message type codes (you must align with the spec)
MSG_TYPE_NEW_ORDER = 0x01
MSG_TYPE_CANCEL    = 0x02
MSG_TYPE_EXEC_RPT  = 0x03
# ... add all message types from the spec

# Header layout (example – adjust to spec)
# Suppose header is:
#   uint16 msg_length
#   uint8  msg_type
#   uint32 seq_num
#   uint64 sending_time
HEADER_FORMAT = ">HBIQ"  # big-endian: uint16, uint8, uint32, uint64
HEADER_SIZE   = struct.calcsize(HEADER_FORMAT)

# Map header fields to tags (FIX-like tags as example)
HEADER_TAGS = ["MsgLength", "MsgType", "SeqNum", "SendingTime"]

# Field type decoders
def decode_uint8(data: bytes, offset: int) -> Tuple[int, int]:
    return struct.unpack_from(">B", data, offset)[0], offset + 1

def decode_uint16(data: bytes, offset: int) -> Tuple[int, int]:
    return struct.unpack_from(">H", data, offset)[0], offset + 2

def decode_uint32(data: bytes, offset: int) -> Tuple[int, int]:
    return struct.unpack_from(">I", data, offset)[0], offset + 4

def decode_uint64(data: bytes, offset: int) -> Tuple[int, int]:
    return struct.unpack_from(">Q", data, offset)[0], offset + 8

def decode_int32(data: bytes, offset: int) -> Tuple[int, int]:
    return struct.unpack_from(">i", data, offset)[0], offset + 4

def decode_char(data: bytes, offset: int) -> Tuple[str, int]:
    return data[offset:offset+1].decode("ascii"), offset + 1

def decode_string(data: bytes, offset: int, length: int) -> Tuple[str, int]:
    raw = data[offset:offset+length]
    return raw.rstrip(b"\x00").decode("ascii"), offset + length

# A field definition: (tag, decoder, extra_args)
# decoder signature: (data, offset, *extra_args) -> (value, new_offset)
FieldDef = Tuple[str, Any, Tuple[Any, ...]]

# Example schemas – YOU MUST FILL THESE FROM THE SPEC
# Each message type has:
#   "presence_bytes": number of bytes in presence map
#   "fields": list of FieldDef in the order of bits (bit 0 = first field, etc.)
MESSAGE_SCHEMAS: Dict[int, Dict[str, Any]] = {
    MSG_TYPE_NEW_ORDER: {
        "presence_bytes": 2,  # e.g. 16 bits of presence
        "fields": [
            # bit 0
            ("ClOrdID", decode_string, (20,)),   # 20-byte ASCII
            # bit 1
            ("Symbol", decode_string, (12,)),
            # bit 2
            ("Side", decode_char, ()),
            # bit 3
            ("OrderQty", decode_uint32, ()),
            # bit 4
            ("Price", decode_int32, ()),         # e.g. price * 10000
            # bit 5
            ("TimeInForce", decode_char, ()),
            # bit 6
            ("Account", decode_string, (10,)),
            # bit 7
            ("OrdType", decode_char, ()),
            # ... continue according to spec
        ],
    },
    MSG_TYPE_CANCEL: {
        "presence_bytes": 1,
        "fields": [
            ("OrigClOrdID", decode_string, (20,)),
            ("ClOrdID", decode_string, (20,)),
            ("Symbol", decode_string, (12,)),
            ("Side", decode_char, ()),
            ("OrderQty", decode_uint32, ()),
            # ...
        ],
    },
    MSG_TYPE_EXEC_RPT: {
        "presence_bytes": 3,
        "fields": [
            ("OrderID", decode_string, (20,)),
            ("ExecID", decode_string, (20,)),
            ("ExecType", decode_char, ()),
            ("OrdStatus", decode_char, ()),
            ("LeavesQty", decode_uint32, ()),
            ("CumQty", decode_uint32, ()),
            ("AvgPx", decode_int32, ()),
            # ...
        ],
    },
    # ... add all message types
}

###############################################################################
# 2. Decoder class
###############################################################################

class OCGDecoder:
    def __init__(self, message_schemas: Dict[int, Dict[str, Any]]):
        self.schemas = message_schemas

    def decode_message(self, data: bytes, offset: int = 0) -> Tuple[Dict[str, Any], int]:
        """
        Decode a single OCG message from data[offset:].
        Returns (fields_dict, new_offset).
        """
        if len(data) - offset < HEADER_SIZE:
            raise ValueError("Not enough data for header")

        # 1) Decode header
        header_values = struct.unpack_from(HEADER_FORMAT, data, offset)
        msg_length = header_values[0]
        msg_type   = header_values[1]

        fields: Dict[str, Any] = {}
        for tag, value in zip(HEADER_TAGS, header_values):
            fields[tag] = value

        # Move offset past header
        cur = offset + HEADER_SIZE

        # 2) Look up schema
        schema = self.schemas.get(msg_type)
        if schema is None:
            # Unknown message type – skip body
            cur = offset + msg_length
            return fields, cur

        presence_bytes = schema["presence_bytes"]
        field_defs: List[FieldDef] = schema["fields"]

        # 3) Read presence map
        if len(data) - cur < presence_bytes:
            raise ValueError("Not enough data for presence map")

        presence_raw = data[cur:cur + presence_bytes]
        cur += presence_bytes

        # Convert presence bytes to bit list (LSB = bit 0)
        presence_bits: List[int] = []
        for i, b in enumerate(presence_raw):
            for bit in range(8):
                presence_bits.append((b >> bit) & 0x01)

        # 4) Decode fields according to presence bits
        for bit_index, field_def in enumerate(field_defs):
            if bit_index >= len(presence_bits):
                break  # no more bits

            present = presence_bits[bit_index]
            if not present:
                continue

            tag, decoder, extra = field_def
            if extra:
                value, cur = decoder(data, cur, *extra)
            else:
                value, cur = decoder(data, cur)
            fields[tag] = value

        # 5) Ensure we don't read beyond msg_length
        end = offset + msg_length
        if cur > end:
            raise ValueError("Decoded beyond message length")
        # If there is padding or reserved bytes, skip them
        cur = end

        return fields, cur

    def decode_stream(self, data: bytes) -> List[Dict[str, Any]]:
        """
        Decode all messages from a byte buffer.
        """
        messages = []
        offset = 0
        while offset < len(data):
            fields, offset = self.decode_message(data, offset)
            messages.append(fields)
        return messages

###############################################################################
# 3. Tag=value formatting
###############################################################################

def format_tag_value(fields: Dict[str, Any]) -> str:
    """
    Convert a dict of fields into a FIX-like tag=value string.
    Here we just use the field names as 'tags'.
    """
    parts = []
    for tag, value in fields.items():
        parts.append(f"{tag}={value}")
    return "|".join(parts)  # use '|' as separator (or SOH in real FIX)

###############################################################################
# 4. Example usage
###############################################################################

def decode_ocg_file(path: str):
    with open(path, "rb") as f:
        data = f.read()

    decoder = OCGDecoder(MESSAGE_SCHEMAS)
    messages = decoder.decode_stream(data)

    for msg in messages:
        print(format_tag_value(msg))


if __name__ == "__main__":
    # Example: decode a capture file
    # Replace 'ocg_capture.bin' with your actual binary capture
    decode_ocg_file("ocg_capture.bin")
