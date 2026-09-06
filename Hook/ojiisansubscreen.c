#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "cJSON.h"

static HMODULE module;

static void report(const wchar_t *directory, const char *message) {
    wchar_t path[32768];
    swprintf(path, 32768, L"%ls\\ojiisansubscreen.log", directory);
    FILE *f = _wfopen(path, L"ab");
    if (f) { fprintf(f, "%s\r\n", message); fclose(f); }
    OutputDebugStringA(message);
}

static int regular_file(const wchar_t *path) {
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static wchar_t *utf16(const char *s) {
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, NULL, 0);
    if (!n) return NULL;
    wchar_t *out = calloc(n, sizeof(wchar_t));
    if (out) MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, out, n);
    return out;
}

// Accept pasted Windows paths without changing the rest of JSON parsing.
// Quotes cannot occur in a Windows directory name, so the next quote ends it.
static char *normalize_directory(const char *text) {
    const char *key = strstr(text, "\"subscreen application directory\"");
    if (!key) return NULL;
    const char *start = strchr(key + strlen("\"subscreen application directory\""), ':');
    if (!start) return NULL;
    start++;
    while (*start == ' ' || *start == '\t' || *start == '\r' || *start == '\n') start++;
    if (*start != '"') return NULL;
    start++;
    const char *end = strchr(start, '"');
    if (!end) return NULL;
    char *result = calloc(strlen(text)*2 + 1, 1);
    if (!result) return NULL;
    size_t used = (size_t)(start-text);
    memcpy(result, text, used);
    for (const char *p = start; p < end; p++) {
        if (*p == '\\') {
            result[used++] = '\\'; result[used++] = '\\';
            if (p+1 < end && p[1] == '\\') p++;
        } else result[used++] = *p;
    }
    strcpy(result+used, end);
    return result;
}

static DWORD WINAPI start_subscreen(void *unused) {
    (void)unused;
    wchar_t *directory = calloc(32768, sizeof(wchar_t));
    wchar_t *path = calloc(32768, sizeof(wchar_t));
    wchar_t *python = calloc(32768, sizeof(wchar_t));
    wchar_t *script = calloc(32768, sizeof(wchar_t));
    wchar_t *command = calloc(32768, sizeof(wchar_t));
    wchar_t *appdir = NULL;
    cJSON *json = NULL;
    char *buffer = NULL;
    FILE *file = NULL;
    if (!directory || !path || !python || !script || !command) goto done;
    DWORD length = GetModuleFileNameW(module, directory, 32768);
    if (!length || length >= 32768) goto done;
    wchar_t *slash = wcsrchr(directory, L'\\');
    if (!slash) goto done;
    *slash = 0;
    swprintf(path, 32768, L"%ls\\config.json", directory);
    file = _wfopen(path, L"rb");
    if (!file) { report(directory, "Cannot open config.json beside DLL."); goto done; }
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    rewind(file);
    if (size <= 0 || size > 65536) { report(directory, "Invalid config size."); goto done; }
    buffer = calloc((size_t)size + 1, 1);
    if (!buffer || fread(buffer, 1, size, file) != (size_t)size) goto done;
    fclose(file); file = NULL;
    const char *text = buffer;
    if (size >= 3 && (unsigned char)text[0] == 0xef && (unsigned char)text[1] == 0xbb && (unsigned char)text[2] == 0xbf) text += 3;
    char *normalized = normalize_directory(text);
    json = cJSON_ParseWithOpts(normalized ? normalized : text, NULL, 1);
    free(normalized);
    cJSON *mode = cJSON_GetObjectItemCaseSensitive(json, "game mode");
    cJSON *app = cJSON_GetObjectItemCaseSensitive(json, "subscreen application directory");
    if (!cJSON_IsObject(json) || !cJSON_IsString(app) ||
        (mode && (!cJSON_IsString(mode) || (strcmp(mode->valuestring, "iidx") && strcmp(mode->valuestring, "sdvx") && strcmp(mode->valuestring, "auto"))))) {
        report(directory, "Config needs subscreen application directory; optional game mode is iidx/sdvx/auto."); goto done;
    }
    appdir = utf16(app->valuestring);
    // Require an absolute directory; quoted paths cannot be valid Windows filenames.
    if (!appdir || wcslen(appdir) > 10000 || wcschr(appdir, L'"') ||
        !(wcslen(appdir) >= 3 && appdir[1] == L':' && (appdir[2] == L'\\' || appdir[2] == L'/'))) {
        report(directory, "Application directory must be an absolute drive path."); goto done;
    }
    const wchar_t *mode_args = L"";
    if (cJSON_IsString(mode) && strcmp(mode->valuestring, "iidx") == 0) mode_args = L"--game-mode iidx ";
    if (cJSON_IsString(mode) && strcmp(mode->valuestring, "sdvx") == 0) mode_args = L"--game-mode sdvx ";
    swprintf(python, 32768, L"%ls\\OjiisanSubscreen.exe", appdir);
    if (regular_file(python)) {
        swprintf(command, 32768, L"\"%ls\" %ls--parent-pid %lu", python, mode_args, GetCurrentProcessId());
    } else {
    swprintf(script, 32768, L"%ls\\hook_start.py", appdir);
    if (!regular_file(script)) { report(directory, "hook_start.py not found in application directory."); goto done; }
    swprintf(python, 32768, L"%ls\\.venv\\Scripts\\python.exe", appdir);
    if (!regular_file(python)) swprintf(python, 32768, L"%ls\\.runtime\\python.exe", appdir);
    if (!regular_file(python)) {
        DWORD n = GetEnvironmentVariableW(L"USERPROFILE", path, 32768);
        if (n && n < 30000) swprintf(python, 32768,
            L"%ls\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe", path);
    }
    BOOL launcher = FALSE;
    if (!regular_file(python)) {
        DWORD n = SearchPathW(NULL, L"py.exe", NULL, 32768, python, NULL);
        if (!n || n >= 32768) { report(directory, "Python not found. Run QUICK_START.bat once to set up the app."); goto done; }
        launcher = TRUE;
    }
    swprintf(command, 32768, L"\"%ls\" %ls\"%ls\" %ls--parent-pid %lu",
        python, launcher ? L"-3 " : L"", script, mode_args, GetCurrentProcessId());
    }
    STARTUPINFOW si = {0}; PROCESS_INFORMATION pi = {0};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    if (CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, appdir, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        report(directory, "Subscreen launch requested; see application subscreen-launch.log for application errors.");
    } else {
        DWORD error = GetLastError();
        char message[128];
        snprintf(message, sizeof(message), "Failed to create subscreen process. (Windows error %lu)", error);
        report(directory, message);
    }
done:
    if (file) fclose(file);
    cJSON_Delete(json);
    free(buffer); free(appdir); free(directory); free(path); free(python); free(script); free(command);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        module = instance;
        DisableThreadLibraryCalls(instance);
        // No config I/O, Qt, process creation, or waiting under the loader lock.
        HANDLE worker = CreateThread(NULL, 0, start_subscreen, NULL, 0, NULL);
        if (worker) CloseHandle(worker);
    }
    return TRUE;
}
