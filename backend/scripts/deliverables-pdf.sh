#!/bin/sh
# Convert a Markdown file (default: deliverables.md at the repository root)
# to an A4 PDF next to it.
#
#   1. mermaid-cli (mmdc) draws the ```mermaid blocks as SVG images;
#   2. pandoc turns the Markdown into one self-contained HTML page;
#   3. headless Chrome prints that page to PDF.
#
# Usage: scripts/deliverables-pdf.sh [input.md] [output.pdf]
# Needs: mmdc (npm i -g @mermaid-js/mermaid-cli), pandoc, Chrome or Chromium
# (set CHROME=/path/to/chrome if it is not found).
set -eu

root="$(cd "$(dirname "$0")/../.." && pwd)"
input="${1:-$root/deliverables.md}"
output="${2:-${input%.md}.pdf}"
input="$(cd "$(dirname "$input")" && pwd)/$(basename "$input")"
case "$output" in /*) ;; *) output="$PWD/$output" ;; esac

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing $1: $2" >&2; exit 1; }; }
need mmdc "npm install -g @mermaid-js/mermaid-cli"
need pandoc "brew install pandoc  (or: sudo apt install pandoc)"

chrome="${CHROME:-}"
if [ -z "$chrome" ]; then
    for candidate in \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium" \
        google-chrome google-chrome-stable chromium chromium-browser; do
        if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
            chrome="$candidate"
            break
        fi
    done
fi
[ -n "$chrome" ] || { echo "Chrome or Chromium not found; set CHROME=/path/to/chrome" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "Drawing Mermaid diagrams..."
mmdc -q -i "$input" -o "$work/doc.md" -e svg -b transparent

cat > "$work/pdf.css" <<'EOF'
@page { size: A4; margin: 16mm 14mm; }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; max-width: none; margin: 0; padding: 0; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 1.4em; border-bottom: 1px solid #ccc; padding-bottom: 3px; }
h3 { font-size: 12.5pt; margin-top: 1.2em; }
h4 { font-size: 11pt; }
h2, h3, h4 { break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 9pt; }
th, td { border: 1px solid #c8c8c8; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #f0f2f4; }
tr { break-inside: avoid; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.5pt; background: #f4f4f4; padding: 0 2px; border-radius: 2px; overflow-wrap: anywhere; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; padding: 8px; font-size: 8.5pt; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; display: block; margin: 0.6em auto; break-inside: avoid; }
a { color: #0b5cad; text-decoration: none; overflow-wrap: anywhere; }
EOF

echo "Building HTML..."
# hard_line_breaks keeps consecutive lines (e.g. the Project / Candidate /
# Repository header) on separate lines. Images resolve relative to the
# input file's folder; the drawn diagrams sit in the work folder.
pandoc "$work/doc.md" -f gfm+hard_line_breaks -t html5 -s \
    --metadata pagetitle="$(sed -n 's/^# //p' "$input" | head -1)" \
    --css "$work/pdf.css" --embed-resources \
    --resource-path="$(dirname "$input"):$work" \
    -o "$work/doc.html"

echo "Printing PDF..."
"$chrome" --headless=new --disable-gpu --no-pdf-header-footer \
    --print-to-pdf="$output" "file://$work/doc.html" >/dev/null 2>&1

[ -s "$output" ] || { echo "Chrome did not write $output" >&2; exit 1; }
echo "Written: $output"
