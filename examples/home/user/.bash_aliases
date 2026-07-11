function cx() {
    set -x

    local profile=""
    local cwd=$(pwd)

    # Check for m2energetic directories (including subdirectories)
    if [[ "$cwd" == /home/przemek/PCODE-pl/m2-kinetic* ]] || \
       [[ "$cwd" == /home/przemek/PCODE-pl/zadania* ]] || \
       [[ "$cwd" == /home/przemek/PCODE-pl/zadania-commerce* ]] || \
       [[ "$cwd" == /home/przemek/PCODE-pl/zadania-mageos* ]] || \
       [[ "$cwd" == /home/przemek/PCODE-pl/zadania-hyva* ]] || \
       [[ "$cwd" == /home/przemek/.venvs/sqlglot* ]]; then
        profile="--profile=m2energetic"
    # Check for mcptap directories (including subdirectories)
    elif [[ "$cwd" == /home/przemek/PCODE-pl/mcp-tap* ]] || \
         [[ "$cwd" == /home/przemek/PCODE-pl/mcp-tap-extras* ]]; then
        profile="--profile=mcptap"
    fi

    systemctl --user restart mcptap.service
    if [ -n "$profile" ]; then
        codex "$profile" "$@"
    else
        codex "$@"
    fi

    set +x
}
