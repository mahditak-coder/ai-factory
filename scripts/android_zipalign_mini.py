#!/usr/bin/env python3
"""zipalign mini — بازساخت کامل ZIP با ترازبندی ۴ بایتی داده‌های STORED.
چرا: zipalign در مخازن Termux نیست؛ برای targetSdk>=30 نصب‌کنندهٔ اندروید
خواستار resources.arsc بدون فشرده‌سازی و تراز ۴ بایتی است.
راستی‌آزمایی: unzip -v خروجی (ستون offsetها) یا apksigner verify.
"""
import struct, sys

def parse_cd(data):
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0: raise SystemExit("no EOCD")
    cd_count = struct.unpack_from("<H", data, eocd + 10)[0]
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    entries = []
    i = cd_off
    for _ in range(cd_count):
        if struct.unpack_from("<I", data, i)[0] != 0x02014b50: raise SystemExit("bad CDH")
        (vmade, vneed, flags, method, mt, md, crc, csize, usize,
         nlen, elen, clen, disk, iattr, eattr, lfh) = struct.unpack_from("<HHHHHHIIIHHHHHII", data, i+4)
        name = data[i+46:i+46+nlen]
        extra = data[i+46+nlen:i+46+nlen+elen]
        comment = data[i+46+nlen+elen:i+46+nlen+elen+clen]
        entries.append(dict(name=name, flags=flags, method=method, mt=mt, md=md,
                            crc=crc, csize=csize, usize=usize, extra=extra,
                            comment=comment, lfh=lfh, cdh=data[i:i+46+nlen+elen+clen]))
        i += 46 + nlen + elen + clen
    return entries

def local_data(data, e):
    lfh = e["lfh"]
    if struct.unpack_from("<I", data, lfh)[0] != 0x04034b50: raise SystemExit("bad LFH " + repr(e["name"]))
    nlen, elen = struct.unpack_from("<HH", data, lfh + 26)
    dstart = lfh + 30 + nlen + elen
    return data[dstart:dstart + e["csize"]]

def main(src, dst, align=4):
    data = open(src, "rb").read()
    entries = parse_cd(data)
    out = bytearray()
    new_entries = []
    for e in entries:
        blob = local_data(data, e)
        nlen = len(e["name"])
        # local header با اندازه‌های واقعی (بدون data descriptor)
        ver_need = struct.unpack_from("<H", e["cdh"], 6)[0]
        flags = e["flags"] & ~0x8
        hdr = struct.pack("<IHHHHHIIIHH", 0x04034b50, ver_need, flags,
                          e["method"], e["mt"], e["md"], e["crc"], e["csize"], e["usize"], nlen, 0)
        off = len(out)
        if e["method"] == 0:  # STORED → تراز داده
            need = (align - ((off + 30 + nlen) % align)) % align
            out += b"\x00" * need
            off = len(out)
        out += hdr + e["name"] + blob
        new_entries.append((e, off))
    cd_start = len(out)
    for e, off in new_entries:
        cdh = bytearray(e["cdh"])
        struct.pack_into("<H", cdh, 8, (e["cdh"][8] | 0x8) if False else struct.unpack_from("<H", cdh, 8)[0] & ~0x8)
        struct.pack_into("<I", cdh, 42, off)
        out += cdh
    cd_end = len(out)
    out += struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, len(entries), len(entries),
                       cd_end - cd_start, cd_start, 0)
    open(dst, "wb").write(out)
    print(f"zipalign-mini: {len(entries)} entries, {src} -> {dst}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
