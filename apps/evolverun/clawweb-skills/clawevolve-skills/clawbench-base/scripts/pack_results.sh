#!/usr/bin/env bash
# Pack results directory for archiving and download.
# Usage:
#   ./scripts/pack_results.sh                    # pack entire results/
#   ./scripts/pack_results.sh --scene "0410评测"  # pack a specific scene

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/results"
OUTPUT_DIR="$PROJECT_ROOT/dist"
SCENE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --scene)
            SCENE="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--scene <name>] [-o <output_dir>]"
            echo ""
            echo "Options:"
            echo "  --scene <name>    Pack results for a specific scene only"
            echo "  -o, --output      Output directory (default: dist/)"
            echo "  -h, --help        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [[ ! -d "$RESULTS_DIR" ]]; then
    echo "Error: results/ directory not found at $RESULTS_DIR"
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
mkdir -p "$OUTPUT_DIR"

if [[ -z "$SCENE" ]]; then
    # Pack entire results/
    ARCHIVE_NAME="results_${TIMESTAMP}.tar.gz"
    echo "Packing entire results/ ..."
    tar -czf "$OUTPUT_DIR/$ARCHIVE_NAME" -C "$PROJECT_ROOT" results
else
    # Pack specific scene across benchmark/baseline
    TEMP_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_DIR" EXIT

    FOUND=0
    for component in benchmark baseline; do
        SRC="$RESULTS_DIR/$component/$SCENE"
        if [[ -d "$SRC" ]]; then
            mkdir -p "$TEMP_DIR/results/$component/$SCENE"
            cp -R "$SRC/." "$TEMP_DIR/results/$component/$SCENE/"
            FOUND=1
        fi
    done

    if [[ $FOUND -eq 0 ]]; then
        echo "Error: no results found for scene \"$SCENE\""
        echo "Looked in:"
        for component in benchmark baseline; do
            echo "  results/$component/$SCENE/"
        done
        exit 1
    fi

    # Sanitize scene name for filename (replace spaces/special chars with _)
    SAFE_SCENE=$(echo "$SCENE" | sed 's/[^a-zA-Z0-9_\-]/_/g')
    ARCHIVE_NAME="results_${SAFE_SCENE}_${TIMESTAMP}.tar.gz"
    echo "Packing scene \"$SCENE\" ..."
    tar -czf "$OUTPUT_DIR/$ARCHIVE_NAME" -C "$TEMP_DIR" results
fi

ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"
SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
echo "Done: $ARCHIVE_PATH ($SIZE)"
