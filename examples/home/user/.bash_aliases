function get_profile() {
    local cwd="${1:-$(pwd)}"

    # Check for vue/alokai directories (including subdirectories)
    if [[ "$cwd" == /home/user/PCODE-pl/zadania-alokai/nuxt3-magento-sdk-storefront* ]] || \
        [[ "$cwd" == /home/user/PCODE-pl/zadania-alokai/alokai* ]]; then
        printf '%s\n' "--profile=alokai"

    # Check for m2energetic directories (including subdirectories)
    elif [[ "$cwd" == /home/user/PCODE-pl/m2-kinetic* ]] || \
       [[ "$cwd" == /home/user/PCODE-pl/zadania* ]] || \
       [[ "$cwd" == /home/user/PCODE-pl/zadania-commerce* ]] || \
       [[ "$cwd" == /home/user/PCODE-pl/zadania-mageos* ]] || \
       [[ "$cwd" == /home/user/PCODE-pl/zadania-hyva* ]] || \
       [[ "$cwd" == /home/user/PCODE-pl/zadania-shopware* ]] || \
       [[ "$cwd" == /home/user/.venvs/sqlglot* ]]; then
        printf '%s\n' "--profile=m2energetic"

    # Check for mcptap directories (including subdirectories)
    elif [[ "$cwd" == /home/user/PCODE-pl/mcp-tap* ]] || \
         [[ "$cwd" == /home/user/PCODE-pl/mcp-tap-extras* ]]; then
        printf '%s\n' "--profile=mcptap"

    # Check for llm-council directories (including subdirectories)
    elif [[ "$cwd" == /home/user/PCODE-pl/llm-council* ]]; then
        printf '%s\n' "--profile=llmcouncil"
    fi
}

function cx() {
    set -x

    local profile
    profile=$(get_profile)

    if [ -n "$profile" ]; then
        LD_PRELOAD=/home/user/.local/lib/libmcptap_fileblock.so codex "$profile" "$@"
    else
        LD_PRELOAD=/home/user/.local/lib/libmcptap_fileblock.so codex "$@"
    fi

    set +x
}

function ha() {
    set -x

    local profile
    profile=$(get_profile)

    if [ -n "$profile" ]; then
        LD_PRELOAD=/home/user/.local/lib/libmcptap_fileblock.so hermes "$profile" "$@"
    else
        LD_PRELOAD=/home/user/.local/lib/libmcptap_fileblock.so hermes "$@"
    fi

    set +x
}
