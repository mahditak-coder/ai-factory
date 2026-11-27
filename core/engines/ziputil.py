"""ziputil — ابزار مشترک ZIP برای خط تولید اندروید (موتور + اسکریپت CLI).

قواعد (همان zipalign-mini آزمون‌شدهٔ AF-02):
* بازساخت کامل ZIP با ترازبندی ۴ بایتی داده‌های STORED — برای targetSdk>=30
  نصب‌کنندهٔ اندروید می‌خواهد resources.arsc بدون فشرده‌سازی و تراز باشد.
* افزودن ورودی STORED (مثلاً classes.dex) به ZIP موجود، با بازسازی صحیح CD.
* بدون data descriptor؛ اندازه‌ها از Central Directory (منبع حقیقت).
"""
import os
import struct
import tempfile
import zlib
from pathlib import Path

LFH_SIG = 0x04034B50
CDH_SIG = 0x02014B50
EOCD_SIG = 0x06054B50


def parse_cd(data: bytes) -> list[dict]:
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("EOCD یافت نشد — فایل ZIP نیست")
    cd_count = struct.unpack_from("<H", data, eocd + 10)[0]
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    entries = []
    i = cd_off
    for _ in range(cd_count):
        if struct.unpack_from("<I", data, i)[0] != CDH_SIG:
            raise ValueError("CDH نامعتبر")
        (ver_made, ver_need, flags, method, mt, md, crc, csize, usize,
         nlen, elen, clen, disk, iattr, eattr, lfh) = struct.unpack_from(
            "<HHHHHHIIIHHHHHII", data, i + 4)
        name = data[i + 46:i + 46 + nlen]
        extra = data[i + 46 + nlen:i + 46 + nlen + elen]
        comment = data[i + 46 + nlen + elen:i + 46 + nlen + elen + clen]
        entries.append(dict(name=name, flags=flags, method=method, mt=mt, md=md,
                            crc=crc, csize=csize, usize=usize, extra=extra,
                            comment=comment, lfh=lfh, cdh=data[i:i + 46 + nlen + elen + clen],
                            ver_need=ver_need, ver_made=ver_made))
        i += 46 + nlen + elen + clen
    return entries


def _local_data(data: bytes, e: dict) -> bytes:
    lfh = e["lfh"]
    if struct.unpack_from("<I", data, lfh)[0] != LFH_SIG:
        raise ValueError("LFH نامعتبر: " + repr(e["name"]))
    nlen, elen = struct.unpack_from("<HH", data, lfh + 26)
    start = lfh + 30 + nlen + elen
    return data[start:start + e["csize"]]


def _assemble(entries: list[dict], data: bytes, align: int = 4) -> bytes:
    """بازساخت ZIP از فهرست ورودی‌ها با ترازبندی STORED. data = منبع بایت‌های لوکال."""
    out = bytearray()
    new_offsets = []
    for e in entries:
        blob = _local_data(data, e) if e.get("cdh") is not None else e["blob"]
        nlen = len(e["name"])
        flags = e["flags"] & ~0x8
        hdr = struct.pack("<IHHHHHIIIHH", LFH_SIG, e.get("ver_need", 0x14), flags,
                          e["method"], e["mt"], e["md"], e["crc"],
                          e["csize"], e["usize"], nlen, 0)
        off = len(out)
        if e["method"] == 0:
            need = (align - ((off + 30 + nlen) % align)) % align
            out += b"\x00" * need
            off = len(out)
        out += hdr + e["name"] + blob
        new_offsets.append((e, off))
    cd_start = len(out)
    for e, off in new_offsets:
        if e.get("cdh") is not None:
            cdh = bytearray(e["cdh"])
        else:
            cdh = bytearray(struct.pack(
                "<IHHHHHHIIIHHHHHII", CDH_SIG, e.get("ver_made", 0),
                e.get("ver_need", 0x14), 0, e["method"], e["mt"], e["md"],
                e["crc"], e["csize"], e["usize"], len(e["name"]), 0, 0, 0, 0, off, 0))
            cdh += e["name"]
        struct.pack_into("<H", cdh, 8, struct.unpack_from("<H", cdh, 8)[0] & ~0x8)
        struct.pack_into("<I", cdh, 42, off)
        out += cdh
    cd_end = len(out)
    out += struct.pack("<IHHHHIIH", EOCD_SIG, 0, 0, len(entries), len(entries),
                       cd_end - cd_start, cd_start, 0)
    return bytes(out)


def rebuild_file(apk_path: Path, align: int = 4) -> None:
    """ترازبندی مجدد یک فایل ZIP/APK روی دیسک (اتمی با tmp)."""
    p = Path(apk_path)
    data = p.read_bytes()
    out = _assemble(parse_cd(data), data, align)
    _atomic_write(p, out)


def append_stored(apk_path: Path, name: str, blob: bytes, align: int = 4) -> None:
    """افزودن ورودی STORED (مثلاً classes.dex) + ترازبندی نهایی، اتمی روی tmp."""
    p = Path(apk_path)
    data = p.read_bytes()
    entries = parse_cd(data)
    entries.append(dict(name=name.encode(), flags=0, method=0, mt=0, md=0,
                        crc=zlib.crc32(blob) & 0xFFFFFFFF, csize=len(blob),
                        usize=len(blob), extra=b"", comment=b"", lfh=0, cdh=None,
                        blob=blob, ver_need=0x14, ver_made=0))
    out = _assemble(entries, data, align)
    _atomic_write(p, out)


def _atomic_write(p: Path, out: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        os.write(fd, out)
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
