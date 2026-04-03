#!/bin/bash
# Creates the "Laser Lab" macOS .app bundle and registers it with Launchpad.
# Run from anywhere — the script resolves the repo path automatically.

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Laser Lab.app"

echo "Repo: $REPO_DIR"
echo "Creating $APP ..."

mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# Launcher script
cat > "$APP/Contents/MacOS/launcher" << EOF
#!/bin/bash
cd "$REPO_DIR"
source .env_spectrometer/bin/activate
python launcher_gui.py
EOF
chmod +x "$APP/Contents/MacOS/launcher"

# Info.plist
cat > "$APP/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Laser Lab</string>
    <key>CFBundleDisplayName</key>
    <string>Laser Lab</string>
    <key>CFBundleIdentifier</key>
    <string>uk.ac.lcls.laser-lab</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
</dict>
</plist>
EOF

# Generate icon (requires venv with Pillow already set up)
echo "Generating icon ..."
source "$REPO_DIR/.env_spectrometer/bin/activate"
python "$REPO_DIR/make_icon.py"

# Notify Launchpad
touch "$APP"

echo "Done. 'Laser Lab' will appear in Launchpad shortly."
