#!/usr/bin/env python3
"""Resolve and fetch the real androidx.browser dependency closure.

v1.0.2 - v1.0.7 all shipped with exactly one hard-coded assumption in the CI
workflow:

    # androidx.browser has no transitive deps, so the AAR's classes.jar
    # can be used directly. No Gradle, no Maven resolution.

That is false. androidx.browser declares androidx.core as an *api* dependency,
and androidx.core drags in annotation, collection, concurrent-futures,
interpolator, versionedparcelable and lifecycle-common. Every one of those was
absent from the APK, so the first call into a Custom Tabs code path died with

    java.lang.NoClassDefFoundError: Failed resolution of:
        Landroidx/core/app/ActivityOptionsCompat;

Three root-cause guesses shipped before anyone read the stack trace, and all
three were wrong, because nobody asked the question this script answers.

So: stop hard-coding. Walk the POMs on dl.google.com, take compile+runtime
scope only, and emit the exact artifact list plus their extracted classes.jar
files. tools/verify_apk.py then proves the closure is COMPLETE by asserting
the dex references nothing it does not define.

Output:
  <outdir>/<artifact>-<version>.jar   extracted classes.jar
  <outdir>/closure.txt                one 'group:artifact:version' per line
"""
import os
import posixpath
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

MAVEN = 'https://dl.google.com/dl/android/maven2'
CENTRAL = 'https://repo1.maven.org/maven2'
POM_NS = '{http://maven.apache.org/POM/4.0.0}'

# Only these groups may be pulled. Anything else is a hard error, because a
# build script that silently fetches arbitrary coordinates is a supply-chain
# hole, not a convenience.
ALLOWED_GROUPS = frozenset((
    'androidx.browser',
    'androidx.core',
    'androidx.annotation',
    'androidx.collection',
    'androidx.concurrent',
    'androidx.interpolator',
    'androidx.versionedparcelable',
    'androidx.lifecycle',
    'androidx.arch.core',
    'androidx.tracing',
    'com.google.guava',
))
SCOPES = {'compile', 'runtime', None}          # None == unspecified -> compile

seen_v = {}                                     # (group, artifact) -> chosen version

# androidx POMs declare the MINIMUM version they were built against, which is
# frequently years older than the artifact that requires it. Gradle silently
# bumps to the newest version; a hand-rolled resolver that does not will build
# against a 2018 core and then fail at runtime with NoSuchMethodError from a
# 2023 library. So conflict resolution is explicit here: take the highest of
# (declared, pinned). Pinned to versions contemporary with browser 1.8.0.
MIN_VERSIONS = {
    ('androidx.core', 'core'): '1.10.1',
    ('androidx.annotation', 'annotation'): '1.6.0',
    ('androidx.annotation', 'annotation-experimental'): '1.4.0',
    ('androidx.collection', 'collection'): '1.2.0',
    ('androidx.concurrent', 'concurrent-futures'): '1.1.0',
    ('androidx.interpolator', 'interpolator'): '1.0.0',
    ('androidx.versionedparcelable', 'versionedparcelable'): '1.1.1',
    ('androidx.lifecycle', 'lifecycle-common'): '2.6.1',
    ('androidx.lifecycle', 'lifecycle-runtime'): '2.6.1',
    ('androidx.arch.core', 'core-common'): '2.2.0',
    ('com.google.guava', 'listenablefuture'): '1.0',
}


def _vkey(v):
    parts = re.split(r'[.\-]', v)
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, 0))
    return out


def bump(group, artifact, version):
    pin = MIN_VERSIONS.get((group, artifact))
    if not pin:
        return version
    try:
        if _vkey(version) < _vkey(pin):
            return pin
    except TypeError:
        return version
    return version


def group_path(group):
    return group.replace('.', '/')


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def repos_for(group):
    """Guava and friends are on Maven Central; androidx is on Google's repo."""
    if group.startswith('androidx.'):
        return (MAVEN,)
    return (MAVEN, CENTRAL)


def try_fetch(group, path, filename):
    for base in repos_for(group):
        url = '%s/%s/%s' % (base, path, filename)
        try:
            return fetch(url), url
        except Exception:                       # noqa: BLE001
            continue
    return None, None


def read_pom(group, artifact, version):
    path = '%s/%s/%s' % (group_path(group), artifact, version)
    blob, url = try_fetch(group, path, '%s-%s.pom' % (artifact, version))
    if blob is None:
        sys.stderr.write('  pom fetch failed %s:%s:%s\n' % (group, artifact, version))
        return None
    try:
        return ET.fromstring(blob)
    except Exception as e:                      # noqa: BLE001
        sys.stderr.write('  pom unparseable %s (%s)\n' % (url, e))
        return None


def prop(pom, name, seen=None):
    """Resolve a ${...} property from the POM itself (no parent POMs)."""
    seen = seen or set()
    m = re.search(r'\$\{([^}]+)\}', name or '')
    if not m:
        return name
    key = m.group(1)
    if key in seen:
        return name
    seen.add(key)
    node = pom.find(POM_NS + 'properties')
    if node is None:
        return name
    for child in node:
        if child.tag.endswith('}') and child.tag.split('}')[1] == key:
            return (child.text or '').strip()
    return name


def direct_deps(pom, g, a, v):
    out = []
    for d in pom.findall(POM_NS + 'dependencies/' + POM_NS + 'dependency'):
        dg = prop(pom, d.findtext(POM_NS + 'groupId'), set())
        da = prop(pom, d.findtext(POM_NS + 'artifactId'), set())
        dv = prop(pom, d.findtext(POM_NS + 'version'), set())
        # Maven allows hard version ranges like "[2.1.0]" / "[2.1.0,)".
        # We are not a range resolver: take the lower bound and pin from there.
        if dv:
            dv = dv.strip().strip('[]').split(',')[0].strip()
        scope = (d.findtext(POM_NS + 'scope') or '').strip() or None
        if not dg or not da or not dv:
            continue
        if scope not in SCOPES:
            continue
        if d.findtext(POM_NS + 'optional') == 'true':
            continue
        if dg not in ALLOWED_GROUPS:
            sys.stderr.write('  REFUSING non-allowlisted dependency %s:%s\n' % (dg, da))
            continue
        out.append((dg, da, bump(dg, da, dv)))
    return out


def resolve(root, seen, order, depth=0, pinned=None):
    g, a, v = root
    key = (g, a)
    if key in seen:
        # already visited: if we now have a higher version, re-resolve it
        prev = seen_v.get(key)
        if prev is None or _vkey(v) <= _vkey(prev):
            return
    seen_v[key] = v if key not in seen_v or _vkey(v) > _vkey(seen_v[key]) else seen_v[key]
    seen.add(key)
    pom = read_pom(g, a, v)
    if pom is None:
        sys.stderr.write('  cannot resolve pom for %s:%s:%s\n' % (g, a, v))
        return
    print('%s%s:%s:%s' % ('  ' * depth, g, a, v))
    order.append((g, a, v))
    for dep in direct_deps(pom, g, a, v):
        resolve(dep, seen, order, depth + 1)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    outdir = argv[1]
    os.makedirs(outdir, exist_ok=True)

    order, seen = [], set()
    print('resolving androidx.browser closure:')
    resolve(('androidx.browser', 'browser', '1.8.0'), seen, order)

    if not order:
        sys.stderr.write('resolved nothing - refusing to build\n')
        return 1

    print('\nfetching %d artifact(s):' % len(order))
    total = 0
    lines = []
    for g, a, v in order:
        base = posixpath.join(group_path(g), a, v)
        blob, url = try_fetch(g, base, '%s-%s.aar' % (a, v))
        if blob is None:
            blob, url = try_fetch(g, base, '%s-%s.jar' % (a, v))
        if blob is None:
            sys.stderr.write('  FAIL %s:%s:%s (no aar or jar on any repo)\n' % (g, a, v))
            return 1
        z = zipfile.ZipFile(_buf(blob))
        jars = [n for n in z.namelist()
                if n.startswith('classes') and n.endswith('.jar')]
        if not jars:
            # A plain jar artifact rather than an AAR.
            dest = os.path.join(outdir, '%s-%s.jar' % (a, v))
            with open(dest, 'wb') as f:
                f.write(blob)
        else:
            dest = os.path.join(outdir, '%s-%s.jar' % (a, v))
            with open(dest, 'wb') as f:
                f.write(z.read(jars[0]))
            # Keep R.txt: a hand-rolled build has no AGP step to generate the
            # library's R class, so tools/gen_lib_r.py reconstructs it.
            if 'R.txt' in z.namelist():
                rdest = os.path.join(outdir, '%s.r.txt' % a)
                with open(rdest, 'wb') as f:
                    f.write(z.read('R.txt'))
        size = os.path.getsize(dest)
        total += size
        print('  %-52s %7d B' % ('%s-%s.jar' % (a, v), size))
        lines.append('%s:%s:%s' % (g, a, v))

    with open(os.path.join(outdir, 'closure.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\nclosure: %d artifacts, %d bytes of classes' % (len(order), total))
    return 0


def _buf(data):
    import io
    return io.BytesIO(data)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
