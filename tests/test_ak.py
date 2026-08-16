import lz4.block
import pytest

from arknightsavatar.unpack.ak import (
    _read_extended_length,
    decompress_ak_lz4,
    install_unitypy_ak_patch,
    unscramble_ak_lz4,
)


def _scramble(payload: bytes, uncompressed_size: int) -> bytes:
    """Produce an Arknights-style scrambled LZ4 block from normal LZ4 data."""
    compressed = lz4.block.compress(payload, store_size=False)
    data = bytearray(compressed)
    ip = 0
    op = 0
    while True:
        token = data[ip]
        literal_length = (token >> 4) & 0x0F  # standard LZ4: high nibble
        match_length = token & 0x0F  # standard LZ4: low nibble
        data[ip] = (match_length << 4) | literal_length  # swap into AK form
        ip += 1
        if literal_length == 15:
            extension, ip = _read_extended_length(data, ip)
            literal_length += extension
        op += literal_length
        ip += literal_length
        if uncompressed_size - op < 12:
            break
        offset = data[ip] | (data[ip + 1] << 8)  # standard little-endian
        data[ip] = (offset >> 8) & 0xFF
        data[ip + 1] = offset & 0xFF
        ip += 2
        if match_length == 15:
            extension, ip = _read_extended_length(data, ip)
            match_length += extension
        match_length += 4
        op += match_length
    return bytes(data)


@pytest.mark.parametrize(
    "payload",
    [
        b"hello world hello world hello world " * 20,
        bytes(range(256)) * 8,
        (b"Arknights LZ4 variant test - " + "明日方舟".encode()) * 30 + b"\x00" * 64,
    ],
)
def test_decompress_ak_lz4_roundtrip(payload: bytes):
    scrambled = _scramble(payload, len(payload))
    assert decompress_ak_lz4(scrambled, len(payload)) == payload


def test_unscramble_is_involution():
    payload = b"roundtrip through the scrambler " * 40
    compressed = lz4.block.compress(payload, store_size=False)
    scrambled = _scramble(payload, len(payload))
    assert compressed != scrambled
    assert unscramble_ak_lz4(scrambled, len(payload)) == compressed


def test_install_unitypy_ak_patch_wires_lzham():
    install_unitypy_ak_patch()
    from UnityPy.enums import CompressionFlags
    from UnityPy.helpers import CompressionHelper

    assert (
        CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] is decompress_ak_lz4
    )
    assert CompressionHelper.DECOMPRESSION_MAP[5] is decompress_ak_lz4
