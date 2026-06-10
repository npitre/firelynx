#!/bin/bash

# Firelynx Installer
# Installs Firelynx for the current user

set -e  # Exit on any error

echo "Installing Firelynx..."

# Check for required dependencies
echo "Checking dependencies..."

missing_deps=()

# Check for Python 3 (3.8+ required — Selenium 4.x sets the floor)
if ! command -v python3 >/dev/null 2>&1; then
    missing_deps+=("python3")
elif ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    pyver=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    echo "❌ Python 3.8 or newer is required, but python3 is $pyver."
    echo "   Selenium 4.x needs 3.8+ (3.9+ recommended). Install a newer Python and"
    echo "   run Firelynx from a virtualenv built with it — see the README."
    exit 1
fi

# Check for Firefox
if ! command -v firefox >/dev/null 2>&1; then
    missing_deps+=("firefox")
fi

# Check for lynx
if ! command -v lynx >/dev/null 2>&1; then
    missing_deps+=("lynx")
fi

# Check for GCC (needed to build ProxySSL)
if ! command -v gcc >/dev/null 2>&1; then
    missing_deps+=("gcc")
fi

# Check for make (needed to build ProxySSL)
if ! command -v make >/dev/null 2>&1; then
    missing_deps+=("make")
fi

# Check for OpenSSL development libraries (needed to build ProxySSL)
if ! pkg-config --exists openssl >/dev/null 2>&1; then
    if [[ -f /etc/fedora-release ]]; then
        missing_deps+=("openssl-devel")
    elif [[ -f /etc/debian_version ]]; then
        missing_deps+=("libssl-dev")
    else
        missing_deps+=("openssl-dev")
    fi
fi

# Check for Selenium (try importing)
if ! python3 -c "import selenium" >/dev/null 2>&1; then
    missing_deps+=("python3-selenium")
fi

# Check for selenium-manager (built into modern Selenium)
selenium_manager_available=false
selenium_manager_type=""
if command -v selenium-manager >/dev/null 2>&1; then
    selenium_manager_available=true
    selenium_manager_type="standalone"
elif python3 -c "import selenium.webdriver.common.selenium_manager" >/dev/null 2>&1; then
    selenium_manager_available=true
    selenium_manager_type="embedded"
fi

# Report missing dependencies
if [[ ${#missing_deps[@]} -gt 0 ]]; then
    echo ""
    echo "❌ Missing required dependencies:"
    printf '  - %s\n' "${missing_deps[@]}"
    echo ""
    
    # Detect distribution and suggest installation commands
    if [[ -f /etc/fedora-release ]]; then
        echo "On Fedora, install with:"
        echo "  sudo dnf install ${missing_deps[*]}"
        if [[ "$selenium_manager_available" == false ]]; then
            echo "  sudo dnf install selenium-manager  # For automatic geckodriver management"
        fi
    elif [[ -f /etc/debian_version ]]; then
        echo "On Debian/Ubuntu, install with:"
        echo "  sudo apt update"
        echo "  sudo apt install ${missing_deps[*]}"
        echo ""
        echo "Note: Modern Selenium (4.x+) includes automatic geckodriver download."
        echo "No additional packages needed - geckodriver will be downloaded on first use."
    else
        echo "Install these packages using your distribution's package manager."
        echo "Note: Selenium 4.x+ includes automatic geckodriver download."
    fi
    
    echo ""
    echo "Please install the missing dependencies and run this installer again."
    exit 1
fi

if [[ "$selenium_manager_available" == false ]]; then
    echo "ℹ️  geckodriver will be downloaded automatically on first use"
    echo "   Modern Selenium (4.x+) handles driver management internally"
    if [[ -f /etc/fedora-release ]]; then
        echo "   (Optional: install selenium-manager package for faster startup)"
    fi
    echo ""
elif [[ $selenium_manager_type == "embedded" ]]; then
    echo "✅ Selenium manager available (embedded in Selenium package)"
    echo ""
elif [[ $selenium_manager_type == "standalone" ]]; then
    echo "✅ Selenium manager available (standalone command)"
    echo ""
fi

echo "✅ All required dependencies found!"
echo ""

# Build ProxySSL for HTTPS support
echo "Building ProxySSL for HTTPS support..."
cd proxyssl
if make; then
    echo "✅ ProxySSL built successfully"
else
    echo "❌ Failed to build ProxySSL"
    echo "HTTPS navigation may not work properly"
    echo "You can try building manually later with: cd proxyssl && make"
fi
cd ..
echo ""

# Create necessary directories
echo "Creating directories..."
mkdir -p "$HOME/.local/share/firelynx"
mkdir -p "$HOME/bin"

# Copy Python files and JavaScript modules to ~/.local/share/firelynx/
echo "Installing Firelynx Python files and JavaScript modules..."

# Copy modular structure
if [[ -d "src" ]]; then
    echo "Installing modular structure..."
    cp -r src "$HOME/.local/share/firelynx/"
else
    echo "Error: src directory not found. Cannot install Firelynx."
    echo "Please run this script from the Firelynx project directory."
    exit 1
fi

# Always copy JavaScript modules
cp -r js "$HOME/.local/share/firelynx/"

# Copy browser extensions (stealth WebDriver bypass loaded at Firefox startup)
cp -r extensions "$HOME/.local/share/firelynx/"

# Copy ProxySSL library if it was built successfully
if [[ -f proxyssl/libproxyssl.so ]]; then
    echo "Installing ProxySSL library..."
    mkdir -p "$HOME/.local/share/firelynx/proxyssl"
    cp proxyssl/libproxyssl.so "$HOME/.local/share/firelynx/proxyssl/"
    echo "✅ ProxySSL installed - HTTPS support enabled"
else
    echo "⚠️  ProxySSL not found - HTTPS support disabled"
    echo "   Build manually with: cd proxyssl && make"
fi

# Copy main executable to ~/bin/
echo "Installing firelynx executable..."
cp firelynx "$HOME/bin/"

# Ensure ~/bin is in PATH
if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
    echo ""
    echo "⚠️  NOTE: $HOME/bin is not in your PATH."
    echo "Add this line to your ~/.bashrc or ~/.zshrc:"
    echo "    export PATH=\"\$HOME/bin:\$PATH\""
    echo ""
    echo "Then restart your terminal or run: source ~/.bashrc"
    echo ""
fi

# Initialize geckodriver if selenium-manager is available as standalone command
if [[ $selenium_manager_type == "standalone" ]]; then
    echo "Pre-downloading geckodriver with selenium-manager..."
    selenium-manager --browser firefox --output JSON >/dev/null 2>&1 || true
elif [[ $selenium_manager_type == "embedded" ]]; then
    echo "Selenium manager is embedded - geckodriver will download on first Firefox launch"
fi

echo ""
echo "✅ Firelynx installed successfully!"
echo ""
echo "Usage:"
echo "  firelynx https://example.com           # Browse with lynx interface"
echo "  firelynx                               # Start with search page"
echo "  firelynx --dump --search 'query'      # Text output mode"
echo ""

# Test if firelynx is immediately available
if command -v firelynx >/dev/null 2>&1; then
    echo "🚀 Ready to use! Try: firelynx"
else
    echo "Restart your terminal or run 'source ~/.bashrc' to use firelynx"
fi