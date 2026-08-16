"""Arknights custom LZ4 variant (flagged as LZHAM by UnityFS).

Arknights CN (Unity 2021.3.39f1+, client 2.5.04) stores asset-bundle blocks
in a scrambled LZ4 format: the token nibbles are swapped and match offsets
are byte-swapped.  ``decompress_ak_lz4`` rewrites the bytes back into
standard LZ4 and decompresses them, so UnityPy can parse the bundle.
"""

from __future__ import annotations

from collections.abc import Callable

import lz4.block

try:
    from UnityPy.enums import CompressionFlags
    from UnityPy.helpers import CompressionHelper
except ImportError:  # pragma: no cover - optional dependency
    CompressionFlags = None  # type: ignore[assignment]
    CompressionHelper = None  # type: ignore[assignment]


def _read_extended_length(data: bytearray, pos: int) -> tuple[int, int]:
    """Read an LZ4 255-chain extended length, returning (length, next_pos)."""
    length = 0
    while True:
        byte = data[pos]
        pos += 1
        length += byte
        if byte != 255:
            break
    return length, pos


def unscramble_ak_lz4(
    compressed_data: bytes | bytearray, uncompressed_size: int
) -> bytes:
    """Rewrite Arknights scrambled-LZ4 bytes back into standard LZ4 blocks.

    The transformation is an involution for the token/offset swaps, so it can
    also be used to produce scrambled test fixtures from normal LZ4 data.
    """
    data = bytearray(compressed_data)
    ip = 0
    op = 0
    while True:
        token = data[ip]
        literal_length = token & 0x0F
        match_length = (token & 0xF0) >> 4
        data[ip] = (literal_length << 4) | match_length
        ip += 1
        if literal_length == 15:
            extension, ip = _read_extended_length(data, ip)
            literal_length += extension
        op += literal_length
        ip += literal_length
        if uncompressed_size - op < 12:  # MFLIMIT: rest of block is literals
            break
        # Arknights stores the 2-byte little-endian offset byte-swapped.
        offset = (data[ip] << 8) | data[ip + 1]
        data[ip] = offset & 0xFF
        data[ip + 1] = (offset >> 8) & 0xFF
        ip += 2
        if match_length == 15:
            extension, ip = _read_extended_length(data, ip)
            match_length += extension
        match_length += 4  # LZ4 MINMATCH
        op += match_length
    return bytes(data)


def decompress_ak_lz4(
    compressed_data: bytes | bytearray, uncompressed_size: int
) -> bytes:
    """Decompress an Arknights custom-compressed UnityFS block."""
    fixed = unscramble_ak_lz4(compressed_data, uncompressed_size)
    return lz4.block.decompress(fixed, uncompressed_size)


def install_unitypy_ak_patch() -> None:
    """Route UnityPy's LZHAM (block flags 4/5) decompression to our handler."""
    if CompressionHelper is None or CompressionFlags is None:
        return
    patch: Callable[[bytes | bytearray, int], bytes] = decompress_ak_lz4
    CompressionHelper.decompress_lzham = patch  # type: ignore[assignment]
    CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = patch
    # Newer UnityPy enums name flag 5 COMPRESSION_5; cover it either way.
    CompressionHelper.DECOMPRESSION_MAP[5] = patch
