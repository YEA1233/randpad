#!/bin/sh
# Builds randpad_<version>_all.deb from randpad_gui.py in the same folder.
# Usage: sh build_deb.sh [version]   (REPO_URL=https://you.github.io/randpad adds the apt source)   (needs only dpkg-deb, which every Debian/Mint box has)
set -e
cd "$(dirname "$0")"
SRC="$(pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

VERSION="${1:-1.0.0}"
PKG=randpad
ROOT="$STAGE/${PKG}_${VERSION}"
mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/lib/randpad" \
         "$ROOT/usr/lib/udev/rules.d" \
         "$ROOT/usr/lib/modules-load.d" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/scalable/apps"

# --- app + launcher ---
install -m 644 "$SRC/randpad_gui.py" "$ROOT/usr/lib/randpad/randpad_gui.py"
cat > "$ROOT/usr/bin/randpad" << 'EOF'
#!/bin/sh
exec python3 /usr/lib/randpad/randpad_gui.py "$@"
EOF
chmod 755 "$ROOT/usr/bin/randpad"

# --- permissions: let the logged-in user create/use virtual pads without sudo ---
cat > "$ROOT/usr/lib/udev/rules.d/60-randpad.rules" << 'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"
EOF
echo uinput > "$ROOT/usr/lib/modules-load.d/randpad.conf"

# --- menu entry + icon ---
cat > "$ROOT/usr/share/applications/randpad.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=RandPad
Comment=Virtual gamepad that sends random input or passes your real controller through
Exec=randpad
Icon=randpad
Terminal=false
Categories=Utility;Game;
Keywords=controller;gamepad;dualshock;random;autoclicker;
EOF

cat > "$ROOT/usr/share/icons/hicolor/scalable/apps/randpad.svg" << 'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#263238"/>
  <path d="M14 22h36c5 0 9 5 10 12l2 10c1 5-3 8-7 6l-7-4H16l-7 4c-4 2-8-1-7-6l2-10c1-7 5-12 10-12z"
        fill="#eceff1" transform="translate(0 2)"/>
  <rect x="17" y="28" width="12" height="4" rx="1" fill="#263238"/>
  <rect x="21" y="24" width="4" height="12" rx="1" fill="#263238"/>
  <circle cx="42" cy="26" r="2.5" fill="#c62828"/>
  <circle cx="48" cy="32" r="2.5" fill="#2e7d32"/>
  <circle cx="36" cy="32" r="2.5" fill="#1565c0"/>
  <circle cx="42" cy="38" r="2.5" fill="#f9a825"/>
</svg>
EOF

# --- optional: register the apt repo so future updates arrive via apt upgrade ---
if [ -n "$REPO_URL" ]; then
  mkdir -p "$ROOT/etc/apt/sources.list.d"
  echo "deb [trusted=yes] $REPO_URL ./" > "$ROOT/etc/apt/sources.list.d/randpad.list"
  echo /etc/apt/sources.list.d/randpad.list > "$ROOT/DEBIAN/conffiles"
fi

# --- package metadata ---
SIZE=$(du -sk "$ROOT" | cut -f1)
cat > "$ROOT/DEBIAN/control" << EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-evdev, python3-tk
Installed-Size: $SIZE
Maintainer: RandPad <randpad@localhost>
Description: Random virtual gamepad with real-controller passthrough
 Creates a virtual Xbox-style gamepad via uinput. Toggle between forwarding
 your real controller and sending random inputs, with a hotkey or one click.
 Useful for emulators such as DuckStation. Linux only.
EOF

cat > "$ROOT/DEBIAN/postinst" << 'EOF'
#!/bin/sh
set -e
modprobe uinput 2>/dev/null || true
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=misc --action=change 2>/dev/null || true
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q 2>/dev/null || true
exit 0
EOF
cat > "$ROOT/DEBIAN/postrm" << 'EOF'
#!/bin/sh
set -e
udevadm control --reload-rules 2>/dev/null || true
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q 2>/dev/null || true
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

dpkg-deb --root-owner-group --build "$ROOT" "$SRC/${PKG}_${VERSION}_all.deb"
echo "Built ${PKG}_${VERSION}_all.deb"
