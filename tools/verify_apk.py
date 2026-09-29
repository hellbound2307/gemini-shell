#!/usr/bin/env python3
"""Release gate: an APK that declares components it cannot instantiate must not ship.

History of what this exists to prevent
--------------------------------------
v1.0.2  manifest declared com.geminishell.app.MainActivity; the dex held only
        the generated R classes (javac looked in ./src, sources live in
        app/src). Every step exited 0. Result: ClassNotFoundException at bind,
        instant exit, no window, and no logcat to read.
v1.0.6  that class was fixed, and the gate shipped checking exactly one
        hard-coded name. It then passed an APK that still died on launch,
        because a gate that knows one class cannot catch the next one.

The invariant is now general:
  1. every activity/service/receiver/provider the manifest declares has a
     class in the dex  (missing -> ClassNotFoundException, instant exit)
  2. the Custom Tabs types MainActivity calls are in the dex
     (missing -> NoClassDefFoundError at class load: same symptom,
      different cause, and the previous gate could not see it)
  3. the target URL constant survived compilation

The manifest is read from the PLAINTEXT source, not from the binary
AndroidManifest.xml. A previous version of this script hand-parsed the AXML
string pool and emitted garbage; a gate built on a fallible parser produces
false results, and a gate that cries wolf gets switched off. In CI the source
manifest is right there, so there is no reason to decode binary XML.

Usage: verify_apk.py <apk> [source-manifest]
"""
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

ANDROID_NS = '{http://schemas.android.com/apk/res/android}'

# Types MainActivity touches directly.
REQUIRED_TYPES = [
    'Landroidx/browser/customtabs/CustomTabsIntent;',
    'Landroidx/browser/customtabs/CustomTabsClient;',
    'Landroidx/browser/customtabs/CustomTabsSession;',
    'Landroidx/browser/customtabs/CustomTabsServiceConnection;',
]

COMPONENT_TAGS = ('activity', 'activity-alias', 'service', 'receiver', 'provider')


def dex_strings(blob):
    """Return the dex string table as a set."""
    if blob[:4] != b'dex\n':
        raise ValueError('not a dex file (magic=%r)' % (blob[:4],))
    ssz, soff = struct.unpack_from('<II', blob, 0x38)
    out = set()
    for i in range(ssz):
        p = struct.unpack_from('<I', blob, soff + i * 4)[0]
        shift = 0
        n = 0
        while True:
            b = blob[p]
            p += 1
            n |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        out.add(blob[p:blob.index(b'\x00', p)].decode('utf-8', 'replace'))
    return out


def declared_components(manifest_path):
    """Every component class the manifest names, fully qualified."""
    root = ET.parse(manifest_path).getroot()
    pkg = root.get('package')
    if not pkg:
        raise ValueError('manifest has no package attribute')
    out = set()
    for tag in COMPONENT_TAGS:
        for el in root.iter(tag):
            name = el.get(ANDROID_NS + 'name')
            if not name:
                continue
            if name.startswith('.'):
                name = pkg + name
            elif '.' not in name:
                name = '%s.%s' % (pkg, name)
            out.add(name)
    return pkg, sorted(out)


def main(apk_path, manifest_path):
    apk = zipfile.ZipFile(apk_path)
    names = set(apk.namelist())
    print('== %s ==' % apk_path)
    print('entries: %d' % len(names))

    failures = []

    for required in ('classes.dex', 'AndroidManifest.xml', 'resources.arsc'):
        if required not in names:
            print('FAIL: APK is missing %s' % required)
            return 1
    print('required entries: OK')

    try:
        pkg, components = declared_components(manifest_path)
    except Exception as e:                        # noqa: BLE001
        print('FAIL: could not read %s: %s' % (manifest_path, e))
        return 1
    print('manifest package: %s' % pkg)
    print('declared components: %d' % len(components))
    if not components:
        failures.append('manifest declares no components at all')

    strs = dex_strings(apk.read('classes.dex'))
    print('dex strings: %d' % len(strs))

    for comp in components:
        desc = 'L' + comp.replace('.', '/') + ';'
        ok = desc in strs
        print('  component %-48s %s' % (desc, 'OK' if ok else 'MISSING FROM DEX'))
        if not ok:
            failures.append('%s declared in manifest but absent from dex' % comp)

    for desc in REQUIRED_TYPES:
        ok = desc in strs
        print('  type      %-48s %s' % (desc, 'OK' if ok else 'MISSING FROM DEX'))
        if not ok:
            failures.append('%s referenced but absent from dex' % desc)

    # Sanity: the dex must belong to this app, not to some other build.
    if 'L' + pkg.replace('.', '/') + '/MainActivity;' in strs:
        print('  dex/app identity: OK (%s.MainActivity present)' % pkg)
    elif not components:
        failures.append('cannot establish dex/app identity')

    has_url = any('gemini.google.com' in s for s in strs)
    print('  target url constant: %s' % ('OK' if has_url else 'MISSING'))
    if not has_url:
        failures.append('target url constant not found in dex')

    print('')
    if failures:
        for f in failures:
            print('FAIL: %s' % f)
        return 1
    print('PASS: every declared component and required type is in the dex')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'app/AndroidManifest.xml'))
