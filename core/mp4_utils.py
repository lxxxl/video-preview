import struct
import structlog

logger = structlog.get_logger(__name__)


def scan_boxes(data: bytes, offset: int = 0) -> list[tuple[bytes, int, int]]:
    boxes = []
    pos = offset
    while pos < len(data) - 8:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = data[pos + 4:pos + 8]
        if size < 8 or pos + size > len(data):
            break
        boxes.append((btype, pos + 8, size - 8))
        pos += size
    return boxes


def find_box(data: bytes, box_type: bytes, offset: int = 0) -> tuple[int, int] | None:
    for btype, content_start, content_size in scan_boxes(data, offset):
        if btype == box_type:
            return content_start, content_size
    return None


def parse_avcc(data: bytes) -> dict | None:
    if len(data) < 7:
        return None

    nalu_length_size = (data[4] & 0x03) + 1

    num_sps = data[5] & 0x1F
    pos = 6
    sps_list = []
    for _ in range(num_sps):
        if pos + 2 > len(data):
            return None
        sps_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if pos + sps_len > len(data):
            return None
        sps_list.append(data[pos:pos + sps_len])
        pos += sps_len

    if pos >= len(data):
        return None
    num_pps = data[pos]
    pos += 1
    pps_list = []
    for _ in range(num_pps):
        if pos + 2 > len(data):
            return None
        pps_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if pos + pps_len > len(data):
            return None
        pps_list.append(data[pos:pos + pps_len])
        pos += pps_len

    return {
        "nalu_length_size": nalu_length_size,
        "sps": sps_list,
        "pps": pps_list,
    }


def extract_codec_config(head_data: bytes) -> dict | None:
    moov = find_box(head_data, b"moov")
    if moov is None:
        return None
    moov_start, moov_size = moov
    moov_data = head_data[moov_start:moov_start + moov_size]

    for trak_type, trak_start, trak_size in scan_boxes(moov_data):
        if trak_type != b"trak":
            continue
        trak_data = moov_data[trak_start:trak_start + trak_size]

        mdia = find_box(trak_data, b"mdia")
        if mdia is None:
            continue
        mdia_data = trak_data[mdia[0]:mdia[0] + mdia[1]]

        minf = find_box(mdia_data, b"minf")
        if minf is None:
            continue
        minf_data = mdia_data[minf[0]:minf[0] + minf[1]]

        stbl = find_box(minf_data, b"stbl")
        if stbl is None:
            continue
        stbl_data = minf_data[stbl[0]:stbl[0] + stbl[1]]

        stsd = find_box(stbl_data, b"stsd")
        if stsd is None:
            continue
        stsd_data = stbl_data[stsd[0]:stsd[0] + stsd[1]]

        if len(stsd_data) < 8:
            continue
        stsd_entries = stsd_data[8:]

        avc1 = find_box(stsd_entries, b"avc1")
        if avc1 is not None:
            avc1_data = stsd_entries[avc1[0]:avc1[0] + avc1[1]]
            # avc1: 8 bytes SampleEntry + 70 bytes VisualSampleEntry = 78 bytes before child boxes
            if len(avc1_data) > 78:
                avcc = find_box(avc1_data, b"avcC", 78)
                if avcc is not None:
                    config = parse_avcc(avc1_data[avcc[0]:avcc[0] + avcc[1]])
                    if config:
                        config["codec"] = "h264"
                        return config

        hvc1 = find_box(stsd_entries, b"hvc1")
        hev1 = find_box(stsd_entries, b"hev1")
        if hvc1 is not None or hev1 is not None:
            return {"codec": "hevc", "nalu_length_size": 4, "sps": [], "pps": []}

    return None


def parse_stss(data: bytes) -> list[int]:
    """Parse sync sample (keyframe) table. Returns 0-based sample indices."""
    if len(data) < 8:
        return []
    entry_count = struct.unpack(">I", data[4:8])[0]
    indices = []
    for i in range(entry_count):
        off = 8 + i * 4
        if off + 4 > len(data):
            break
        indices.append(struct.unpack(">I", data[off:off + 4])[0] - 1)
    return indices


def parse_stsz(data: bytes) -> tuple[int, list[int]]:
    """Parse sample size table. Returns (default_size, [sizes])."""
    if len(data) < 12:
        return 0, []
    default_size = struct.unpack(">I", data[4:8])[0]
    count = struct.unpack(">I", data[8:12])[0]
    if default_size > 0:
        return default_size, [default_size] * count
    sizes = []
    for i in range(count):
        off = 12 + i * 4
        if off + 4 > len(data):
            break
        sizes.append(struct.unpack(">I", data[off:off + 4])[0])
    return 0, sizes


def parse_stco(data: bytes) -> list[int]:
    """Parse chunk offset table (stco = 32-bit offsets)."""
    if len(data) < 8:
        return []
    count = struct.unpack(">I", data[4:8])[0]
    offsets = []
    for i in range(count):
        off = 8 + i * 4
        if off + 4 > len(data):
            break
        offsets.append(struct.unpack(">I", data[off:off + 4])[0])
    return offsets


def parse_co64(data: bytes) -> list[int]:
    """Parse chunk offset table (co64 = 64-bit offsets)."""
    if len(data) < 8:
        return []
    count = struct.unpack(">I", data[4:8])[0]
    offsets = []
    for i in range(count):
        off = 8 + i * 8
        if off + 8 > len(data):
            break
        offsets.append(struct.unpack(">Q", data[off:off + 8])[0])
    return offsets


def parse_stsc(data: bytes) -> list[tuple[int, int, int]]:
    """Parse sample-to-chunk table. Returns [(first_chunk_1based, samples_per_chunk, desc_idx)]."""
    if len(data) < 8:
        return []
    count = struct.unpack(">I", data[4:8])[0]
    entries = []
    for i in range(count):
        off = 8 + i * 12
        if off + 12 > len(data):
            break
        fc, spc, sdi = struct.unpack(">III", data[off:off + 12])
        entries.append((fc, spc, sdi))
    return entries


def parse_stts(data: bytes) -> list[tuple[int, int]]:
    """Parse time-to-sample table. Returns [(count, delta)]."""
    if len(data) < 8:
        return []
    count = struct.unpack(">I", data[4:8])[0]
    entries = []
    for i in range(count):
        off = 8 + i * 8
        if off + 8 > len(data):
            break
        sc, sd = struct.unpack(">II", data[off:off + 8])
        entries.append((sc, sd))
    return entries


def sample_to_byte_offset(
    sample_idx: int,
    stco_offsets: list[int],
    stsc_entries: list[tuple[int, int, int]],
    stsz_sizes: list[int],
) -> int:
    """Given a 0-based sample index, compute its byte offset in the file."""
    chunk_idx = 0
    samples_before = 0

    expanded_chunks = []
    for i, (first_chunk, spc, _) in enumerate(stsc_entries):
        next_first = stsc_entries[i + 1][0] if i + 1 < len(stsc_entries) else len(stco_offsets) + 1
        for c in range(first_chunk - 1, next_first - 1):
            if c >= len(stco_offsets):
                break
            expanded_chunks.append((c, spc))

    for c_idx, spc in expanded_chunks:
        if samples_before + spc > sample_idx:
            offset = stco_offsets[c_idx]
            for s in range(samples_before, sample_idx):
                if s < len(stsz_sizes):
                    offset += stsz_sizes[s]
            return offset
        samples_before += spc

    return stco_offsets[-1] if stco_offsets else 0


def sample_to_time(sample_idx: int, stts_entries: list[tuple[int, int]], timescale: int) -> float:
    """Convert 0-based sample index to time in seconds."""
    total_ticks = 0
    remaining = sample_idx
    for count, delta in stts_entries:
        if remaining <= count:
            total_ticks += remaining * delta
            break
        total_ticks += count * delta
        remaining -= count
    return total_ticks / timescale if timescale else 0


def parse_mdhd(data: bytes) -> int:
    """Parse media header to get timescale."""
    if len(data) < 4:
        return 0
    version = data[0]
    if version == 0:
        return struct.unpack(">I", data[12:16])[0] if len(data) >= 16 else 0
    else:
        return struct.unpack(">I", data[20:24])[0] if len(data) >= 24 else 0


def get_keyframe_positions(head_data: bytes) -> list[tuple[float, int]]:
    """Parse moov to get (time_seconds, byte_offset) for each video keyframe."""
    moov = find_box(head_data, b"moov")
    if moov is None:
        return []
    moov_data = head_data[moov[0]:moov[0] + moov[1]]

    for _, trak_start, trak_size in scan_boxes(moov_data):
        trak_data = moov_data[trak_start:trak_start + trak_size]

        mdia = find_box(trak_data, b"mdia")
        if mdia is None:
            continue
        mdia_data = trak_data[mdia[0]:mdia[0] + mdia[1]]

        hdlr = find_box(mdia_data, b"hdlr")
        if hdlr is None:
            continue
        hdlr_data = mdia_data[hdlr[0]:hdlr[0] + hdlr[1]]
        if len(hdlr_data) < 12 or hdlr_data[8:12] != b"vide":
            continue

        mdhd = find_box(mdia_data, b"mdhd")
        timescale = parse_mdhd(mdia_data[mdhd[0]:mdhd[0] + mdhd[1]]) if mdhd else 30000

        minf = find_box(mdia_data, b"minf")
        if minf is None:
            continue
        minf_data = mdia_data[minf[0]:minf[0] + minf[1]]

        stbl = find_box(minf_data, b"stbl")
        if stbl is None:
            continue
        stbl_data = minf_data[stbl[0]:stbl[0] + stbl[1]]

        stss_box = find_box(stbl_data, b"stss")
        stts_box = find_box(stbl_data, b"stts")
        stsz_box = find_box(stbl_data, b"stsz")
        stsc_box = find_box(stbl_data, b"stsc")
        stco_box = find_box(stbl_data, b"stco")
        co64_box = find_box(stbl_data, b"co64")

        if stss_box is None or stts_box is None:
            continue

        sync_samples = parse_stss(stbl_data[stss_box[0]:stss_box[0] + stss_box[1]])
        stts_entries = parse_stts(stbl_data[stts_box[0]:stts_box[0] + stts_box[1]])

        if stco_box:
            chunk_offsets = parse_stco(stbl_data[stco_box[0]:stco_box[0] + stco_box[1]])
        elif co64_box:
            chunk_offsets = parse_co64(stbl_data[co64_box[0]:co64_box[0] + co64_box[1]])
        else:
            continue

        stsc_entries = parse_stsc(stbl_data[stsc_box[0]:stsc_box[0] + stsc_box[1]]) if stsc_box else []
        _, stsz_sizes = parse_stsz(stbl_data[stsz_box[0]:stsz_box[0] + stsz_box[1]]) if stsz_box else (0, [])

        result = []
        for idx in sync_samples:
            t = sample_to_time(idx, stts_entries, timescale)
            byte_off = sample_to_byte_offset(idx, chunk_offsets, stsc_entries, stsz_sizes)
            result.append((t, byte_off))

        return result

    return []


ANNEX_B_START_CODE = b"\x00\x00\x00\x01"


def avc_to_annexb(data: bytes, nalu_length_size: int, sps_list: list[bytes], pps_list: list[bytes]) -> bytes:
    parts = []

    for sps in sps_list:
        parts.append(ANNEX_B_START_CODE + sps)
    for pps in pps_list:
        parts.append(ANNEX_B_START_CODE + pps)

    pos = 0
    while pos + nalu_length_size <= len(data):
        if nalu_length_size == 4:
            nalu_len = struct.unpack(">I", data[pos:pos + 4])[0]
        elif nalu_length_size == 2:
            nalu_len = struct.unpack(">H", data[pos:pos + 2])[0]
        elif nalu_length_size == 1:
            nalu_len = data[pos]
        else:
            break

        pos += nalu_length_size

        if nalu_len == 0 or nalu_len > len(data) - pos:
            break

        parts.append(ANNEX_B_START_CODE + data[pos:pos + nalu_len])
        pos += nalu_len

    if not parts:
        return data

    return b"".join(parts)
