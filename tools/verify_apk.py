#!/usr/bin/env python3
"""Release gate for gemini-shell.

The invariant that matters
--------------------------
A dex may reference types it does not define only if something else on the
runtime classpath provides them. In this APK every dependency is dexed in, so
"referenced but not defined" must be EMPTY apart from platform/JDK types.

This one check would have caught the crash that shipped in v1.0.2 through
v1.0.7:

    java.lang.NoClassDefFoundError: Failed resolution of:
        Landroidx/core/app/ActivityOptionsCompat;
    at androidx.browser.customtabs.CustomTabsIntent$Builder.setExitAnimations

androidx.browser declares androidx.core as an api dependency, and the CI
workflow asserted in a comment that "androidx.browser has no transitive deps"
and dexed only that one AAR. Twenty-two referenced types were missing. Three
root-cause guesses (missing activity class, corrupt resources.arsc, toolchain
mismatch) were all wrong, because all three were guesses about a stack trace
nobody had read.

The gate is layered, cheapest and most general first:

  1. dex type closure      - no referenced-but-undefined type   <- the real one
  2. declared components   - every manifest component is in the dex
  3. required types        - the Custom Tabs surface actually called
  4. required entries      - classes.dex / manifest / resources.arsc present

The manifest is read from the PLAINTEXT source. A previous version of this
script hand-parsed the binary AXML string pool and emitted garbage; a gate
built on a fallible parser produces false results, and a gate that cries wolf
gets switched off.

Usage: verify_apk.py <apk> [source-manifest]
"""
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

ANDROID_NS = '{http://schemas.android.com/apk/res/android}'

# Types MainActivity calls directly.
REQUIRED_TYPES = [
    'Landroidx/browser/customtabs/CustomTabsIntent;',
    'Landroidx/browser/customtabs/CustomTabsClient;',
    'Landroidx/browser/customtabs/CustomTabsSession;',
    'Landroidx/browser/customtabs/CustomTabsServiceConnection;',
    # present precisely because v1.0.7 died here
    'Landroidx/core/app/ActivityOptionsCompat;',
]

COMPONENT_TAGS = ('activity', 'activity-alias', 'service', 'receiver', 'provider')

# Provided by the platform / the JDK, never by this APK.
PLATFORM_PREFIXES = (
    'android/', 'java/', 'javax/', 'kotlin/', 'kotlinx/', 'dalvik/',
    'sun/', 'jdk/', 'org/w3c/', 'org/xml/', 'org/xmlpull/', 'org/json/',
    'org/intellij/', 'org/jetbrains/',
)

# Deliberately NOT closed. Narrow by design: a broad "ignore these" list is
# how a gate stops being a gate.
#
# androidx.annotation types are CLASS-retained annotations. ART resolves them
# lazily and only when something calls getAnnotations(); a missing annotation
# class never produces a NoClassDefFoundError on a code path that actually
# runs. The androidx.annotation artifact also ships a stub jar at 1.6.0 (the
# real classes live in annotation-jvm), so demanding it would fail the build on
# a class the runtime does not need. Everything else must resolve.
COMPILE_ONLY_PREFIXES = (
    'androidx/annotation/',
)


def uleb128(blob, p):
    shift = 0
    val = 0
    while True:
        b = blob[p]
        p += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, p
        shift += 7


def dex_strings(blob):
    if blob[:4] != b'\x64\x65\x78\n':
        raise ValueError('not a dex file')
    ssz, soff = struct.unpack_from('<II', blob, 0x38)
    out = set()
    for i in range(ssz):
        p = struct.unpack_from('<I', blob, soff + i * 4)[0]
        n, p = uleb128(blob, p)
        out.add(blob[p:blob.index(b'\x00', p)].decode('utf-8', 'replace'))
    return out


def dex_type_closure(blob):
    """Return (referenced, defined) as sets of type descriptors."""
    ssz, soff = struct.unpack_from('<II', blob, 0x38)
    tsz, toff = struct.unpack_from('<II', blob, 0x40)
    csz, coff = struct.unpack_from('<II', blob, 0x60)

    strings = []
    for i in range(ssz):
        p = struct.unpack_from('<I', blob, soff + i * 4)[0]
        n, p = uleb128(blob, p)
        strings.append(blob[p:blob.index(b'\x00', p)].decode('utf-8', 'replace'))

    types = [strings[struct.unpack_from('<I', blob, toff + i * 4)[0]]
             for i in range(tsz)]
    defined = {types[struct.unpack_from('<I', blob, coff + i * 32)[0]]
               for i in range(csz)}
    return set(types), defined


def strip_array(desc):
    d = desc.lstrip('[')
    if d.startswith('L') and d.endswith(';'):
        d = d[1:-1]
    return d


def unresolved_types(blob):
    referenced, defined = dex_type_closure(blob)
    out = set()
    for t in referenced:
        if t in defined:
            continue
        norm = strip_array(t)
        if not norm or '/' not in norm:
            continue                       # primitive or non-class type
        if norm.startswith(PLATFORM_PREFIXES):
            continue
        if norm.startswith(COMPILE_ONLY_PREFIXES):
            continue
        out.add(norm)
    return out, len(defined), len(referenced)


def declared_components(manifest_path):
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

    # ---- 4. required entries -------------------------------------------
    for required in ('classes.dex', 'AndroidManifest.xml', 'resources.arsc'):
        if required not in names:
            print('FAIL: APK is missing %s' % required)
            return 1
    print('required entries: OK')

    dex = apk.read('classes.dex')

    # ---- 1. dex type closure (the real gate) ---------------------------
    unres, ndef, nref = unresolved_types(dex)
    print('')
    print('-- dex type closure --')
    print('types defined: %d   referenced: %d' % (ndef, nref))
    if unres:
        print('UNRESOLVED: %d type(s) referenced but not defined' % len(unres))
        for t in sorted(unres):
            print('  %s' % t)
        failures.append(
            'dex references %d types it does not define -> NoClassDefFoundError '
            'on first use (this is what killed v1.0.2 through v1.0.7)' % len(unres))
    else:
        print('closure: COMPLETE (no referenced-but-undefined types)')

    # ---- 2. declared components ----------------------------------------
    print('')
    print('-- declared components --')
    try:
        pkg, components = declared_components(manifest_path)
    except Exception as e:                  # noqa: BLE001
        print('FAIL: could not read %s: %s' % (manifest_path, e))
        return 1
    print('manifest package: %s' % pkg)
    strs = dex_strings(dex)
    for comp in components:
        desc = 'L' + comp.replace('.', '/') + ';'
        ok = desc in strs
        print('  %-48s %s' % (desc, 'OK' if ok else 'MISSING FROM DEX'))
        if not ok:
            failures.append('%s declared in manifest but absent from dex' % comp)

    # ---- 3. required types ---------------------------------------------
    print('')
    print('-- required types --')
    for desc in REQUIRED_TYPES:
        ok = desc in strs
        print('  %-48s %s' % (desc, 'OK' if ok else 'MISSING'))
        if not ok:
            failures.append('%s missing from dex' % desc)

    has_url = any('gemini.google.com' in s for s in strs)
    print('  target url constant: %s' % ('OK' if has_url else 'MISSING'))
    if not has_url:
        failures.append('target url constant not found in dex')

    print('')
    if failures:
        for f in failures:
            print('FAIL: %s' % f)
        return 1
    print('PASS: closure complete, every declared component present')
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'app/AndroidManifest.xml'))
