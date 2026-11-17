#!/usr/bin/env bash
# Build a self-contained mkvtune .deb for Debian: pure-Python dependencies
# vendored under /usr/lib/mkvtune/vendor so the package needs no pip.
# Output: packaging/deb/build/mkvtune_<version>-1_all.deb
set -euo pipefail

cd "$(dirname "$0")/../.."
VERSION=$(python3 -c "import re; print(re.search(r'^version = \"(.+)\"', open('pyproject.toml').read(), re.M).group(1))")
BUILD=packaging/deb/build
ROOT="$BUILD/mkvtune_${VERSION}-1_all"
DEB="$BUILD/mkvtune_${VERSION}-1_all.deb"

rm -rf "$BUILD"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/lib/mkvtune/vendor" "$ROOT/usr/share/doc/mkvtune"

echo ">> resolving wheels (mkvtune + textual)"
python3 -m pip wheel --no-deps --wheel-dir "$BUILD/wheels" . >/dev/null
python3 -m pip wheel --wheel-dir "$BUILD/wheels" "textual>=8.2,<9" >/dev/null

echo ">> unpacking wheels into vendor/"
for wheel in "$BUILD"/wheels/*.whl; do
    python3 -m zipfile -e "$wheel" "$ROOT/usr/lib/mkvtune/vendor/"
done

# vendored payload must stay pure-Python for Architecture: all
if find "$ROOT/usr/lib/mkvtune/vendor" -name '*.so' | grep -q .; then
    echo "ERROR: compiled extension in vendor payload; Architecture: all would lie" >&2
    exit 1
fi

cat > "$ROOT/usr/bin/mkvtune" <<'EOF'
#!/usr/bin/python3
"""mkvtune launcher (Debian package)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib" / "mkvtune" / "vendor"))

from mkvtune.cli import main

if __name__ == "__main__":
    sys.exit(main())
EOF
chmod 755 "$ROOT/usr/bin/mkvtune"

cp README.md "$ROOT/usr/share/doc/mkvtune/README.md"
cp LICENSE "$ROOT/usr/share/doc/mkvtune/copyright"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: mkvtune
Version: ${VERSION}-1
Section: video
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), mkvtoolnix
Installed-Size: $(du -sk "$ROOT/usr" | cut -f1)
Maintainer: TheRealAshito <TheRealAshito@users.noreply.github.com>
Homepage: https://github.com/TheRealAshito/mkvtune
Description: TUI for MKV audio/subtitle track flag management
 Pick which MKV audio/subtitle track wins and batch-apply
 enabled/default/forced flags with mkvpropedit -- in place, with no
 remux and no re-encode. Includes an interactive TUI and a scriptable
 CLI, undo journals and post-apply verification.
 .
 No network access, no telemetry.
EOF

dpkg-deb --build --root-owner-group "$ROOT"
echo ">> built: $DEB"
dpkg-deb --info "$DEB" | sed -n '1,12p'
