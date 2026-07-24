#!/usr/bin/env bash
#
# Run all pytest test files in the ../tests directory, in parallel.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TESTS_DIR="${PROJECT_ROOT}/tests"

shopt -s nullglob
test_files=("${TESTS_DIR}"/test_*.py "${TESTS_DIR}"/*_test.py)
shopt -u nullglob

if [ ${#test_files[@]} -eq 0 ]; then
    echo "No test files found in ${TESTS_DIR}"
    exit 0
fi

# Deduplicate (in case both glob patterns match the same file)
mapfile -t test_files < <(printf '%s\n' "${test_files[@]}" | sort -u)

SKIP_TESTS=(test_log_store.py)

should_skip() {
    local base_name="$1"
    for skip in "${SKIP_TESTS[@]}"; do
        [ "${base_name}" = "${skip}" ] && return 0
    done
    return 1
}

VENV_DIR="${MCPTAP_VENV_DIR:-$HOME/.local/share/mcptap/.venv}"

# Activate the project venv so that pytest and all dependencies (including
# aiohttp) come from the venv's Python rather than the system interpreter.
if [ -f "${VENV_DIR}/bin/activate" ]; then
    source "${VENV_DIR}/bin/activate"
else
    echo "ERROR: venv not found at ${VENV_DIR}. Run setup.sh first." >&2
    exit 1
fi

# Ensure dev dependencies (pytest, pytest-asyncio) are installed in the venv.
if ! python3 -c "import pytest, pytest_asyncio" 2>/dev/null; then
    echo "Installing dev dependencies into venv..."
    pip install -q -r "${PROJECT_ROOT}/requirements-dev.txt"
fi

# sqlfluff and sqllineage rely on config files (.sqlfluff) in the project root,
# so tests must run with CWD set to the project root regardless of where the
# script is invoked from.
cd "${PROJECT_ROOT}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

pids=()
outputs=()

for i in "${!test_files[@]}"; do
    test_file="${test_files[$i]}"
    base_name="$(basename "${test_file}")"

    if should_skip "${base_name}"; then
        echo "Skipping: ${test_file}"
        continue
    fi

    out_file="${TMP_DIR}/test_${i}.out"
    outputs+=("${out_file}")

    (
        echo "=========================================="
        echo "Running: ${test_file}"
        echo "=========================================="

        if [ "${base_name}" = "evaluate_process_data_quality_test.py" ]; then
            pytest -q "${test_file}" -k 'not real_api'
        else
            pytest -q "${test_file}"
        fi
    ) >"${out_file}" 2>&1 &

    pids+=($!)
done

EXIT_CODE=0

for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
        :
    else
        EXIT_CODE=1
    fi
done

for out_file in "${outputs[@]}"; do
    cat "${out_file}"
    echo ""
done

exit "${EXIT_CODE}"
