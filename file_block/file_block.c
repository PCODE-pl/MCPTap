/*
 * libmcptap_fileblock.so — LD_PRELOAD library that blocks file access
 * to paths listed in a per-session control file.
 *
 * The session ID is read from the CODEX_THREAD_ID environment variable
 * (set automatically by Codex CLI for all child processes).  The control
 * file is expected at:
 *
 *   <MCPTAP_BLOCKED_DIR>/<CODEX_THREAD_ID>/blocked_files
 *
 * where MCPTAP_BLOCKED_DIR defaults to /tmp/mcptap/per_session.
 *
 * For testing or standalone use, MCPTAP_BLOCKED_FILES_FILE can be set
 * directly to override the auto-constructed path.
 *
 * Blocked syscalls: open, openat, openat64, access, faccessat,
 * fopen, fopen64, stat, stat64, lstat, lstat64, __xstat, __xstat64,
 * __lxstat, __lxstat64, statx, readlink, readlinkat, realpath,
 * openat2 (Linux 5.6+, via wrapper and raw syscall interception).
 *
 * Returns -1 / NULL with errno = EACCES for blocked paths.
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
/* openat2 is not wrapped by glibc, so programs call it via                */
/* syscall(__NR_openat2, ...). We intercept the generic syscall()          */
/* function and also export an openat2() wrapper symbol.                   */
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
typedef long (*syscall_fn)(long, ...);

static open_fn real_open = NULL;
static open_fn real_open64 = NULL;
static openat_fn real_openat = NULL;
static openat_fn real_openat64 = NULL;
static access_fn real_access = NULL;
static faccessat_fn real_faccessat = NULL;
static fopen_fn real_fopen = NULL;
static fopen_fn real_fopen64 = NULL;
static syscall_fn real_syscall = NULL;

static void init_real_funcs(void) {
    if (!real_open)    real_open    = (open_fn)    dlsym(RTLD_NEXT, "open");
    if (!real_open64)  real_open64  = (open_fn)    dlsym(RTLD_NEXT, "open64");
    if (!real_openat)  real_openat  = (openat_fn)  dlsym(RTLD_NEXT, "openat");
    if (!real_openat64)real_openat64= (openat_fn)  dlsym(RTLD_NEXT, "openat64");
    if (!real_access)  real_access  = (access_fn)  dlsym(RTLD_NEXT, "access");
    if (!real_faccessat)real_faccessat=(faccessat_fn)dlsym(RTLD_NEXT, "faccessat");
    if (!real_fopen)   real_fopen   = (fopen_fn)   dlsym(RTLD_NEXT, "fopen");
    if (!real_fopen64) real_fopen64 = (fopen_fn)   dlsym(RTLD_NEXT, "fopen64");
    if (!real_syscall) real_syscall = (syscall_fn) dlsym(RTLD_NEXT, "syscall");
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

/* Build the path to the per-session control file.
 * Uses MCPTAP_BLOCKED_FILES_FILE if set (for testing / override).
 * Otherwise constructs: <MCPTAP_BLOCKED_DIR>/<CODEX_THREAD_ID>/blocked_files
 * Returns 0 on success, -1 if no session ID is available. */
static int build_control_path(char *buf, size_t buf_size) {
    /* Explicit override (for tests / standalone use) */
    const char *explicit = getenv("MCPTAP_BLOCKED_FILES_FILE");
    if (explicit && *explicit) {
        snprintf(buf, buf_size, "%s", explicit);
        return 0;
    }

    const char *session_id = getenv("CODEX_THREAD_ID");
    if (!session_id || !*session_id)
        return -1;

    const char *base_dir = getenv("MCPTAP_BLOCKED_DIR");
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
        blocked_paths[blocked_count] = strdup(expanded);
        if (blocked_paths[blocked_count])
            blocked_count++;
    }
    fclose(f);
}

static int is_path_blocked(const char *path) {
    if (!path)
        return 0;

    /* Skip checks while loading the blocklist to prevent recursion */
    if (_loading_blocklist)
        return 0;

    if (!blocked_initialized)
        load_blocklist();

    if (blocked_count == 0)
        return 0;

    /* Resolve absolute path */
    char abs_path[8192];
    if (path[0] == '~') {
        expand_tilde(path, abs_path, sizeof(abs_path));
        path = abs_path;
    } else if (path[0] != '/') {
        /* Try to resolve relative path */
        char cwd[4096];
        if (getcwd(cwd, sizeof(cwd))) {
            snprintf(abs_path, sizeof(abs_path), "%s/%s", cwd, path);
            path = abs_path;
        }
    }

    for (int i = 0; i < blocked_count; i++) {
        if (!blocked_paths[i]) continue;
        if (strcmp(path, blocked_paths[i]) == 0)
            return 1;
    }
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
/* openat2 is not wrapped by glibc, so programs call it via                 */
/* syscall(__NR_openat2, ...). We intercept the generic syscall()          */
/* function and check if the first argument is __NR_openat2.               */
/*                                                                         */
/* If glibc later provides an openat2() wrapper, our exported symbol       */
/* will take precedence and intercept those calls directly.                */
/* ----------------------------------------------------------------------- */

int openat2(int dirfd, const char *pathname, struct mcptap_open_how *how,
            size_t size) {
    init_real_funcs();
    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    if (!real_syscall)
        return -1;
    return (int)real_syscall(__NR_openat2, dirfd, pathname, how, size);
}

/* Intercept raw syscall() to catch programs that call
 * openat2 via syscall(__NR_openat2, ...). */
long syscall(long number, ...) {
    init_real_funcs();

    /* Fast path: if it's not openat2, delegate immediately. */
    if (number != __NR_openat2) {
        va_list ap;
        va_start(ap, number);
        long a1 = va_arg(ap, long);
        long a2 = va_arg(ap, long);
        long a3 = va_arg(ap, long);
        long a4 = va_arg(ap, long);
        long a5 = va_arg(ap, long);
        long a6 = va_arg(ap, long);
        va_end(ap);
        return real_syscall(number, a1, a2, a3, a4, a5, a6);
    }

    /* openat2: extract pathname (2nd arg) and check. */
    va_list ap;
    va_start(ap, number);
    int dirfd = va_arg(ap, int);
    const char *pathname = va_arg(ap, const char *);
    void *how = va_arg(ap, void *);
    size_t size = va_arg(ap, size_t);
    va_end(ap);

    maybe_reload();
    if (is_path_blocked(pathname)) {
        errno = EACCES;
        return -1;
    }
    return real_syscall(number, dirfd, pathname, how, size);
}
