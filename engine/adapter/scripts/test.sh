#!/bin/bash

set -u
#set -e # break on error
#set -x # print commands
export NCCL_DEBUG=warn

# Check if `jq` is installed
if ! command -v jq &> /dev/null; then
    echo "❌ Error: 'jq' is required but not installed. Install it with 'apt install jq' or 'brew install jq'."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine current platform and python command
if [[ "$OSTYPE" == "darwin"* ]]; then
    python_cmd=python3
    current_platform="darwin"
else
    python_cmd=python
    current_platform="linux"
fi

# Parse command-line options
dry_run=false
target_cases=()
target_group=""
include_no_ci=false
passthrough_args=()
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dry-run|-n)
            dry_run=true
            shift
            ;;
        --cases=*)
            IFS=',' read -ra tmp_cases <<< "${1#*=}"
            target_cases+=("${tmp_cases[@]}")
            shift
            ;;
        --group=*)
            target_group="${1#*=}"
            shift
            ;;
        --include-no-ci)
            include_no_ci=true
            shift
            ;;
        *)
            passthrough_args+=("$1")
            shift
            ;;
    esac
done

# Determine the root directory (where the script is located)
ROOT_DIR=${SCRIPT_DIR}/..
cd "$ROOT_DIR"

# Parse configurations from launch.json based on target cases
if [[ ${#target_cases[@]} -gt 0 ]]; then
    # Determine if we're doing exact or fuzzy (substring) matching
    is_fuzzy=false
    clean_cases=()
    for case in "${target_cases[@]}"; do
        if [[ "$case" == ~* ]]; then
            is_fuzzy=true
            # Remove leading ~ and add to clean list
            clean_cases+=("${case#\~}")
        else
            clean_cases+=("$case")
        fi
    done

    if [[ "$is_fuzzy" == true ]]; then
        # Fuzzy: substring match using regex
        cases_regex=$(printf '%s\n' "${clean_cases[@]}" | jq -R . | jq -s -r 'join("|")')
        CONFIGS=$(jq -c --arg REGEX "$cases_regex" \
            '.configurations[] | select(type == "object" and .type == "debugpy" and (.name | test($REGEX)))' \
            "$ROOT_DIR/.vscode/launch.json")
    else
        # Exact: match against full name
        # Build a JSON array of exact strings
        exact_json=$(printf '%s\n' "${clean_cases[@]}" | jq -R . | jq -s .)
        CONFIGS=$(jq -c --argjson EXACT_NAMES "$exact_json" \
            '.configurations[] | select(type == "object" and .type == "debugpy" and (.name as $n | $EXACT_NAMES | index($n)))' \
            "$ROOT_DIR/.vscode/launch.json")
    fi
else
    CONFIGS=$(jq -c '.configurations[] | select(.type == "debugpy")' "$ROOT_DIR/.vscode/launch.json")
fi

# Further filter by group if the --group option was used
if [[ -n "$target_group" ]]; then
    # We use 'test($REGEX; "i")' for case-insensitive substring matching
    CONFIGS=$(echo "$CONFIGS" | jq -c --arg REGEX "$target_group" \
        'select(.presentation.group | test($REGEX; "i"))')
fi

# Check if there are any configs to run
if [[ -z "$CONFIGS" ]]; then
    echo "❌ No configurations found matching the specified criteria."
    if [[ ${#target_cases[@]} -gt 0 ]]; then
        echo "   - Cases: $(IFS=, ; echo "${target_cases[*]}")"
    fi
    if [[ -n "$target_group" ]]; then
        echo "   - Group keyword: $target_group"
    fi
    exit 1
fi

# Initialize error tracking
errors=()
successes=()

# Loop through each configuration
while IFS= read -r config; do
    # Extract configuration parameters
    NAME=$(echo "$config" | jq -r '.name')
    GROUP=$(echo "$config" | jq -r '.presentation.group // "No Group"')
    PLATFORM=$(echo "$config" | jq -r '.presentation.platform // "all"')

    # NEW: Filter based on the 'platform' key
    if [[ "$PLATFORM" == "$current_platform" ]] || [[ "$PLATFORM" == "all" ]]; then
        : # Platform matches or is 'all', continue processing
    elif [[ "$PLATFORM" == "no-ci" ]]; then
        if [[ "$include_no_ci" == false ]]; then
            echo "Skipping config: $NAME (reason: platform is 'no-ci'. Use --include-no-ci to run.)"
            continue
        fi
    else
        # Mismatch (e.g., PLATFORM is 'darwin' but current_platform is 'linux')
        echo "Skipping config: $NAME (reason: platform mismatch. Required: '$PLATFORM', current: '$current_platform')"
        continue
    fi

    REQUEST_TYPE=$(echo "$config" | jq -r '.request')
    if [[ "$REQUEST_TYPE" == "attach" ]]; then
        echo "Skipping config: $NAME (reason: request is attach)"
        continue
    fi
    
    # Handle program path
    PROGRAM=$(echo "$config" | jq -r '.program')
    if [[ "$PROGRAM" == "\${file}" ]]; then
        echo "Skipping config: $NAME"
        continue
    fi
    if [[ "$PROGRAM" == ".venv/bin/torchrun" ]]; then
        PROGRAM=torchrun
    fi
    if [[ "$PROGRAM" == *.py ]]; then
        CMD="$python_cmd $PROGRAM"
    else
        path=$(command -v "$PROGRAM" 2>/dev/null)
        CMD="$path"
    fi

    # Prepare arguments
    # 1. Get the base arguments from launch.json. Initialize to be safe with set -u.
    config_args=()
    config_args=($(echo "$config" | jq -r '.args // [] | @sh'))

    # 2. Define the standard additional arguments for testing.
    ADDITIONAL_ARGS=(
    )

    # 3. Combine all argument arrays. This is safe even if some arrays are empty.
    ARGS_ARRAY=("${config_args[@]:-}" "${ADDITIONAL_ARGS[@]:-}" "${passthrough_args[@]:-}")

    CLEAN_ARGS_ARRAY=()
    for arg in "${ARGS_ARRAY[@]:-}"; do
        CLEAN_ARGS_ARRAY+=("${arg//\'/}")
    done
    JOINED_ARGS=$(printf "%s " "${CLEAN_ARGS_ARRAY[@]:-}")

    # Execute command
    cd "$ROOT_DIR"
    echo " CASE: [$GROUP] $NAME"
    
    start=$(date +%s)
    if $dry_run; then
        echo "[DRY RUN] $CMD $JOINED_ARGS"
        CMD_EXIT_CODE=0
    else
        echo "[RUNNING] $CMD $JOINED_ARGS"
        $CMD $JOINED_ARGS
        CMD_EXIT_CODE=$?
    fi
    end=$(date +%s)
    DURATION="($(( end - start )) s)"

    # Track results
    if [ $CMD_EXIT_CODE -eq 0 ]; then
        echo "✅ [$GROUP] $NAME completed successfully $DURATION."
        successes+=("[$GROUP] $NAME $DURATION")
    else
        echo "❌ [$GROUP] $NAME failed with exit code $CMD_EXIT_CODE $DURATION."
        errors+=("[$GROUP] $NAME failed with exit code $CMD_EXIT_CODE $DURATION.")
    fi

    echo ""
    echo "🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀"
    echo ""
done <<< "$CONFIGS"

# Final report
echo ""
echo "✅ Summary of successes:"
printf "    - %s\n" "${successes[@]:-None}"
echo "❌ Summary of errors:"
if [ ${#errors[@]} -gt 0 ]; then
    printf "    - %s\n" "${errors[@]}"
    exit 1
else
    echo "    - None"
fi
