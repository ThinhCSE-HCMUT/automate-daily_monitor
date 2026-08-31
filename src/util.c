#define _DEFAULT_SOURCE
#include "monitor.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

FILE *g_log = NULL;

void log_msg(const char *level, const char *fmt, ...)
{
    char ts[32];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm);

    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[%s] [%s] ", ts, level);
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);

    if (g_log) {
        va_start(ap, fmt);
        fprintf(g_log, "[%s] [%s] ", ts, level);
        vfprintf(g_log, fmt, ap);
        fprintf(g_log, "\n");
        fflush(g_log);
        va_end(ap);
    }
}

void set_na(char *field, size_t n)
{
    snprintf(field, n, "N/A");
}

void now_stamp(char *out, size_t n)
{
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(out, n, "%Y-%m-%d %H:%M:%S", &tm);
}

int file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

int ensure_parent_dir(const char *path)
{
    char tmp[MAX_PATH];
    snprintf(tmp, sizeof(tmp), "%s", path);
    char *slash = strrchr(tmp, '/');
    if (!slash)
        return 0;
    *slash = '\0';
    if (tmp[0] == '\0')
        return 0;
    if (mkdir(tmp, 0755) == 0 || errno == EEXIST)
        return 0;
    return -1;
}

int run_cmd(const char *cmd, char *out, size_t out_sz, int timeout_sec)
{
    (void)timeout_sec; /* nmcli/ping/ssh already have their own timeouts */
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        log_msg("ERROR", "popen failed for: %s (%s)", cmd, strerror(errno));
        return -1;
    }

    if (out && out_sz) {
        size_t n = fread(out, 1, out_sz - 1, fp);
        out[n] = '\0';
    }
    /* Always drain leftover output. A full pipe blocks the child; pclose
     * then kills Python with SIGPIPE / exit 120. */
    char dump[512];
    while (fread(dump, 1, sizeof(dump), fp) > 0) {
    }

    int st = pclose(fp);
    if (st == -1)
        return -1;
    if (WIFEXITED(st))
        return WEXITSTATUS(st);
    return -1;
}

int wait_ping(const char *ip, int timeout_sec)
{
    char cmd[160];
    time_t deadline = time(NULL) + timeout_sec;

    snprintf(cmd, sizeof(cmd), "ping -c 1 -W 1 %s >/dev/null 2>&1", ip);
    while (time(NULL) < deadline) {
        if (run_cmd(cmd, NULL, 0, 0) == 0)
            return 0;
        sleep(1);
    }
    return -1;
}
