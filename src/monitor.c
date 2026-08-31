/*
 * Simplifi daily monitor — Raspberry Pi 4 Model B
 *
 * Flow:
 *   1. Join lab SSID "Simplifi Lab 5Ghz" (fallback 2.4Ghz)
 *   2. For each router (Voicelink 1/2, Faxback 1/2):
 *        nmcli connection up router profile (lab profile + static IP are kept)
 *        -> delete ~/.ssh/known_hosts -> ssh root@192.168.2.1
 *        -> collect ubus/simcom/uptime/ping
 *   3. nmcli connection up saved lab profile (static IP restored, wlan0 not deleted)
 *
 * Build on the Pi:  sudo apt install -y build-essential network-manager openssh-client
 *                   make && ./monitor
 */
#define _DEFAULT_SOURCE
#include "monitor.h"

#include <ctype.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

Config g_cfg;
static int g_restore_lab = 0;

static void csv_quote(FILE *fp, const char *s)
{
    int need = 0;
    for (const char *p = s; *p; p++) {
        if (*p == ',' || *p == '"' || *p == '\n' || *p == '\r') {
            need = 1;
            break;
        }
    }
    if (!need) {
        fputs(s, fp);
        return;
    }
    fputc('"', fp);
    for (const char *p = s; *p; p++) {
        if (*p == '"')
            fputc('"', fp);
        fputc(*p, fp);
    }
    fputc('"', fp);
}

static void anydesk_for_imei(const char *imei, char *out, size_t n);

static int str_ieq(const char *a, const char *b)
{
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b))
            return 0;
        a++;
        b++;
    }
    return *a == '\0' && *b == '\0';
}

static int contains_ci(const char *hay, const char *needle)
{
    size_t n;

    if (!hay || !needle || !needle[0])
        return 0;
    n = strlen(needle);
    for (const char *p = hay; *p; p++) {
        size_t i = 0;
        while (i < n && p[i] &&
               tolower((unsigned char)p[i]) == tolower((unsigned char)needle[i]))
            i++;
        if (i == n)
            return 1;
    }
    return 0;
}

static int is_fax_imei(const char *imei)
{
    return imei && imei[0] &&
           (strcmp(imei, "861107035990853") == 0 ||
            strcmp(imei, "866758040526465") == 0);
}

static int router_is_fax(const Router *r)
{
    if (!r)
        return 0;
    if (is_fax_imei(r->imei))
        return 1;
    return contains_ci(r->name, "fax");
}

static void row_init(MonitorRow *row, const Router *r)
{
    memset(row, 0, sizeof(*row));
    now_stamp(row->date, sizeof(row->date));
    snprintf(row->station, sizeof(row->station), "%s", r->name);
    snprintf(row->imei, sizeof(row->imei), "%s", r->imei);
    snprintf(row->anydesk, sizeof(row->anydesk), "%s", r->anydesk);
    if (!row->anydesk[0])
        anydesk_for_imei(r->imei, row->anydesk, sizeof(row->anydesk));
    set_na(row->firmware, sizeof(row->firmware));
    set_na(row->uptime, sizeof(row->uptime));
    /* Voicelink cannot be automated (desk phone). Fax starts FAIL until queue check. */
    if (router_is_fax(r) && g_cfg.fax_send)
        snprintf(row->voicelink_status, sizeof(row->voicelink_status), "FAIL");
    else
        snprintf(row->voicelink_status, sizeof(row->voicelink_status), "N/A");
    set_na(row->carrier, sizeof(row->carrier));
    set_na(row->phone, sizeof(row->phone));
    set_na(row->rssi, sizeof(row->rssi));
    snprintf(row->wifi_sim_status, sizeof(row->wifi_sim_status), "FAIL");
    snprintf(row->ssh_access, sizeof(row->ssh_access), "FAIL");
    row->note[0] = '\0';
}

static const char *CSV_HEADER =
    "Date,Anydesk ID,IMEI,Firmware Version,Uptime (hh:mm),Voicelink/Fax status,Carrier,Phone,"
    "RSSI (dBm),WiFi Status (Sim Data),SSH Access,Note\n";

static void vfax_normalize(const char *in, char *out, size_t n)
{
    if (in && str_ieq(in, "PASS"))
        snprintf(out, n, "PASS");
    else if (in && (str_ieq(in, "N/A") || str_ieq(in, "NA") || str_ieq(in, "N.A.")))
        snprintf(out, n, "N/A");
    else if (in && in[0] && str_ieq(in, "FAIL"))
        snprintf(out, n, "FAIL");
    else if (in && in[0])
        snprintf(out, n, "FAIL");
    else
        snprintf(out, n, "N/A");
}

static void csv_write_row(FILE *fp, const MonitorRow *row)
{
    char vfax[MAX_FIELD];

    vfax_normalize(row->voicelink_status, vfax, sizeof(vfax));
    csv_quote(fp, row->date);             fputc(',', fp);
    csv_quote(fp, row->anydesk);          fputc(',', fp);
    csv_quote(fp, row->imei);             fputc(',', fp);
    csv_quote(fp, row->firmware);         fputc(',', fp);
    csv_quote(fp, row->uptime);           fputc(',', fp);
    csv_quote(fp, vfax);                  fputc(',', fp);
    csv_quote(fp, row->carrier);          fputc(',', fp);
    csv_quote(fp, row->phone);            fputc(',', fp);
    csv_quote(fp, row->rssi);             fputc(',', fp);
    csv_quote(fp, row->wifi_sim_status);  fputc(',', fp);
    csv_quote(fp, row->ssh_access);       fputc(',', fp);
    csv_quote(fp, row->note);             fputc('\n', fp);
}

/* Only the calendar date: "2026-08-27 18:41:02" → "2026-08-27". Time is ignored. */
static void csv_day(const char *stamp, char *day, size_t n)
{
    const char *p = stamp;
    while (*p == '"' || isspace((unsigned char)*p))
        p++;

    size_t i = 0;
    while (p[i] && p[i] != ' ' && p[i] != 'T' && p[i] != ',' && p[i] != '"' && i + 1 < n)
        i++;
    memcpy(day, p, i);
    day[i] = '\0';
}

static const char *csv_skip_field(const char *p)
{
    if (*p == '"') {
        p++;
        while (*p) {
            if (*p == '"') {
                if (p[1] == '"') {
                    p += 2;
                    continue;
                }
                p++;
                break;
            }
            p++;
        }
        if (*p == ',')
            p++;
        return p;
    }
    while (*p && *p != ',' && *p != '\n' && *p != '\r')
        p++;
    if (*p == ',')
        p++;
    return p;
}

static int csv_nth_field(const char *line, int idx, char *out, size_t n)
{
    const char *p = line;
    for (int field = 0; field < idx && *p; field++)
        p = csv_skip_field(p);

    if (!*p || *p == '\n' || *p == '\r') {
        out[0] = '\0';
        return -1;
    }

    if (*p == '"') {
        p++;
        size_t i = 0;
        while (*p && i + 1 < n) {
            if (*p == '"' && p[1] == '"') {
                out[i++] = '"';
                p += 2;
                continue;
            }
            if (*p == '"')
                break;
            out[i++] = *p++;
        }
        out[i] = '\0';
        return 0;
    }

    size_t i = 0;
    while (*p && *p != ',' && *p != '\n' && *p != '\r' && i + 1 < n)
        out[i++] = *p++;
    out[i] = '\0';
    return 0;
}

static void status_pass_fail(const char *in, char *out, size_t n)
{
    if (in && (str_ieq(in, "PASS") || str_ieq(in, "UP") || str_ieq(in, "YES") ||
               str_ieq(in, "OK") || str_ieq(in, "OKE")))
        snprintf(out, n, "PASS");
    else
        snprintf(out, n, "FAIL");
}

static void anydesk_for_imei(const char *imei, char *out, size_t n)
{
    static const struct {
        const char *imei;
        const char *anydesk;
    } map[] = {
        {"866758040553188", "1267941734"},
        {"861107035967513", "1267941734"},
        {"861107035990853", "1484607357"},
        {"866758040526465", "1628162772"},
        {"866834045868010", "1818958765"},
        {"866834041157558", "1818958765"},
    };

    out[0] = '\0';
    if (!imei || !imei[0])
        return;
    for (int i = 0; i < g_cfg.router_count; i++) {
        if (g_cfg.routers[i].imei[0] && strcmp(g_cfg.routers[i].imei, imei) == 0 &&
            g_cfg.routers[i].anydesk[0]) {
            snprintf(out, n, "%s", g_cfg.routers[i].anydesk);
            return;
        }
    }
    for (size_t i = 0; i < sizeof(map) / sizeof(map[0]); i++) {
        if (strcmp(map[i].imei, imei) == 0) {
            snprintf(out, n, "%s", map[i].anydesk);
            return;
        }
    }
}

static int csv_row_from_line(const char *line, int old_format, int has_vfax, MonitorRow *row)
{
    char wifi[MAX_FIELD], ssh[MAX_FIELD], vfax[MAX_FIELD];

    memset(row, 0, sizeof(*row));
    csv_nth_field(line, 0, row->date, sizeof(row->date));
    csv_nth_field(line, 2, row->imei, sizeof(row->imei));
    csv_nth_field(line, 3, row->firmware, sizeof(row->firmware));
    csv_nth_field(line, 4, row->uptime, sizeof(row->uptime));
    snprintf(row->voicelink_status, sizeof(row->voicelink_status), "N/A");

    if (old_format) {
        csv_nth_field(line, 1, row->station, sizeof(row->station));
        csv_nth_field(line, 6, row->carrier, sizeof(row->carrier));
        csv_nth_field(line, 7, row->phone, sizeof(row->phone));
        csv_nth_field(line, 8, row->rssi, sizeof(row->rssi));
        csv_nth_field(line, 9, wifi, sizeof(wifi));
        csv_nth_field(line, 10, ssh, sizeof(ssh));
        anydesk_for_imei(row->imei, row->anydesk, sizeof(row->anydesk));
    } else if (has_vfax) {
        csv_nth_field(line, 1, row->anydesk, sizeof(row->anydesk));
        csv_nth_field(line, 5, vfax, sizeof(vfax));
        csv_nth_field(line, 6, row->carrier, sizeof(row->carrier));
        csv_nth_field(line, 7, row->phone, sizeof(row->phone));
        csv_nth_field(line, 8, row->rssi, sizeof(row->rssi));
        csv_nth_field(line, 9, wifi, sizeof(wifi));
        csv_nth_field(line, 10, ssh, sizeof(ssh));
        csv_nth_field(line, 11, row->note, sizeof(row->note));
        vfax_normalize(vfax, row->voicelink_status, sizeof(row->voicelink_status));
        if (!row->anydesk[0])
            anydesk_for_imei(row->imei, row->anydesk, sizeof(row->anydesk));
    } else {
        csv_nth_field(line, 1, row->anydesk, sizeof(row->anydesk));
        csv_nth_field(line, 5, row->carrier, sizeof(row->carrier));
        csv_nth_field(line, 6, row->phone, sizeof(row->phone));
        csv_nth_field(line, 7, row->rssi, sizeof(row->rssi));
        csv_nth_field(line, 8, wifi, sizeof(wifi));
        csv_nth_field(line, 9, ssh, sizeof(ssh));
        csv_nth_field(line, 10, row->note, sizeof(row->note));
        if (!row->anydesk[0])
            anydesk_for_imei(row->imei, row->anydesk, sizeof(row->anydesk));
    }
    status_pass_fail(wifi, row->wifi_sim_status, sizeof(row->wifi_sim_status));
    status_pass_fail(ssh, row->ssh_access, sizeof(row->ssh_access));
    return row->imei[0] ? 0 : -1;
}

#define CSV_KEEP_DAYS     20
#define CSV_ROWS_PER_DAY  4

static int csv_count_data_rows(void)
{
    FILE *fp = fopen(g_cfg.output_csv, "r");
    if (!fp)
        return 0;
    char line[2048];
    int n = 0;
    int header = 1;
    while (fgets(line, sizeof(line), fp)) {
        if (header) {
            header = 0;
            continue;
        }
        if (line[0] == '\n' || line[0] == '\r' || line[0] == '\0')
            continue;
        n++;
    }
    fclose(fp);
    return n;
}

/* Drop the first 4 data rows (oldest day). File is oldest-first, new dates append. */
static int csv_drop_oldest_day(void)
{
    char tmp_path[MAX_PATH + 8];
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", g_cfg.output_csv);

    FILE *in = fopen(g_cfg.output_csv, "r");
    FILE *out = fopen(tmp_path, "w");
    if (!in || !out) {
        if (in)
            fclose(in);
        if (out)
            fclose(out);
        return -1;
    }

    char line[2048];
    int line_no = 0;
    int skipped = 0;
    while (fgets(line, sizeof(line), in)) {
        line_no++;
        if (line_no == 1) {
            fputs(line, out);
            continue;
        }
        if (skipped < CSV_ROWS_PER_DAY) {
            skipped++;
            continue;
        }
        fputs(line, out);
    }
    fclose(in);
    fclose(out);
    if (rename(tmp_path, g_cfg.output_csv) != 0)
        return -1;
    return skipped;
}

static void csv_prune_oldest_days(void)
{
    int max_rows = CSV_KEEP_DAYS * CSV_ROWS_PER_DAY;
    int n = csv_count_data_rows();
    while (n > max_rows) {
        if (csv_drop_oldest_day() <= 0)
            break;
        n = csv_count_data_rows();
        log_msg("INFO", "CSV: dropped oldest 4 rows; now %d data rows (keep %d days)",
                n, CSV_KEEP_DAYS);
    }
}

/*
 * Compare only the date part of column Date (ignore HH:MM:SS).
 * Same YYYY-MM-DD + same IMEI → overwrite that row.
 * Different date → append a new row.
 * Old CSVs (Station / Voicelink columns) are rewritten into the new header.
 */
static int csv_upsert(const MonitorRow *row)
{
    char today[16];
    char tmp_path[MAX_PATH + 8];

    csv_day(row->date, today, sizeof(today));
    ensure_parent_dir(g_cfg.output_csv);

    if (!file_exists(g_cfg.output_csv)) {
        FILE *fp = fopen(g_cfg.output_csv, "w");
        if (!fp) {
            log_msg("ERROR", "Cannot write CSV %s", g_cfg.output_csv);
            return -1;
        }
        fputs(CSV_HEADER, fp);
        csv_write_row(fp, row);
        fclose(fp);
        log_msg("INFO", "CSV: created %s and inserted IMEI %s (%s)",
                g_cfg.output_csv, row->imei, today);
        return 0;
    }

    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", g_cfg.output_csv);
    FILE *in = fopen(g_cfg.output_csv, "r");
    FILE *out = fopen(tmp_path, "w");
    if (!in || !out) {
        log_msg("ERROR", "Cannot rewrite CSV %s", g_cfg.output_csv);
        if (in)
            fclose(in);
        if (out)
            fclose(out);
        return -1;
    }

    char line[2048];
    int replaced = 0;
    int line_no = 0;
    int old_format = 0;
    int has_vfax = 0;
    while (fgets(line, sizeof(line), in)) {
        line_no++;
        if (line_no == 1) {
            old_format = (strstr(line, "Anydesk ID") == NULL);
            has_vfax = (strstr(line, "Voicelink/Fax") != NULL);
            fputs(CSV_HEADER, out);
            continue;
        }

        MonitorRow existing;
        if (csv_row_from_line(line, old_format, has_vfax, &existing) != 0) {
            continue;
        }

        char row_day[16];
        csv_day(existing.date, row_day, sizeof(row_day));

        int same_day = (today[0] && strcmp(row_day, today) == 0);
        int same_imei = (row->imei[0] && strcmp(existing.imei, row->imei) == 0);

        if (same_day && same_imei && !replaced) {
            csv_write_row(out, row);
            replaced = 1;
            continue;
        }
        csv_write_row(out, &existing);
    }

    if (!replaced) {
        csv_write_row(out, row);
        log_msg("INFO", "CSV: appended new row for IMEI %s on %s", row->imei, today);
    } else {
        log_msg("INFO", "CSV: overwrote IMEI %s (same date %s, time ignored)", row->imei, today);
    }

    fclose(in);
    fclose(out);
    if (rename(tmp_path, g_cfg.output_csv) != 0) {
        log_msg("ERROR", "Cannot replace %s with temp file", g_cfg.output_csv);
        return -1;
    }
    if (!replaced)
        csv_prune_oldest_days();
    return 0;
}

static void trim_inplace(char *s)
{
    char *p = s;
    size_t n;

    if (!s)
        return;
    while (*p && isspace((unsigned char)*p))
        p++;
    if (p != s)
        memmove(s, p, strlen(p) + 1);
    n = strlen(s);
    while (n > 0 && isspace((unsigned char)s[n - 1]))
        s[--n] = '\0';
}

/* Update Voicelink/Fax status on today's row for this IMEI; keep other columns. */
static int csv_set_vfax_today(const char *imei, const char *status)
{
    char today_stamp[MAX_FIELD];
    char today[16];
    char tmp_path[MAX_PATH + 8];
    char st[MAX_FIELD];
    int patched = 0;

    if (!imei || !imei[0] || !file_exists(g_cfg.output_csv))
        return -1;

    vfax_normalize(status, st, sizeof(st));
    now_stamp(today_stamp, sizeof(today_stamp));
    csv_day(today_stamp, today, sizeof(today));
    snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", g_cfg.output_csv);

    FILE *in = fopen(g_cfg.output_csv, "r");
    FILE *out = fopen(tmp_path, "w");
    if (!in || !out) {
        if (in)
            fclose(in);
        if (out)
            fclose(out);
        return -1;
    }

    char line[2048];
    int line_no = 0;
    int old_format = 0;
    int has_vfax = 0;
    while (fgets(line, sizeof(line), in)) {
        line_no++;
        if (line_no == 1) {
            old_format = (strstr(line, "Anydesk ID") == NULL);
            has_vfax = (strstr(line, "Voicelink/Fax") != NULL);
            fputs(CSV_HEADER, out);
            continue;
        }

        MonitorRow existing;
        if (csv_row_from_line(line, old_format, has_vfax, &existing) != 0)
            continue;

        char row_day[16];
        csv_day(existing.date, row_day, sizeof(row_day));
        if (today[0] && strcmp(row_day, today) == 0 && strcmp(existing.imei, imei) == 0) {
            snprintf(existing.voicelink_status, sizeof(existing.voicelink_status), "%s", st);
            patched = 1;
        }
        csv_write_row(out, &existing);
    }

    fclose(in);
    fclose(out);
    if (rename(tmp_path, g_cfg.output_csv) != 0)
        return -1;
    return patched ? 0 : -1;
}

static void apply_fax_status_file(const char *path)
{
    FILE *fp;
    char line[256];

    if (!path || !path[0])
        return;
    fp = fopen(path, "r");
    if (!fp) {
        log_msg("WARN", "No fax status file %s — Fax station CSV stays FAIL", path);
        return;
    }
    while (fgets(line, sizeof(line), fp)) {
        char *eq;
        char st[MAX_FIELD];

        trim_inplace(line);
        if (!line[0] || line[0] == '#')
            continue;
        eq = strchr(line, '=');
        if (!eq)
            continue;
        *eq = '\0';
        trim_inplace(line);
        trim_inplace(eq + 1);
        if (!line[0] || !eq[1])
            continue;
        vfax_normalize(eq + 1, st, sizeof(st));
        if (csv_set_vfax_today(line, st) == 0)
            log_msg("INFO", "CSV: Voicelink/Fax status IMEI %s = %s", line, st);
        else
            log_msg("WARN", "CSV: no today's row to set Voicelink/Fax status for IMEI %s",
                    line);
    }
    fclose(fp);
}

static const char *pick_gateway(void)
{
    if (wait_ping(g_cfg.router_gateway, 12) == 0) {
        log_msg("INFO", "Router gateway reachable: %s", g_cfg.router_gateway);
        return g_cfg.router_gateway;
    }
    if (g_cfg.router_gateway_alt[0] &&
        wait_ping(g_cfg.router_gateway_alt, 8) == 0) {
        log_msg("INFO", "Using alternate gateway: %s", g_cfg.router_gateway_alt);
        return g_cfg.router_gateway_alt;
    }
    return NULL;
}

static int collect_router(const Router *r, MonitorRow *row)
{
    char out[PTY_BUF_SIZE];
    SshPty ssh;
    memset(&ssh, 0, sizeof(ssh));
    ssh.master_fd = -1;
    ssh.pid = -1;

    ssh_delete_known_hosts();

    const char *gw = pick_gateway();
    if (!gw) {
        log_msg("ERROR", "%s: gateway not reachable after WiFi join", r->name);
        snprintf(row->ssh_access, sizeof(row->ssh_access), "FAIL");
        snprintf(row->wifi_sim_status, sizeof(row->wifi_sim_status), "FAIL");
        return -1;
    }

    if (ssh_pty_open(&ssh, g_cfg.ssh_user, gw) != 0) {
        log_msg("INFO", "%s: cannot open SSH (Dropbear off?) — skip", r->name);
        snprintf(row->ssh_access, sizeof(row->ssh_access), "FAIL");
        return -1;
    }

    if (ssh_pty_login(&ssh, r->password, g_cfg.ssh_timeout_sec) != 0) {
        log_msg("INFO", "%s: SSH login failed (Dropbear off or wrong password) — skip",
                r->name);
        snprintf(row->ssh_access, sizeof(row->ssh_access), "FAIL");
        ssh_pty_close(&ssh);
        return -1;
    }

    snprintf(row->ssh_access, sizeof(row->ssh_access), "PASS");

    if (ssh_pty_exec(&ssh, "ubus call cellular status", out, sizeof(out), 15) == 0) {
        log_msg("INFO", "cellular status captured (%zu bytes)", strlen(out));
        fill_from_cellular(out, row);
        if (row->imei[0] && r->imei[0] && strcmp(row->imei, r->imei) != 0)
            log_msg("WARN", "%s: IMEI mismatch (expected %s, got %s) — check WiFi SSID",
                    r->name, r->imei, row->imei);
        snprintf(row->imei, sizeof(row->imei), "%s", r->imei);
    } else {
        log_msg("WARN", "%s: ubus cellular status failed", r->name);
    }

    if (ssh_pty_exec(&ssh, "simcom get firmware_version", out, sizeof(out), 12) == 0)
        parse_firmware_line(out, row->firmware, sizeof(row->firmware));

    if (ssh_pty_exec(&ssh, "uptime", out, sizeof(out), 8) == 0)
        parse_uptime_hhmm(out, row->uptime, sizeof(row->uptime));

    if (ssh_pty_exec(&ssh, "ping -c 3 -W 3 google.com", out, sizeof(out), 25) == 0)
        parse_ping_status(out, row->wifi_sim_status, sizeof(row->wifi_sim_status));
    else
        snprintf(row->wifi_sim_status, sizeof(row->wifi_sim_status), "FAIL");

    ssh_pty_close(&ssh);
    return 0;
}

static const char *python_bin(void)
{
    if (file_exists(".venv/bin/python3"))
        return ".venv/bin/python3";
    if (file_exists("venv/bin/python3"))
        return "venv/bin/python3";
    return "python3";
}

static int run_python_stream(const char *what, const char *cmd)
{
    char line[2048];

    log_msg("INFO", "%s", what);
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        log_msg("ERROR", "Cannot start: %s", cmd);
        return -1;
    }
    while (fgets(line, sizeof(line), fp)) {
        fputs(line, stderr);
        if (g_log) {
            fputs(line, g_log);
            fflush(g_log);
        }
    }

    int st = pclose(fp);
    if (st != -1 && WIFEXITED(st))
        return WEXITSTATUS(st);
    return -1;
}

static void fetch_portal_logs(void)
{
    char cmd[1024];
    const char *py;

    if (!g_cfg.portal_logs) {
        log_msg("INFO", "Portal log download disabled (portal_logs=0)");
        return;
    }
    if (!file_exists(g_cfg.portal_conf)) {
        log_msg("WARN", "Portal conf %s not found — skip developer log download", g_cfg.portal_conf);
        return;
    }

    ensure_parent_dir("output/.keep");
    py = python_bin();
    if (strcmp(py, "python3") == 0)
        log_msg("WARN", "No .venv found — using system python3. Run: make deps");

    snprintf(cmd, sizeof(cmd),
             "PYTHONUNBUFFERED=1 %s scripts/portal_logs.py --conf '%s' --out output "
             "--imei-csv scripts/portal_imeis.csv 2>&1",
             py, g_cfg.portal_conf);
    int rc = run_python_stream("Downloading Simplifi Portal developer logs ...", cmd);
    if (rc != 0)
        log_msg("ERROR", "Portal log download failed (exit %d)", rc);
    else
        log_msg("PASSED", "Portal logs saved under output/routers_log/");
}

static void chrome_cleanup(void)
{
    log_msg("INFO", "Stopping leftover Chromium/chromedriver before Faxback ...");
    run_cmd("pkill -9 -f '/tmp/simplifi-chrome' >/dev/null 2>&1; "
            "pkill -9 -f chromedriver >/dev/null 2>&1; true",
            NULL, 0, 0);
    sleep(3);
}

static void send_fax_stations(void)
{
    char cmd[1024];
    const char *py;

    if (!g_cfg.fax_send) {
        log_msg("INFO", "Fax send disabled (fax_send=0)");
        return;
    }
    if (!file_exists(g_cfg.fax_conf)) {
        log_msg("WARN", "Fax conf %s not found — skip Faxback send", g_cfg.fax_conf);
        return;
    }

    chrome_cleanup();
    py = python_bin();
    snprintf(cmd, sizeof(cmd),
             "PYTHONUNBUFFERED=1 %s scripts/send_fax.py --conf '%s' 2>&1",
             py, g_cfg.fax_conf);
    int rc = run_python_stream("Sending test fax to Fax Stations via Faxback ...", cmd);
    apply_fax_status_file("output/fax_status.txt");
    if (rc != 0)
        log_msg("ERROR", "Fax send/queue check failed (exit %d)", rc);
    else
        log_msg("PASSED", "Fax send + ReceivedPendingDeletion check finished");
}

static void routers_log_day(char *day, size_t n)
{
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(day, n, "%d_%m_%Y", &tm);
}

static int make_abs_path(const char *src, char *dst, size_t n)
{
    char resolved[PATH_MAX];

    if (!src || !src[0])
        return -1;
    if (src[0] == '/') {
        snprintf(dst, n, "%s", src);
        return 0;
    }
    if (realpath(src, resolved)) {
        snprintf(dst, n, "%s", resolved);
        return 0;
    }
    if (!getcwd(resolved, sizeof(resolved)))
        return -1;
    snprintf(dst, n, "%s/%s", resolved, src);
    return 0;
}

static const char *path_basename(const char *p)
{
    const char *s = strrchr(p, '/');

    return (s && s[1]) ? s + 1 : p;
}

static void laptop_ssh_host(char *out, size_t n)
{
    if (g_cfg.laptop_user[0])
        snprintf(out, n, "%s@%s", g_cfg.laptop_user, g_cfg.laptop_host);
    else
        snprintf(out, n, "%s", g_cfg.laptop_host);
}

static void laptop_conn_flags(char *extra, size_t n, int for_scp)
{
    int off = 0;

    extra[0] = '\0';
    if (g_cfg.laptop_port > 0)
        off += snprintf(extra + off, n - (size_t)off, for_scp ? "-P %d " : "-p %d ",
                        g_cfg.laptop_port);
    if (g_cfg.laptop_key[0] && file_exists(g_cfg.laptop_key))
        snprintf(extra + off, n - (size_t)off, "-i '%s' ", g_cfg.laptop_key);
}

static int scp_to_laptop_as(const char *src, int recursive, const char *remote_name)
{
    char extra[384], host[MAX_PATH], target[MAX_PATH * 2], cmd[2048], out[1024];
    char abs_src[PATH_MAX];
    const char *name;

    if (make_abs_path(src, abs_src, sizeof(abs_src)) != 0) {
        log_msg("ERROR", "Cannot resolve absolute path for %s", src);
        return -1;
    }

    laptop_conn_flags(extra, sizeof(extra), 1);
    laptop_ssh_host(host, sizeof(host));
    if (recursive) {
        snprintf(target, sizeof(target), "%s:%s/", host, g_cfg.laptop_dest);
    } else {
        name = (remote_name && remote_name[0]) ? remote_name : path_basename(abs_src);
        snprintf(target, sizeof(target), "%s:%s/%s", host, g_cfg.laptop_dest, name);
    }

    snprintf(cmd, sizeof(cmd),
             "scp %s-O -o BatchMode=yes -o StrictHostKeyChecking=accept-new %s'%s' '%s'",
             extra, recursive ? "-r " : "", abs_src, target);
    log_msg("INFO", "scp %s -> %s", abs_src, target);
    return run_cmd(cmd, out, sizeof(out), 0);
}

static int scp_to_laptop(const char *src, int recursive)
{
    return scp_to_laptop_as(src, recursive, NULL);
}

static int scp_csv_to_laptop(const char *src)
{
    char alt[64];
    time_t t;
    struct tm tm;

    if (scp_to_laptop_as(src, 0, "daily_monitor.csv") == 0) {
        log_msg("PASSED", "Copied daily_monitor.csv to lab laptop");
        return 0;
    }

    t = time(NULL);
    localtime_r(&t, &tm);
    strftime(alt, sizeof(alt), "daily_monitor_%Y%m%d_%H%M%S.csv", &tm);
    log_msg("WARN", "Cannot overwrite daily_monitor.csv (likely open in Excel) — "
            "saving as %s", alt);
    if (scp_to_laptop_as(src, 0, alt) == 0) {
        log_msg("PASSED", "CSV landed on the laptop as %s — close Excel later and "
                "replace daily_monitor.csv if you want one file", alt);
        return 0;
    }
    return -1;
}

static void sync_to_laptop(void)
{
    char day[16], day_dir[MAX_PATH];
    int rc;

    if (!g_cfg.laptop_sync) {
        log_msg("INFO", "Laptop sync disabled (laptop_sync=0)");
        return;
    }
    if (!g_cfg.laptop_host[0]) {
        log_msg("INFO", "laptop_host is empty — skip copy to lab laptop");
        return;
    }

    routers_log_day(day, sizeof(day));
    snprintf(day_dir, sizeof(day_dir), "output/routers_log/%s", day);

    log_msg("INFO", "Copying results to %s:%s (same SSH as: ssh %s)", g_cfg.laptop_host,
            g_cfg.laptop_dest, g_cfg.laptop_host);

    if (file_exists(g_cfg.output_csv)) {
        if (scp_csv_to_laptop(g_cfg.output_csv) != 0)
            log_msg("ERROR", "scp CSV to laptop failed");
    } else {
        log_msg("WARN", "CSV not found: %s", g_cfg.output_csv);
    }

    if (file_exists(day_dir)) {
        rc = scp_to_laptop(day_dir, 1);
        if (rc != 0)
            log_msg("ERROR", "scp %s failed (exit %d)", day_dir, rc);
        else
            log_msg("PASSED", "Copied %s to lab laptop", day_dir);
    } else {
        log_msg("WARN", "Router log folder not found: %s", day_dir);
    }
}

static void fill_sharepoint_excel(void)
{
    char cmd[1024];
    const char *py;

    if (!g_cfg.sharepoint_excel) {
        log_msg("INFO", "SharePoint Excel fill disabled (sharepoint_excel=0)");
        return;
    }
    if (!file_exists(g_cfg.sharepoint_conf)) {
        log_msg("WARN", "SharePoint conf %s not found — skip Excel fill", g_cfg.sharepoint_conf);
        return;
    }
    if (!file_exists(g_cfg.output_csv)) {
        log_msg("WARN", "CSV %s not found — skip SharePoint Excel fill", g_cfg.output_csv);
        return;
    }

    py = python_bin();
    snprintf(cmd, sizeof(cmd),
             "PYTHONUNBUFFERED=1 %s scripts/sharepoint_excel.py --conf '%s' --csv '%s' 2>&1",
             py, g_cfg.sharepoint_conf, g_cfg.output_csv);
    int rc = run_python_stream("Filling SharePoint monitoring Excel from today's CSV ...", cmd);
    if (rc != 0)
        log_msg("ERROR", "SharePoint Excel fill failed (exit %d)", rc);
    else
        log_msg("PASSED", "SharePoint Excel updated (4 VN station sheets)");
}

static void restore_lab_wifi(void)
{
    if (!g_restore_lab)
        return;
    g_restore_lab = 0;
    log_msg("INFO", "Reconnecting Raspberry Pi to lab WiFi");
    if (wifi_connect_lab() != 0)
        log_msg("ERROR", "Could not restore lab WiFi — check the Pi manually");
}

static void on_signal(int sig)
{
    (void)sig;
    restore_lab_wifi();
    _exit(1);
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "Usage: %s [-c monitor.conf]\n"
            "  Raspberry Pi daily monitor: join each Simplifi router WiFi,\n"
            "  SSH in, collect cellular/firmware/uptime/ping, write CSV.\n",
            argv0);
}

int main(int argc, char **argv)
{
    const char *conf_path = "monitor.conf";

    for (int i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--config") == 0) && i + 1 < argc) {
            conf_path = argv[++i];
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 1;
        }
    }

    if (config_load(conf_path, &g_cfg) != 0)
        return 1;

    ensure_parent_dir(g_cfg.log_file);
    g_log = fopen(g_cfg.log_file, "a");
    if (!g_log)
        log_msg("WARN", "Cannot open log file %s, continuing on stderr only", g_cfg.log_file);

    log_msg("INFO", "=== Simplifi daily monitor start ===");
    log_msg("INFO", "Config: %s  routers: %d  csv: %s", conf_path, g_cfg.router_count,
            g_cfg.output_csv);

    log_msg("INFO", "Assuming Dropbear SSH is enabled on all %d routers — starting",
            g_cfg.router_count);

    if (g_cfg.lab_password[0] == '\0')
        log_msg("WARN", "lab_password is empty in %s — lab WiFi join may fail if the SSID is secured",
                conf_path);

    atexit(restore_lab_wifi);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    log_msg("INFO", "Joining lab network first: '%s'", g_cfg.lab_ssid_5g);
    if (wifi_connect_lab() != 0)
        log_msg("WARN", "Could not join lab WiFi at start; continuing with router cycle");
    g_restore_lab = 1;

    for (int i = 0; i < g_cfg.router_count; i++) {
        Router *r = &g_cfg.routers[i];
        MonitorRow row;
        row_init(&row, r);

        log_msg("INFO", "----- [%d/%d] %s  IMEI=%s  SSID=%s -----",
                i + 1, g_cfg.router_count, r->name, r->imei, r->ssid);

        if (wifi_connect(r->ssid, r->password, g_cfg.wifi_timeout_sec) != 0) {
            log_msg("FAILED", "Failed to connect to WiFi of router %s of %s",
                    r->imei, r->name);
            snprintf(row.ssh_access, sizeof(row.ssh_access), "FAIL");
            snprintf(row.wifi_sim_status, sizeof(row.wifi_sim_status), "FAIL");
            csv_upsert(&row);
            continue;
        }

        log_msg("PASSED", "Successfully connected to WiFi of router %s of %s",
                r->imei, r->name);

        if (collect_router(r, &row) != 0)
            log_msg("INFO", "%s: Dropbear/SSH not available — skip this router, continue",
                    r->name);
        csv_upsert(&row);

        log_msg("INFO", "%s done  SSH=%s  IMEI=%s  FW=%s  up=%s  vfax=%s  carrier=%s  rssi=%s  sim=%s",
                r->name, row.ssh_access, row.imei, row.firmware, row.uptime,
                row.voicelink_status, row.carrier, row.rssi, row.wifi_sim_status);

        wifi_disconnect();
        sleep(2);
    }

    restore_lab_wifi();
    fetch_portal_logs();
    send_fax_stations();
    sync_to_laptop();
    fill_sharepoint_excel();
    log_msg("INFO", "=== Simplifi daily monitor finished ===");
    if (g_log)
        fclose(g_log);
    return 0;
}
