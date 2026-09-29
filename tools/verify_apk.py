#!/usr/bin/env python3
"""Gate a built APK so a crash-on-launch cannot ship again.

The v1.0.2 release declared com.geminishell.app.MainActivity as its launcher
activity but the dex contained only the generated R classes, because javac
never produced MainActivity.class and d8 dexed what was there and exited 0.
Nothing in the pipeline checked.

This script decodes the dex string table directly (no SDK needed) and fails
unless the launcher activity named in the manifest is actually present.
"""
import struct
import sys
import zipfile


def manifest_activity(apk):
    """Pull the activity android:name out of the binary manifest."""
    data = apk.read('AndroidManifest.xml')
    raw = data.decode('utf-16-le', 'ignore')
    joined = ''.join(raw)
    # the string pool holds the fully-qualified activity name
    for token in joined.split('\x00'):
        pass
    idx = joined.find('MainActivity')
    if idx < 0:
        return None
    start = joined.rfind('.', 0, idx)
    # walk backwards to the start of the package-qualified name
    begin = idx
    while begin > 0 and (joined[begin - 1].isalnum() or joined[begin - 1] in '._$'):
        begin -= 1
    return joined[begin:idx + len('MainActivity')]


def dex_strings(blob):
    """Return the dex string table as a set."""
    if blob[:4] != b'dex\n':
        raise ValueError(f'not a dex file (magic={blob[:4]!r})')
    ssz, soff = struct.unpack_from('<II', blob, 0x38)
    out = set()
    for i in range(ssz):
        o = struct.unpack_from('<I', blob, soff + i * 4)[0]
        p = o
        shift = 0
        n = 0
        while True:
            b = blob[p]
            p += 1
            n |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        end = blob.index(b'\x00', p)
        out.add(blob[p:end].decode('utf-8', 'replace'))
    return out


def main(path):
    apk = zipfile.ZipFile(path)
    names = set(apk.namelist())
    print(f'== {path} ==')
    print('entries:', sorted(names))

    ok = True

    if 'classes.dex' not in names:
        print('FAIL: no classes.dex')
        return 1

    activity = manifest_activity(apk)
    print(f'manifest launcher activity: {activity}')
    if not activity:
        print('FAIL: could not read launcher activity from manifest')
        return 1

    strs = dex_strings(apk.read('classes.dex'))
    print(f'dex strings: {len(strs)}')

    descriptor = 'L' + activity.replace('.', '/') + ';'
    has_class = descriptor in strs
    has_url = any('gemini.google.com' in s for s in strs)
    has_customtabs = any('CustomTabsIntent' in s for s in strs)
    has_oncreate = 'onCreate' in strs

    for label, val in (
        (f'activity class {descriptor}', has_class),
        ('CustomTabsIntent', has_customtabs),
        ('onCreate', has_oncreate),
        ('gemini url', has_url),
    ):
        print(f"  {label}: {'OK' if val else 'MISSING'}")
        ok = ok and val

    if not ok:
        print('\nFAIL: APK would crash on launch (launcher class not in dex)')
        return 1

    print('\nPASS: launcher class present in dex')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'gemini-shell.apk'))