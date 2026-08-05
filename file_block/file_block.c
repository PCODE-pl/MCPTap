/*
 * libmcptap_fileblock.so — LD_PRELOAD library for MCPTap session integration.
 *
 * 1. File access blocking
 *
 * Blocks file access to paths listed in a per-session control file.
 * The session ID is read from the CODEX_THREAD_ID environment variable
 * (set automatically by Codex CLI for all child processes), or
 * HERMES_SESSION_ID (set by Hermes Agent) as fallback.  The control
 * file is expected at:
 *
 *   <MCPTAP_FB_DIR>/<session_id>/blocked_files
 *
 * where MCPTAP_FB_DIR defaults to /tmp/mcptap/per_session.
 *
 * For testing or standalone use, MCPTAP_FB_FILE can be set
 * directly to override the auto-constructed path.
 *
 * Blocked syscalls: open, openat, openat64, access, faccessat,
 * fopen, fopen64, stat, stat64, lstat, lstat64, __xstat, __xstat64,
 * __lxstat, __lxstat64, statx, readlink, readlinkat, realpath,
 * openat2 (Linux 5.6+, via exported wrapper symbol).
 *
 * Returns -1 / NULL with errno = EACCES for blocked paths.
 *
 * Process allowlist (MCPTAP_FB_PROCESS_ALLOWLIST):
 *   Colon-separated list of process names (as reported by /proc/self/comm)
 *   that bypass all blocklist checks. Default: "git:ssh". Set to empty
 *   string to disable the allowlist entirely.
 *
 * 2. HTTP session-id header injection
 *
 * When the host process connects to the MCPTap proxy on localhost, this
 * layer injects a "session-id" HTTP header carrying the session ID into
 * outgoing HTTP requests. The MCPTap proxy listens for this header to
 * attribute requests to the correct session.
 *
 * The MCPTap listen address is discovered from /proc/<pid>/environ where
 * <pid> is read from <MCPTAP_FB_DIR>/../proxy.pid (written by proxy.py
 * at startup). The address is cached and refreshed every 5 seconds.
 *
 * Injected syscalls: connect (fd tracking), send, write (header injection),
 * close, dup, dup2 (fd table maintenance).
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/syscall.h>
#include <unistd.h>

/* ----------------------------------------------------------------------- */
/* openat2 support (Linux 5.6+)                                            */
/*                                                                         */
/* struct open_how and __NR_openat2 may not be available in older          */
/* kernel headers, so we define them conditionally here.                   */
/* We export an openat2() wrapper symbol so that programs linking          */
/* against it are intercepted. Raw syscall(__NR_openat2, ...) is not       */
/* intercepted because wrapping the generic syscall() function breaks      */
/* Electron/Node.js (which rely on syscall() for getrandom, futex, etc.).  */
/* ----------------------------------------------------------------------- */

#ifndef __NR_openat2
    #define __NR_openat2 437
#endif

/* Minimal definition of struct open_how if the kernel headers
 * don't provide it (matches the kernel ABI exactly). */
struct mcptap_open_how {
    unsigned long long flags;
    unsigned long long mode;
    unsigned long long resolve;
};

/* ----------------------------------------------------------------------- */
/* Function pointer typedefs                                               */
/* ----------------------------------------------------------------------- */

typedef int (*open_fn)(const char *, int, ...);
typedef int (*openat_fn)(int, const char *, int, ...);
typedef int (*access_fn)(const char *, int);
typedef int (*faccessat_fn)(int, const char *, int, int);
typedef FILE *(*fopen_fn)(const char *, const char *);
typedef int (*stat_fn)(const char *, struct stat *);
typedef int (*lstat_fn)(const char *, struct stat *);
typedef int (*statx_fn)(int, const char *, int, unsigned int, struct statx *);
typedef ssize_t (*readlink_fn)(const char *, char *, size_t);
typedef ssize_t (*readlinkat_fn)(int, const char *, char *, size_t);
typedef char *(*realpath_fn)(const char *, char *);

static open_fn real_open = NULL;
static open_fn real_open64 = NULL;
static openat_fn real_openat = NULL;
static openat_fn real_openat64 = NULL;
static access_fn real_access = NULL;
static faccessat_fn real_faccessat = NULL;
static fopen_fn real_fopen = NULL;
static fopen_fn real_fopen64 = NULL;

static void init_real_funcs(void) {
    if (!real_open)    real_open    = (open_fn)    dlsym(RTLD_NEXT, "open");
    if (!real_open64)  real_open64  = (open_fn)    dlsym(RTLD_NEXT, "open64");
    if (!real_openat)  real_openat  = (openat_fn)  dlsym(RTLD_NEXT, "openat");
    if (!real_openat64)real_openat64= (openat_fn)  dlsym(RTLD_NEXT, "openat64");
    if (!real_access)  real_access  = (access_fn)  dlsym(RTLD_NEXT, "access");
    if (!real_faccessat)real_faccessat=(faccessat_fn)dlsym(RTLD_NEXT, "faccessat");
    if (!real_fopen)   real_fopen   = (fopen_fn)   dlsym(RTLD_NEXT, "fopen");
    if (!real_fopen64) real_fopen64 = (fopen_fn)   dlsym(RTLD_NEXT, "fopen64");
}

/* ----------------------------------------------------------------------- */
/* Blocklist management                                                    */
/* ----------------------------------------------------------------------- */

static char **blocked_paths = NULL;
static int blocked_count = 0;
static int blocked_initialized = 0;

/* Re-entrancy guard: when set, interceptors skip blocklist checks so that
 * the library's own file operations (reading the control file) are not
 * intercepted. */
static int _loading_blocklist = 0;

/* Re-entrancy guard: when set, interceptors skip blocklist checks so that
 * calls to realpath() from inside is_path_blocked() do not recurse back
 * through our own realpath() interceptor. */
static int _checking = 0;

/* ----------------------------------------------------------------------- */
/* Process allowlist                                                       */
/*                                                                         */
/* Processes listed in MCPTAP_FB_PROCESS_ALLOWLIST (colon-separated)       */
/* bypass all blocklist checks. This allows trusted system tools like      */
/* git and ssh to access blocked paths (e.g. ~/.ssh/id_rsa for git push)  */
/* while still blocking direct reads by the model (cat, head, etc.).      */
/*                                                                         */
/* The process name is read from /proc/self/comm (Linux) which gives the   */
/* kernel's view of the current process name (truncated to 15 chars).     */
/* Default allowlist: "git:ssh". Set MCPTAP_FB_PROCESS_ALLOWLIST="" to    */
/* disable.                                                               */
/* ----------------------------------------------------------------------- */

static char _current_comm[16] = {0};
static int _comm_initialized = 0;

static const char *get_current_comm(void) {
    if (_comm_initialized)
        return _current_comm;

    _comm_initialized = 1;

    int fd = -1;
    /* Use raw syscall to avoid recursion through our own open() interceptor. */
    _checking = 1;
    fd = (int)syscall(SYS_openat, AT_FDCWD, "/proc/self/comm", O_RDONLY, 0);
    _checking = 0;
    if (fd >= 0) {
        ssize_t n = read(fd, _current_comm, sizeof(_current_comm) - 1);
        close(fd);
        if (n > 0) {
            /* Strip trailing newline. */
            if (_current_comm[n - 1] == '\n')
                n--;
            _current_comm[n] = '\0';
        }
    }
    return _current_comm;
}

static int is_process_allowed(void) {
    const char *allowlist = getenv("MCPTAP_FB_PROCESS_ALLOWLIST");
    /* Default allowlist: git and ssh. */
    if (!allowlist)
        allowlist = "git:ssh";
    /* Empty string disables the allowlist entirely. */
    if (!*allowlist)
        return 0;

    const char *comm = get_current_comm();
    if (!comm || !*comm)
        return 0;

    const char *p = allowlist;
    while (*p) {
        const char *colon = strchr(p, ':');
        size_t len = colon ? (size_t)(colon - p) : strlen(p);
        if (len > 0 && strlen(comm) == len && strncmp(comm, p, len) == 0)
            return 1;
        if (!colon)
            break;
        p = colon + 1;
    }
    return 0;
}

/* Build the path to the per-session control file.
 * Uses MCPTAP_FB_FILE if set (for testing / override).
 * Otherwise constructs: <MCPTAP_FB_DIR>/<session_id>/blocked_files
 * where session_id is read from CODEX_THREAD_ID (Codex CLI) or
 * HERMES_SESSION_ID (Hermes Agent) as fallback.
 * Returns 0 on success, -1 if no session ID is available. */
static int build_control_path(char *buf, size_t buf_size) {
    /* Explicit override (for tests / standalone use) */
    const char *explicit = getenv("MCPTAP_FB_FILE");
    if (explicit && *explicit) {
        snprintf(buf, buf_size, "%s", explicit);
        return 0;
    }

    const char *session_id = getenv("CODEX_THREAD_ID");
    if (!session_id || !*session_id)
        session_id = getenv("HERMES_SESSION_ID");
    if (!session_id || !*session_id)
        return -1;

    const char *base_dir = getenv("MCPTAP_FB_DIR");
    if (!base_dir || !*base_dir)
        base_dir = "/tmp/mcptap/per_session";

    snprintf(buf, buf_size, "%s/%s/blocked_files", base_dir, session_id);
    return 0;
}

static void expand_tilde(const char *src, char *dst, size_t dst_size) {
    if (src[0] == '~') {
        const char *home = getenv("HOME");
        if (home) {
            size_t home_len = strlen(home);
            size_t rest_len = strlen(src + 1);
            if (home_len + rest_len < dst_size) {
                memcpy(dst, home, home_len);
                memcpy(dst + home_len, src + 1, rest_len + 1);
                return;
            }
        }
    }
    snprintf(dst, dst_size, "%s", src);
}

static void load_blocklist(void) {
    blocked_initialized = 1;

    /* Free previous list */
    if (blocked_paths) {
        for (int i = 0; i < blocked_count; i++)
            free(blocked_paths[i]);
        free(blocked_paths);
        blocked_paths = NULL;
        blocked_count = 0;
    }

    char ctrl_path[8192];
    if (build_control_path(ctrl_path, sizeof(ctrl_path)) != 0)
        return;

    /* Use real_fopen to avoid recursion through our own fopen interceptor. */
    fopen_fn real_fopen_local = (fopen_fn)dlsym(RTLD_NEXT, "fopen");
    if (!real_fopen_local)
        return;

    _loading_blocklist = 1;
    FILE *f = real_fopen_local(ctrl_path, "r");
    _loading_blocklist = 0;
    if (!f)
        return;

    char line[4096];
    /* Count lines first */
    int count = 0;
    while (fgets(line, sizeof(line), f)) {
        /* Strip whitespace */
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0' || *p == '\n' || *p == '#') continue;
        count++;
    }
    if (count == 0) {
        fclose(f);
        return;
    }

    blocked_paths = calloc(count, sizeof(char *));
    if (!blocked_paths) {
        fclose(f);
        return;
    }

    rewind(f);
    while (fgets(line, sizeof(line), f) && blocked_count < count) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0' || *p == '\n' || *p == '#') continue;
        /* Strip trailing whitespace/newline */
        size_t len = strlen(p);
        while (len > 0 && (p[len-1] == '\n' || p[len-1] == '\r' ||
               p[len-1] == ' ' || p[len-1] == '\t'))
            p[--len] = '\0';
        if (len == 0) continue;

        char expanded[8192];
        expand_tilde(p, expanded, sizeof(expanded));

        /* Normalize the blocklist entry to an absolute, symlink-resolved
         * form so it matches the realpath-normalized candidate path checked
         * in is_path_blocked(). Falls back to the expanded (tilde-resolved)
         * form if realpath() fails (e.g. the blocked file does not exist
         * yet at load time). */
        char normalized[8192];
        realpath_fn real_realpath = (realpath_fn)dlsym(RTLD_NEXT, "realpath");
        _loading_blocklist = 1;
        char *rp = real_realpath(expanded, normalized);
        _loading_blocklist = 0;
        const char *to_store = rp ? normalized : expanded;
        blocked_paths[blocked_count] = strdup(to_store);
        if (blocked_paths[blocked_count])
            blocked_count++;
    }
    fclose(f);
}

static int is_path_blocked(const char *path) {
    if (!path)
        return 0;

    /* Skip checks while loading the blocklist or already inside a check to
     * prevent recursion. */
    if (_loading_blocklist || _checking)
        return 0;

    /* Allowlisted processes bypass all blocklist checks. */
    if (is_process_allowed())
        return 0;

    if (!blocked_initialized)
        load_blocklist();

    if (blocked_count == 0)
        return 0;

    /* Normalize the candidate path to an absolute, symlink-resolved form so
     * that "./", "..", and symlink aliases cannot bypass the blocklist.
     * The blocklist entries are likewise normalized at load time. */
    const char *candidate = path;
    char expanded[8192];
    char resolved[8192];

    if (path[0] == '~') {
        expand_tilde(path, expanded, sizeof(expanded));
        candidate = expanded;
    } else if (path[0] != '/') {
        char cwd[4096];
        if (getcwd(cwd, sizeof(cwd))) {
            snprintf(expanded, sizeof(expanded), "%s/%s", cwd, path);
            candidate = expanded;
        }
    }

    /* Resolve symlinks using the REAL realpath, bypassing our own
     * interceptor via the guard. If it fails (e.g. path does not exist
     * yet), fall back to the expanded absolute form. */
    _checking = 1;
    realpath_fn real_realpath = (realpath_fn)dlsym(RTLD_NEXT, "realpath");
    char *rp = real_realpath(candidate, resolved);
    _checking = 0;
    if (rp)
        candidate = resolved;

    for (int i = 0; i < blocked_count; i++) {
        if (!blocked_paths[i]) continue;
        if (strcmp(candidate, blocked_paths[i]) == 0)
            return 1;
    }
    return 0;
}

/* ----------------------------------------------------------------------- */
/* Escalator + interpreter payload detection (surgical)                   */
/*                                                                         */
/* A setuid binary (sudo, su, doas, pkexec, runuser, gksu, ...) starts     */
/* its child WITHOUT our LD_PRELOAD (glibc drops untrusted preloads for   */
/* setuid).  The exec* argv-scan via is_path_blocked() already catches     */
/* direct readers such as `sudo cat <blocked>`, but a child invoked as     */
/* an interpreter (e.g. `sudo bash -c 'cat <blocked>'`) receives the       */
/* blocked path as part of a single string argument that is NOT a path     */
/* itself, so the path-scan does not match.                                */
/*                                                                         */
/* This layer blocks such escapes surgically: only when argv[0] is an     */
/* escalator AND a later argv element is an interpreter AND the remaining  */
/* argv (payload) contains a reference (substring match, after tilde      */
/* expansion) to a path on the blocklist.  Legit uses like                */
/* `sudo bash -c 'systemctl restart nginx'` are NOT affected because the  */
/* payload contains no blocked-path reference.                            */
/*                                                                         */
/* Lists are configurable via environment:                                 */
/*   MCPTAP_FB_ESCALATORS    (colon-separated, overrides defaults)        */
/*   MCPTAP_FB_INTERPRETERS  (colon-separated, overrides defaults)        */
/*   MCPTAP_FB_DISABLE_ESCALATOR_CHECK=1  disables this layer entirely    */
/* ----------------------------------------------------------------------- */

static const char *DEFAULT_ESCALATORS[] = {
    "sudo", "su", "doas", "pkexec", "runuser", "gksu", "gksudo",
    "sudoedit", "pkexec-check", "su-to-root", NULL
};

static const char *DEFAULT_INTERPRETERS[] = {
    "bash", "sh", "dash", "zsh", "ksh", "ash", "csh", "tcsh", "fish",
    "busybox", "python", "python2", "python3", "perl", "ruby", "node",
    "nodejs", "php", "awk", "gawk", "mawk", "sed", "xargs", "tee", "env",
    "dd", "make", "nice", "nohup", "strace", "ltrace", "exec", "command",
    "eval", "source", ">", NULL
};

/* basename without modifying input; returns pointer into `path` or path. */
static const char *fb_basename(const char *path) {
    if (!path) return path;
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static int fb_in_list(const char *name, const char *const *defaults,
                      const char *env_var) {
    if (!name || !*name)
        return 0;

    const char *env_list = getenv(env_var);
    if (env_list && *env_list) {
        /* env overrides defaults; colon-separated. Empty entry disables. */
        const char *p = env_list;
        while (*p) {
            const char *colon = strchr(p, ':');
            size_t len = colon ? (size_t)(colon - p) : strlen(p);
            if (len > 0 && strlen(name) == len && strncmp(name, p, len) == 0)
                return 1;
            if (!colon) break;
            p = colon + 1;
        }
        return 0;
    }

    for (int i = 0; defaults[i]; i++) {
        if (strcmp(name, defaults[i]) == 0)
            return 1;
    }
    return 0;
}

static int is_escalator(const char *argv0) {
    if (getenv("MCPTAP_FB_DISABLE_ESCALATOR_CHECK") &&
        getenv("MCPTAP_FB_DISABLE_ESCALATOR_CHECK")[0] == '1')
        return 0;
    return fb_in_list(fb_basename(argv0), DEFAULT_ESCALATORS,
                      "MCPTAP_FB_ESCALATORS");
}

static int is_interpreter(const char *arg) {
    return fb_in_list(fb_basename(arg), DEFAULT_INTERPRETERS,
                      "MCPTAP_FB_INTERPRETERS");
}

/* Check if any blocklist path appears as a substring of `haystack`.
 *
 * `haystack` is typically a concatenated interpreter payload like
 * "cat ~/.fzf-history" or "F=~/.secret; cat \"$F\"".  The blocklist stores
 * entries in their expanded absolute form (e.g. "/home/user/.fzf-history"),
 * so we expand every `~` token inside `haystack` to $HOME before matching,
 * not only a leading `~`.  This catches payloads that reference the blocked
 * path via `~` even when the `~` is not at the start of the string.
 *
 * As a fallback we also match the raw (unexpanded) haystack, in case the
 * blocklist entry was stored in its ~-form (realpath failed at load time). */
static int payload_contains_blocked_path(const char *haystack) {
    if (!haystack || !*haystack)
        return 0;

    if (!blocked_initialized)
        load_blocklist();
    if (blocked_count == 0)
        return 0;

    /* Build an expanded copy of haystack: replace every standalone `~`
     * (i.e. `~` at start or preceded by whitespace/`;`/`=`/`(`/`{`) with
     * $HOME, so payloads like "cat ~/.fzf-history" or "F=~/.secret" get the
     * home path substituted.  Only `~/` and `~` followed by end-of-string
     * are treated as home-reference; `~user` is left untouched. */
    const char *home = getenv("HOME");
    size_t home_len = home ? strlen(home) : 0;
    char expanded[16384];
    size_t off = 0;
    const char *p = haystack;
    while (*p && off < sizeof(expanded) - 1) {
        if (*p == '~' && home && home_len > 0 &&
            (p == haystack ||
             p[-1] == ' ' || p[-1] == '\t' ||
             p[-1] == ';' || p[-1] == '=' ||
             p[-1] == '(' || p[-1] == '{' ||
             p[-1] == '\n')) {
            /* Only expand `~/` or `~` at end; leave `~user` alone. */
            char next = p[1];
            if (next == '/' || next == '\0' || next == ' ' || next == '\t' ||
                next == ';' || next == '"' || next == '\'') {
                if (off + home_len < sizeof(expanded) - 1) {
                    memcpy(expanded + off, home, home_len);
                    off += home_len;
                }
                p++;
                continue;
            }
        }
        expanded[off++] = *p++;
    }
    expanded[off] = '\0';

    /* Match against expanded form. */
    for (int i = 0; i < blocked_count; i++) {
        if (!blocked_paths[i]) continue;
        if (strstr(expanded, blocked_paths[i]))
            return 1;
        /* Fallback: also match against the raw haystack in case the
         * blocklist entry was stored in ~-form (realpath failed at load). */
        if (strstr(haystack, blocked_paths[i]))
            return 1;
    }
    return 0;
}

/* Surgical escalator+interpreter+payload detection.
 *
 * Returns 1 if the argv should be blocked:
 *   - argv[0] is an escalator (sudo, su, ...), AND
 *   - some argv[i] (i>=1) is an interpreter (bash, python, ...) OR a plain
 *     path argument that itself is/contains a blocked path, AND
 *   - either the interpreter's payload (concat of argv after the
 *     interpreter) contains a blocked path as a substring, OR any argv[i>=1]
 *     contains a blocked path as a substring.
 *
 * This blocks e.g.:
 *   sudo bash -c 'cat ~/.fzf-history'
 *   sudo python3 -c "print(open('/home/u/.secret').read())"
 *   sudo env /home/u/.secret bash
 *   sudo bash -c 'F=~/.fzf-history; cat "$F"'
 * while leaving intact:
 *   sudo bash -c 'systemctl restart nginx'
 *   sudo service firebird3.0 start
 *   sudo cat /non-blocked/file
 */
static int is_escalator_interpreter_payload_blocked(char *const argv[]) {
    if (!argv || !argv[0])
        return 0;

    if (!is_escalator(argv[0]))
        return 0;

    /* After an escalator, scan the rest of argv.  We block if EITHER:
     * (a) any argv[i>=1] contains a blocked path as a substring (covers
     *     `sudo env /home/u/.secret bash` and direct path leaks in args),
     * OR (b) we encounter an interpreter and its payload (concat of args
     *     after it) contains a blocked path as a substring (covers
     *     `sudo bash -c 'cat ~/.fzf-history'`). */
    int found_interpreter = 0;

    /* First pass: check for substring-match of blocked paths in any arg. */
    for (int i = 1; argv[i]; i++) {
        if (payload_contains_blocked_path(argv[i]))
            return 1;
    }

    /* Second pass: locate an interpreter and check its concatenated payload. */
    for (int i = 1; argv[i]; i++) {
        if (is_interpreter(argv[i])) {
            found_interpreter = 1;
            /* Concatenate everything after the interpreter into one string
             * and run a substring check. */
            char payload[16384];
            size_t off = 0;
            for (int j = i + 1; argv[j] && off < sizeof(payload) - 2; j++) {
                size_t len = strlen(argv[j]);
                if (off > 0 && off < sizeof(payload) - 1) {
                    payload[off++] = ' ';
                }
                if (off + len >= sizeof(payload) - 1)
                    len = sizeof(payload) - 1 - off;
                memcpy(payload + off, argv[j], len);
                off += len;
            }
            payload[off] = '\0';
            if (off > 0 && payload_contains_blocked_path(payload))
                return 1;
            /* If the interpreter is reached but its payload is empty (e.g.
             * `sudo bash` with no further args), we do not block on that
             * alone -- a bare interactive `sudo bash` is unusual for an
             * agent and not a substring-leak. */
        }
    }

    (void)found_interpreter;
    return 0;
}

/* Check whether any argument in argv looks like a path matching the file
 * blocklist. This is used by the exec* interceptors to stop a child process
 * (e.g. sudo, cp, dd) from reading a blocked file before it is spawned in a
 * context where LD_PRELOAD would no longer be honored (setuid binaries).
 *
 * Each argv element is treated as a potential filesystem path: tilde is
 * expanded, relative paths are resolved against CWD, and symlinks are
 * resolved. Non-path arguments simply do not match and are skipped. */
static int is_argv_blocked(char *const argv[]) {
    if (!argv)
        return 0;

    /* Allowlisted processes bypass all blocklist checks. */
    if (is_process_allowed())
        return 0;

    /* Direct path scan: blocks `sudo cat <blocked>`, `sudo cp <blocked> dst`,
     * etc., where an argv element IS the blocked path. */
    for (int i = 1; argv[i]; i++) {
        if (is_path_blocked(argv[i]))
            return 1;
    }

    /* Surgical escalator+interpreter+payload scan: blocks
     * `sudo bash -c 'cat <blocked>'`, `sudo python3 -c "..."`, etc., where
     * the blocked path is embedded as a substring of an interpreter's
     * payload string rather than as a standalone argv element. */
    if (is_escalator_interpreter_payload_blocked(argv))
        return 1;

    return 0;
}

/* Reload control file (call before each check if file may have changed) */
static void maybe_reload(void) {
    static time_t last_check = 0;
    time_t now = time(NULL);
    if (now - last_check > 1) {
        last_check = now;
        load_blocklist();
    }
}

/* ----------------------------------------------------------------------- */
/* Interceptors                                                            */
/* ----------------------------------------------------------------------- */

/* --- open / open64 --- */
int open(const char *pathname, int flags, ...) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode_t mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_open(pathname, flags, mode);
    }
    return real_open(pathname, flags);
}

int open64(const char *pathname, int flags, ...) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode_t mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_open64(pathname, flags, mode);
    }
    return real_open64(pathname, flags);
}

/* --- openat / openat64 --- */
int openat(int dirfd, const char *pathname, int flags, ...) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode_t mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_openat(dirfd, pathname, flags, mode);
    }
    return real_openat(dirfd, pathname, flags);
}

int openat64(int dirfd, const char *pathname, int flags, ...) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode_t mode = va_arg(ap, mode_t);
        va_end(ap);
        return real_openat64(dirfd, pathname, flags, mode);
    }
    return real_openat64(dirfd, pathname, flags);
}

/* --- access / faccessat --- */
int access(const char *pathname, int mode) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    return real_access(pathname, mode);
}

int faccessat(int dirfd, const char *pathname, int mode, int flags) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    return real_faccessat(dirfd, pathname, mode, flags);
}

/* --- fopen / fopen64 --- */
FILE *fopen(const char *pathname, const char *mode) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return NULL;
    }
    return real_fopen(pathname, mode);
}

FILE *fopen64(const char *pathname, const char *mode) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return NULL;
    }
    return real_fopen64(pathname, mode);
}

/* --- stat family --- */
int __xstat(int ver, const char *path, struct stat *buf) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(path)) {
        errno = EACCES;
        return -1;
    }
    typedef int (*fn_t)(int, const char *, struct stat *);
    fn_t real = (fn_t)dlsym(RTLD_NEXT, "__xstat");
    return real(ver, path, buf);
}

int __xstat64(int ver, const char *path, struct stat64 *buf) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(path)) {
        errno = EACCES;
        return -1;
    }
    typedef int (*fn_t)(int, const char *, struct stat64 *);
    fn_t real = (fn_t)dlsym(RTLD_NEXT, "__xstat64");
    return real(ver, path, buf);
}

int __lxstat(int ver, const char *path, struct stat *buf) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(path)) {
        errno = EACCES;
        return -1;
    }
    typedef int (*fn_t)(int, const char *, struct stat *);
    fn_t real = (fn_t)dlsym(RTLD_NEXT, "__lxstat");
    return real(ver, path, buf);
}

int __lxstat64(int ver, const char *path, struct stat64 *buf) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(path)) {
        errno = EACCES;
        return -1;
    }
    typedef int (*fn_t)(int, const char *, struct stat64 *);
    fn_t real = (fn_t)dlsym(RTLD_NEXT, "__lxstat64");
    return real(ver, path, buf);
}

/* --- statx (glibc >= 2.28) --- */
int statx(int dirfd, const char *pathname, int flags, unsigned int mask,
          struct statx *buf) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    return real_openat ? ((statx_fn)dlsym(RTLD_NEXT, "statx"))(dirfd, pathname, flags, mask, buf) : -1;
}

/* --- readlink / readlinkat --- */
ssize_t readlink(const char *pathname, char *buf, size_t bufsiz) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    readlink_fn real = (readlink_fn)dlsym(RTLD_NEXT, "readlink");
    return real(pathname, buf, bufsiz);
}

ssize_t readlinkat(int dirfd, const char *pathname, char *buf, size_t bufsiz) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    readlinkat_fn real = (readlinkat_fn)dlsym(RTLD_NEXT, "readlinkat");
    return real(dirfd, pathname, buf, bufsiz);
}

/* --- realpath --- */
char *realpath(const char *path, char *resolved) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(path)) {
        errno = EACCES;
        return NULL;
    }
    realpath_fn real = (realpath_fn)dlsym(RTLD_NEXT, "realpath");
    return real(path, resolved);
}

/* ----------------------------------------------------------------------- */
/* openat2 interceptor (Linux 5.6+)                                        */
/*                                                                         */
/* We export an openat2() wrapper that delegates to the kernel via         */
/* the raw syscall number. Programs linking against openat2() are          */
/* intercepted. Raw syscall(__NR_openat2, ...) is not intercepted          */
/* because wrapping the generic syscall() function breaks Electron/Node.   */
/* ----------------------------------------------------------------------- */

int openat2(int dirfd, const char *pathname, struct mcptap_open_how *how,
            size_t size) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    return (int)syscall(__NR_openat2, dirfd, pathname, how, size);
}

/* ----------------------------------------------------------------------- */
/* exec* and posix_spawn* interceptors                                     */
/*                                                                         */
/* A child process started via a setuid binary (sudo, su, pkexec, ...)     */
/* runs WITHOUT our LD_PRELOAD library, because glibc refuses to load      */
/* LD_PRELOAD libraries from untrusted directories for setuid programs.    */
/* That means any blocked file could be read by spawning e.g.              */
/* "sudo cat /path/to/blocked".                                            */
/*                                                                         */
/* To close that escape vector we intercept execve/execvpe/execvp/         */
/* posix_spawn/posix_spawnp here, in the parent process (where the         */
/* library is still active), and scan argv for any argument that resolves   */
/* to a blocked path. If found, we refuse the exec with EACCES.            */
/*                                                                         */
/* This blocks "sudo cat ~/.fzf-history" without disabling sudo for        */
/* unrelated commands (e.g. "sudo service firebird start").               */
/* ----------------------------------------------------------------------- */

typedef int (*execve_fn)(const char *, char *const[], char *const[]);
typedef int (*execvp_fn)(const char *, char *const[]);
typedef int (*execvpe_fn)(const char *, char *const[], char *const[]);
typedef int (*posix_spawn_fn)(pid_t *, const char *,
                              const void *, const void *,
                              char *const[], char *const[]);
typedef int (*posix_spawnp_fn)(pid_t *, const char *,
                               const void *, const void *,
                               char *const[], char *const[]);

static execve_fn real_execve = NULL;
static execvp_fn real_execvp = NULL;
static execvpe_fn real_execvpe = NULL;
static posix_spawn_fn real_posix_spawn = NULL;
static posix_spawnp_fn real_posix_spawnp = NULL;

static void init_exec_funcs(void) {
    if (!real_execve)       real_execve       = (execve_fn)dlsym(RTLD_NEXT, "execve");
    if (!real_execvp)       real_execvp       = (execvp_fn)dlsym(RTLD_NEXT, "execvp");
    if (!real_execvpe)      real_execvpe      = (execvpe_fn)dlsym(RTLD_NEXT, "execvpe");
    if (!real_posix_spawn)  real_posix_spawn  = (posix_spawn_fn)dlsym(RTLD_NEXT, "posix_spawn");
    if (!real_posix_spawnp) real_posix_spawnp = (posix_spawnp_fn)dlsym(RTLD_NEXT, "posix_spawnp");
}

int execve(const char *pathname, char *const argv[], char *const envp[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    return real_execve(pathname, argv, envp);
}

int execv(const char *pathname, char *const argv[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    /* execv has no envp argument; pass environ. */
    extern char **environ;
    return real_execve(pathname, argv, environ);
}

int execvp(const char *file, char *const argv[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    return real_execvp(file, argv);
}

int execvpe(const char *file, char *const argv[], char *const envp[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    return real_execvpe(file, argv, envp);
}

int posix_spawn(pid_t *pid, const char *path,
                const void *file_actions, const void *attrp,
                char *const argv[], char *const envp[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    return real_posix_spawn(pid, path, file_actions, attrp, argv, envp);
}

int posix_spawnp(pid_t *pid, const char *file,
                 const void *file_actions, const void *attrp,
                 char *const argv[], char *const envp[]) {
    init_exec_funcs();
    maybe_reload();
    if (is_argv_blocked(argv)) {
        errno = EACCES;
        return -1;
    }
    return real_posix_spawnp(pid, file, file_actions, attrp, argv, envp);
}

/* ----------------------------------------------------------------------- */
/* HTTP session-id header injection                                         */
/*                                                                         */
/* When the host process (Hermes Agent) makes HTTP requests to the MCPTap   */
/* proxy on localhost, this layer injects a "session-id" HTTP header        */
/* carrying HERMES_SESSION_ID so MCPTap can attribute requests to the       */
/* correct session.                                                        */
/*                                                                         */
/* Mechanism:                                                              */
/*   1. connect() — when the peer matches the MCPTap listen address        */
/*      (discovered from /proc/<pid>/environ via the PID file), the fd is  */
/*      marked in a tracking table.                                        */
/*   2. send()/write() — if the fd is tracked and the buffer looks like     */
/*      an HTTP request header block (contains "\r\n\r\n"), inject          */
/*      "session-id: <id>\r\n" before the terminating blank line.          */
/*   3. close()/dup()/dup2() — maintain the fd tracking table.             */
/*                                                                         */
/* The MCPTap address is read from /proc/<mcptap_pid>/environ where        */
/* mcptap_pid is read from <per_session_dir>/../proxy.pid. This file is    */
/* written by proxy.py at startup. The address is cached and refreshed     */
/* every 5 seconds (like maybe_reload).                                    */
/* ----------------------------------------------------------------------- */

#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <pthread.h>

/* --- fd tracking table --- */

#define MCPTAP_MAX_FD 65536

/* Compact bitmap: bit set = fd is a connection to MCPTap proxy. */
static unsigned char _fd_bitmap[MCPTAP_MAX_FD / 8];
static pthread_mutex_t _fd_mutex = PTHREAD_MUTEX_INITIALIZER;

static void _fd_set(int fd) {
    if (fd < 0 || fd >= MCPTAP_MAX_FD)
        return;
    pthread_mutex_lock(&_fd_mutex);
    _fd_bitmap[fd / 8] |= (1 << (fd % 8));
    pthread_mutex_unlock(&_fd_mutex);
}

static void _fd_clear(int fd) {
    if (fd < 0 || fd >= MCPTAP_MAX_FD)
        return;
    pthread_mutex_lock(&_fd_mutex);
    _fd_bitmap[fd / 8] &= ~(1 << (fd % 8));
    pthread_mutex_unlock(&_fd_mutex);
}

static int _fd_is_set(int fd) {
    if (fd < 0 || fd >= MCPTAP_MAX_FD)
        return 0;
    pthread_mutex_lock(&_fd_mutex);
    int val = (_fd_bitmap[fd / 8] >> (fd % 8)) & 1;
    pthread_mutex_unlock(&_fd_mutex);
    return val;
}

/* --- MCPTap address discovery --- */

static char _mcptap_host[64] = "127.0.0.1";
static int  _mcptap_port = 8787;
static int  _mcptap_addr_loaded = 0;
static time_t _mcptap_addr_last_check = 0;
static pthread_mutex_t _mcptap_addr_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Read MCP_TAP_LISTEN_HOST and MCP_TAP_LISTEN_PORT from
 * /proc/<pid>/environ, where <pid> is read from the proxy.pid file.
 * The environ file is a sequence of NUL-terminated KEY=VALUE strings. */
static void _load_mcptap_addr(void) {
    char pid_path[512];
    const char *per_session = getenv("MCPTAP_FB_DIR");
    if (!per_session || !*per_session)
        per_session = "/tmp/mcptap/per_session";

    /* proxy.pid lives one directory above per_session_dir:
     *   /tmp/mcptap/per_session  ->  /tmp/mcptap/proxy.pid */
    char pid_file[512];
    snprintf(pid_file, sizeof(pid_file), "%s/../proxy.pid", per_session);

    /* Read PID using raw syscall to bypass our own open() interceptor. */
    int _checking_save = _checking;
    _checking = 1;
    int pfd = (int)syscall(SYS_openat, AT_FDCWD, pid_file, O_RDONLY, 0);
    _checking = _checking_save;
    if (pfd < 0)
        return;

    char pid_buf[32];
    ssize_t pn = read(pfd, pid_buf, sizeof(pid_buf) - 1);
    close(pfd);
    if (pn <= 0)
        return;
    pid_buf[pn] = '\0';
    /* Strip whitespace */
    char *end = pid_buf + pn;
    while (end > pid_buf && (end[-1] == '\n' || end[-1] == '\r' ||
           end[-1] == ' ' || end[-1] == '\t'))
        *--end = '\0';
    int mcptap_pid = atoi(pid_buf);
    if (mcptap_pid <= 0)
        return;

    snprintf(pid_path, sizeof(pid_path), "/proc/%d/environ", mcptap_pid);

    _checking_save = _checking;
    _checking = 1;
    int efd = (int)syscall(SYS_openat, AT_FDCWD, pid_path, O_RDONLY, 0);
    _checking = _checking_save;
    if (efd < 0)
        return;

    /* environ can be up to the process's arg_max, but a single page is
     * usually enough for the keys we need. */
    char env_buf[8192];
    ssize_t en = read(efd, env_buf, sizeof(env_buf) - 1);
    close(efd);
    if (en <= 0)
        return;
    env_buf[en] = '\0';

    /* Walk NUL-separated KEY=VALUE entries. */
    char host_buf[64] = "";
    char port_buf[16] = "";
    char *p = env_buf;
    char *env_end = env_buf + en;
    while (p < env_end) {
        size_t len = strnlen(p, env_end - p);
        if (len == 0)
            break;
        if (strncmp(p, "MCP_TAP_LISTEN_HOST=", 20) == 0 && len > 20) {
            size_t copy = len - 20;
            if (copy >= sizeof(host_buf))
                copy = sizeof(host_buf) - 1;
            memcpy(host_buf, p + 20, copy);
            host_buf[copy] = '\0';
        } else if (strncmp(p, "MCP_TAP_LISTEN_PORT=", 20) == 0 && len > 20) {
            size_t copy = len - 20;
            if (copy >= sizeof(port_buf))
                copy = sizeof(port_buf) - 1;
            memcpy(port_buf, p + 20, copy);
            port_buf[copy] = '\0';
        }
        p += len + 1;
    }

    if (*host_buf)
        snprintf(_mcptap_host, sizeof(_mcptap_host), "%s", host_buf);
    if (*port_buf) {
        int port = atoi(port_buf);
        if (port > 0)
            _mcptap_port = port;
    }
    _mcptap_addr_loaded = 1;
}

static void _maybe_reload_mcptap_addr(void) {
    time_t now = time(NULL);
    pthread_mutex_lock(&_mcptap_addr_mutex);
    if (!_mcptap_addr_loaded || now - _mcptap_addr_last_check > 5) {
        _mcptap_addr_last_check = now;
        _load_mcptap_addr();
    }
    pthread_mutex_unlock(&_mcptap_addr_mutex);
}

/* Check if a sockaddr matches the MCPTap proxy listen address. */
static int _is_mcptap_peer(const struct sockaddr *addr, socklen_t addrlen) {
    (void)addrlen;
    if (!addr)
        return 0;
    _maybe_reload_mcptap_addr();

    if (addr->sa_family == AF_INET) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)addr;
        if (sin->sin_port != htons((uint16_t)_mcptap_port))
            return 0;
        struct in_addr expected;
        if (inet_pton(AF_INET, _mcptap_host, &expected) != 1)
            return 0;
        return sin->sin_addr.s_addr == expected.s_addr;
    }
    /* AF_UNIX (unix socket) — not used by MCPTap, skip. */
    return 0;
}

/* --- HTTP header injection --- */

/* Check if buffer looks like an HTTP request header block (starts with an
 * HTTP method) and contains the header terminator "\r\n\r\n". Returns
 * pointer to the terminator, or NULL if not a header block. */
static char *_find_header_end(char *buf, size_t len) {
    /* Quick check: must start with an HTTP method verb. */
    if (len < 5)
        return NULL;
    /* Common HTTP methods: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS */
    if (strncmp(buf, "GET ", 4) != 0 &&
        strncmp(buf, "POST", 4) != 0 &&
        strncmp(buf, "PUT ", 4) != 0 &&
        strncmp(buf, "DELE", 4) != 0 &&
        strncmp(buf, "PATC", 4) != 0 &&
        strncmp(buf, "HEAD", 4) != 0 &&
        strncmp(buf, "OPTI", 4) != 0)
        return NULL;

    /* Find \r\n\r\n — the header terminator. */
    char *terminator = NULL;
    for (size_t i = 0; i + 3 < len; i++) {
        if (buf[i] == '\r' && buf[i+1] == '\n' &&
            buf[i+2] == '\r' && buf[i+3] == '\n') {
            terminator = buf + i;
            break;
        }
    }
    return terminator;
}

/* Case-insensitive substring search for "session-id:" within the header
 * block. If already present, we skip injection. */
static int _has_session_id_header(const char *buf, size_t len) {
    const char needle[] = "session-id:";
    const size_t needle_len = sizeof(needle) - 1;
    if (len < needle_len)
        return 0;
    for (size_t i = 0; i + needle_len <= len; i++) {
        if (strncasecmp(buf + i, needle, needle_len) == 0)
            return 1;
    }
    return 0;
}

/* Build the "session-id: <id>\r\n" injection line.
 * Returns the line length, or 0 if no session ID available. */
static size_t _build_session_header(char *out, size_t out_size) {
    const char *session_id = getenv("CODEX_THREAD_ID");
    if (!session_id || !*session_id)
        session_id = getenv("HERMES_SESSION_ID");
    if (!session_id || !*session_id)
        return 0;
    return (size_t)snprintf(out, out_size, "session-id: %s\r\n", session_id);
}

/* Inject session-id header into the buffer if needed.
 * Returns a newly malloc'd buffer (caller must free) and sets *out_len,
 * or returns NULL if no injection was performed (caller uses original).
 *
 * The injection point is after the last header's "\r\n" and before the
 * final "\r\n" that terminates the header block:
 *   "...last-header: val\r\n\r\nbody"
 *                 insert here ^
 * becomes:
 *   "...last-header: val\r\nsession-id: <id>\r\n\r\nbody"
 */
static char *_inject_session_header(const char *buf, size_t len, size_t *out_len) {
    char *header_end = _find_header_end((char *)buf, len);
    if (!header_end)
        return NULL;

    if (_has_session_id_header(buf, (size_t)(header_end - buf)))
        return NULL;

    char session_line[512];
    size_t session_line_len = _build_session_header(session_line, sizeof(session_line));
    if (session_line_len == 0)
        return NULL;

    /* header_end points to "\r\n\r\n". The last header line ends with "\r\n"
     * at header_end[0..1]. Insert the session-id line AFTER that "\r\n"
     * and BEFORE the final "\r\n" (header_end+2). */
    size_t prefix_len = (size_t)(header_end - buf) + 2; /* includes last header's \r\n */
    size_t tail_len = len - prefix_len; /* the final "\r\n" + body */
    size_t new_len = len + session_line_len;

    char *new_buf = (char *)malloc(new_len);
    if (!new_buf)
        return NULL;

    memcpy(new_buf, buf, prefix_len);
    memcpy(new_buf + prefix_len, session_line, session_line_len);
    memcpy(new_buf + prefix_len + session_line_len, buf + prefix_len, tail_len);

    *out_len = new_len;
    return new_buf;
}

/* --- Interceptors: connect, send, write, close, dup, dup2 --- */

typedef int (*connect_fn)(int, const struct sockaddr *, socklen_t);
typedef ssize_t (*send_fn)(int, const void *, size_t, int);
typedef ssize_t (*write_fn)(int, const void *, size_t);
typedef int (*close_fn)(int);
typedef int (*dup_fn)(int);
typedef int (*dup2_fn)(int, int);

static connect_fn real_connect = NULL;
static send_fn real_send = NULL;
static write_fn real_write = NULL;
static close_fn real_close = NULL;
static dup_fn real_dup = NULL;
static dup2_fn real_dup2 = NULL;

static void init_net_funcs(void) {
    if (!real_connect) real_connect = (connect_fn)dlsym(RTLD_NEXT, "connect");
    if (!real_send)    real_send    = (send_fn)dlsym(RTLD_NEXT, "send");
    if (!real_write)   real_write   = (write_fn)dlsym(RTLD_NEXT, "write");
    if (!real_close)   real_close   = (close_fn)dlsym(RTLD_NEXT, "close");
    if (!real_dup)     real_dup     = (dup_fn)dlsym(RTLD_NEXT, "dup");
    if (!real_dup2)    real_dup2    = (dup2_fn)dlsym(RTLD_NEXT, "dup2");
}

int connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
    init_net_funcs();
    /* Mark fd BEFORE calling real_connect — the peer address is known
     * at connect() time regardless of whether the connection succeeds. */
    if (_is_mcptap_peer(addr, addrlen))
        _fd_set(sockfd);
    return real_connect(sockfd, addr, addrlen);
}

ssize_t send(int fd, const void *buf, size_t len, int flags) {
    init_net_funcs();
    if (_fd_is_set(fd) && len > 0) {
        size_t new_len = 0;
        char *new_buf = _inject_session_header((const char *)buf, len, &new_len);
        if (new_buf) {
            ssize_t n = real_send(fd, new_buf, new_len, flags);
            free(new_buf);
            return n;
        }
    }
    return real_send(fd, buf, len, flags);
}

ssize_t write(int fd, const void *buf, size_t count) {
    init_net_funcs();
    if (_fd_is_set(fd) && count > 0) {
        size_t new_len = 0;
        char *new_buf = _inject_session_header((const char *)buf, count, &new_len);
        if (new_buf) {
            ssize_t n = real_write(fd, new_buf, new_len);
            free(new_buf);
            return n;
        }
    }
    return real_write(fd, buf, count);
}

int close(int fd) {
    init_net_funcs();
    _fd_clear(fd);
    return real_close(fd);
}

int dup(int oldfd) {
    init_net_funcs();
    int newfd = real_dup(oldfd);
    if (newfd >= 0 && _fd_is_set(oldfd))
        _fd_set(newfd);
    return newfd;
}

int dup2(int oldfd, int newfd) {
    init_net_funcs();
    int ret = real_dup2(oldfd, newfd);
    if (ret >= 0) {
        if (_fd_is_set(oldfd))
            _fd_set(newfd);
        else
            _fd_clear(newfd);
    }
    return ret;
}
