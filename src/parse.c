#define _DEFAULT_SOURCE
#include "monitor.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int json_get_string(const char *json, const char *key, char *out, size_t n)
{
    char pat[96];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = strstr(json, pat);
    if (!p)
        return -1;
    p += strlen(pat);
    p = strchr(p, ':');
    if (!p)
        return -1;
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
        p++;

    if (*p == '"') {
        p++;
        size_t i = 0;
        while (*p && *p != '"' && i + 1 < n) {
            if (*p == '\\' && p[1])
                p++;
            out[i++] = *p++;
        }
        out[i] = '\0';
        return 0;
    }

    size_t i = 0;
    while (*p && *p != ',' && *p != '}' && *p != '\n' && i + 1 < n) {
        if (!isspace((unsigned char)*p))
            out[i++] = *p;
        p++;
    }
    out[i] = '\0';
    return out[0] ? 0 : -1;
}

void parse_firmware_line(const char *fw_out, char *out, size_t n)
{
    const char *p = fw_out;
    while (*p && isspace((unsigned char)*p))
        p++;

    /* Skip an echoed command line if present. */
    if (strncmp(p, "simcom", 6) == 0) {
        const char *nl = strchr(p, '\n');
        p = nl ? nl + 1 : p;
        while (*p && isspace((unsigned char)*p))
            p++;
    }

    size_t i = 0;
    while (p[i] && p[i] != '\n' && p[i] != '\r' && i + 1 < n)
        i++;
    while (i > 0 && isspace((unsigned char)p[i - 1]))
        i--;
    if (i == 0) {
        set_na(out, n);
        return;
    }
    memcpy(out, p, i);
    out[i] = '\0';
}

static void format_uptime(int days, int hours, int mins, char *out, size_t n)
{
    if (days <= 0)
        snprintf(out, n, "%02d:%02d", hours, mins);
    else if (days == 1)
        snprintf(out, n, "1 day, %02d:%02d", hours, mins);
    else
        snprintf(out, n, "%d days, %02d:%02d", days, hours, mins);
}

void parse_uptime_hhmm(const char *uptime_out, char *out, size_t n)
{
    /* Examples:
     *   03:08:18 up 5 days, 18:28, load average: ...  →  5 days, 18:28
     *   03:08:18 up 1 day, 3:07, load average: ...    →  1 day, 03:07
     *   03:08:18 up 18:28, load average: ...          →  18:28
     *   03:08:18 up 1 day, 3 min, load average: ...   →  1 day, 00:03
     *   03:08:18 up 7 min, load average: ...          →  00:07
     */
    const char *up = strstr(uptime_out, " up ");
    if (!up) {
        set_na(out, n);
        return;
    }
    up += 4;

    const char *load = strstr(up, "load average");
    char span[128];
    if (load) {
        size_t len = (size_t)(load - up);
        if (len >= sizeof(span))
            len = sizeof(span) - 1;
        memcpy(span, up, len);
        span[len] = '\0';
    } else {
        snprintf(span, sizeof(span), "%s", up);
    }

    int days = 0, hours = 0, mins = 0;
    if (sscanf(span, "%d days, %d:%d", &days, &hours, &mins) == 3 ||
        sscanf(span, "%d day, %d:%d", &days, &hours, &mins) == 3) {
        format_uptime(days, hours, mins, out, n);
        return;
    }
    if (sscanf(span, "%d days, %d min", &days, &mins) == 2 ||
        sscanf(span, "%d day, %d min", &days, &mins) == 2) {
        format_uptime(days, 0, mins, out, n);
        return;
    }
    if (sscanf(span, "%d:%d", &hours, &mins) == 2) {
        format_uptime(0, hours, mins, out, n);
        return;
    }
    if (sscanf(span, "%d min", &mins) == 1) {
        format_uptime(0, 0, mins, out, n);
        return;
    }
    set_na(out, n);
}

void parse_ping_status(const char *ping_out, char *out, size_t n)
{
    /* "3 packets transmitted, 3 packets received, 0% packet loss" */
    const char *p = strstr(ping_out, "packet loss");
    if (!p) {
        snprintf(out, n, "FAIL");
        return;
    }

    const char *pct = p;
    while (pct > ping_out && pct[-1] != ' ' && pct[-1] != ',')
        pct--;
    int loss = atoi(pct);
    if (loss == 0)
        snprintf(out, n, "PASS");
    else
        snprintf(out, n, "FAIL");
}

void fill_from_cellular(const char *ubus_out, MonitorRow *row)
{
    char tmp[MAX_FIELD];

    if (json_get_string(ubus_out, "imei", tmp, sizeof(tmp)) == 0)
        snprintf(row->imei, sizeof(row->imei), "%s", tmp);
    if (json_get_string(ubus_out, "operator", tmp, sizeof(tmp)) == 0)
        snprintf(row->carrier, sizeof(row->carrier), "%s", tmp);
    if (json_get_string(ubus_out, "msisdn", tmp, sizeof(tmp)) == 0)
        snprintf(row->phone, sizeof(row->phone), "%s", tmp);
    if (json_get_string(ubus_out, "rssi", tmp, sizeof(tmp)) == 0)
        snprintf(row->rssi, sizeof(row->rssi), "%s", tmp);

    /* Voicelink Status is desk-phone (manual). Do not map cellular into
     * Voicelink/Fax status. Fax stays FAIL until Faxback queue patch. */
}
