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
char g_flow[48] = "SETUP";
static int g_flow_pct = 0;

void write_monitor_status(int running, int percent, const char *message)
{
    FILE *fp = fopen("output/monitor_status.json", "w");
    if (!fp)
        return;
    time_t t = time(NULL);
    struct tm tm;
    char ts[32];
    localtime_r(&t, &tm);
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm);
    /* minimal JSON — escape quotes in message */
    char safe[512];
    size_t j = 0;
    const char *src = message ? message : "";
    for (size_t i = 0; src[i] && j + 2 < sizeof(safe); i++) {
        char c = src[i];
        if (c == '"' || c == '\\') {
            safe[j++] = '\\';
            safe[j++] = c;
        } else if (c == '\n' || c == '\r') {
            safe[j++] = ' ';
        } else {
            safe[j++] = c;
        }
    }
    safe[j] = '\0';
    fprintf(fp,
            "{\"running\":%s,\"flow\":\"%s\",\"percent\":%d,\"message\":\"%s\",\"updated\":\"%s\"}\n",
            running ? "true" : "false",
            g_flow[0] ? g_flow : "SETUP",
            percent < 0 ? 0 : (percent > 100 ? 100 : percent),
            safe,
            ts);
    fclose(fp);
}

void set_monitor_flow_pct(const char *flow, int percent)
{
    if (flow && flow[0]) {
        snprintf(g_flow, sizeof(g_flow), "%s", flow);
        setenv("MONITOR_FLOW", g_flow, 1);
    }
    if (percent >= 0)
        g_flow_pct = percent > 100 ? 100 : percent;
    write_monitor_status(1, g_flow_pct, g_flow);
}

void set_monitor_flow(const char *flow)
{
    set_monitor_flow_pct(flow, g_flow_pct);
}

void log_msg(const char *level, const char *fmt, ...)
{
    char ts[32];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm);

    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[%s] [%s] [%s] ", ts, level, g_flow[0] ? g_flow : "SETUP");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);

    if (g_log) {
        va_start(ap, fmt);
        fprintf(g_log, "[%s] [%s] [%s] ", ts, level, g_flow[0] ? g_flow : "SETUP");
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
