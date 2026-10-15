import json
import zlib
import pytest
from pipeline.collector import extract_raw_data_slice, validate_wire_crc


def test_extract_raw_data_slice_simple():
    line = b'{"session_id":"abc","sensor":1,"crc32":"12345678","data":[[1,2,3],[4,5,6]]}\n'
    slice_bytes = extract_raw_data_slice(line)
    assert slice_bytes == b"[[1,2,3],[4,5,6]]"


def test_crc_validation_valid():
    data = [[720, 1200, 1560], [708, 1180, 1534]]
    data_text = json.dumps(data, separators=(",", ":"))
    crc_val = zlib.crc32(data_text.encode("utf-8")) & 0xFFFFFFFF
    crc_hex = f"{crc_val:08x}"

    line = f'{{"session_id":"test","sensor":1,"crc32":"{crc_hex}","data":{data_text}}}\n'.encode("utf-8")
    assert validate_wire_crc(line, crc_hex) is True


def test_crc_validation_corrupt():
    data = [[720, 1200, 1560], [708, 1180, 1534]]
    data_text = json.dumps(data, separators=(",", ":"))
    crc_hex = "deadbeef"

    line = f'{{"session_id":"test","sensor":1,"crc32":"{crc_hex}","data":{data_text}}}\n'.encode("utf-8")
    assert validate_wire_crc(line, crc_hex) is False
