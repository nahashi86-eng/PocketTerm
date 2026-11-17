// PocketTerm Remote Receiver (plain C++ / Win32, no Qt)
//
// Build (MinGW g++):
//   g++ -O2 -std=c++17 Reciever.cpp -o receiver.exe -lws2_32 -lbcrypt -static
//
// Run it on the Windows PC you want to administer. It announces itself on the
// LAN (UDP broadcast) and runs PowerShell commands sent by a client that proves
// it knows the one-session pairing token (HMAC-SHA256 challenge/response).
// By default every command must be approved at this console (y/n) first.
//
// Wire protocol (same as the Python/Qt versions):
//   server -> {"challenge":"<64 hex chars>"}\n
//   client -> {"proof":"<hex hmac_sha256(key=token, msg=challenge)>","command":"..."}\n
//   server -> {"approved":bool,"exit_code":int,"output":str}
//          or {"approved":false,"reason":str}
//
// NOTE: traffic is NOT encrypted. Use only on a trusted private LAN.

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <map>
#include <string>
#include <vector>

static const unsigned short DISCOVERY_PORT = 47630;
static const unsigned short COMMAND_PORT = 47631;
static const size_t MAX_COMMAND_CHARS = 8000;
static const size_t MAX_OUTPUT_BYTES = 1000000;
static const size_t MAX_REQUEST_BYTES = 12000;
static const DWORD COMMAND_TIMEOUT_MS = 120000;

static std::atomic<bool> gStop(false);
static std::string gToken;
static bool gRequireApproval = true;
static CRITICAL_SECTION gLogLock;
static CRITICAL_SECTION gPromptLock;

// ---------------------------------------------------------------- utilities
static bool ntok(LONG status) { return status >= 0; }

static void logMsg(const std::string &msg)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char stamp[16];
    snprintf(stamp, sizeof stamp, "%02d:%02d:%02d  ", t.wHour, t.wMinute, t.wSecond);
    EnterCriticalSection(&gLogLock);
    std::cout << stamp << msg << std::endl;
    LeaveCriticalSection(&gLogLock);
}

static bool randomBytes(unsigned char *buf, size_t n)
{
    return ntok(BCryptGenRandom(nullptr, buf, (ULONG)n, BCRYPT_USE_SYSTEM_PREFERRED_RNG));
}

static std::string toHex(const unsigned char *d, size_t n)
{
    static const char *h = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; ++i) {
        s += h[d[i] >> 4];
        s += h[d[i] & 15];
    }
    return s;
}

static std::string base64(const unsigned char *d, size_t n, bool urlSafe)
{
    const char *tbl = urlSafe
        ? "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        : "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string o;
    size_t i = 0;
    for (; i + 2 < n; i += 3) {
        uint32_t v = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
        o += tbl[v >> 18]; o += tbl[(v >> 12) & 63]; o += tbl[(v >> 6) & 63]; o += tbl[v & 63];
    }
    if (i + 1 == n) {
        uint32_t v = d[i] << 16;
        o += tbl[v >> 18]; o += tbl[(v >> 12) & 63];
        if (!urlSafe) o += "==";
    } else if (i + 2 == n) {
        uint32_t v = (d[i] << 16) | (d[i + 1] << 8);
        o += tbl[v >> 18]; o += tbl[(v >> 12) & 63]; o += tbl[(v >> 6) & 63];
        if (!urlSafe) o += "=";
    }
    return o;
}

static bool hmacSha256(const std::string &key, const std::string &msg, unsigned char out[32])
{
    BCRYPT_ALG_HANDLE alg = nullptr;
    if (!ntok(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr,
                                          BCRYPT_ALG_HANDLE_HMAC_FLAG)))
        return false;
    BCRYPT_HASH_HANDLE h = nullptr;
    bool ok = ntok(BCryptCreateHash(alg, &h, nullptr, 0, (PUCHAR)key.data(), (ULONG)key.size(), 0))
           && ntok(BCryptHashData(h, (PUCHAR)msg.data(), (ULONG)msg.size(), 0))
           && ntok(BCryptFinishHash(h, out, 32, 0));
    if (h) BCryptDestroyHash(h);
    BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

static bool constantTimeEqual(const std::string &a, const std::string &b)
{
    if (a.size() != b.size()) return false;
    unsigned char diff = 0;
    for (size_t i = 0; i < a.size(); ++i) diff |= (unsigned char)a[i] ^ (unsigned char)b[i];
    return diff == 0;
}

// Replace invalid UTF-8 sequences with U+FFFD (like Python's errors="replace").
static std::string sanitizeUtf8(const std::string &s)
{
    std::string o;
    size_t i = 0, n = s.size();
    while (i < n) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x80) { o += (char)c; ++i; continue; }
        int len = 0;
        uint32_t cp = 0;
        if (c >= 0xC2 && c <= 0xDF) { len = 2; cp = c & 0x1F; }
        else if (c >= 0xE0 && c <= 0xEF) { len = 3; cp = c & 0x0F; }
        else if (c >= 0xF0 && c <= 0xF4) { len = 4; cp = c & 0x07; }
        bool good = len && i + len <= n;
        if (good) {
            for (int k = 1; k < len; ++k) {
                unsigned char d = (unsigned char)s[i + k];
                if ((d & 0xC0) != 0x80) { good = false; break; }
                cp = (cp << 6) | (d & 0x3F);
            }
        }
        if (good && ((len == 3 && cp < 0x800) || (len == 4 && cp < 0x10000) ||
                     cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)))
            good = false;
        if (good) { o.append(s, i, len); i += len; }
        else { o += "\xEF\xBF\xBD"; ++i; }
    }
    return o;
}

static size_t utf8Length(const std::string &s)
{
    size_t n = 0;
    for (unsigned char c : s) if ((c & 0xC0) != 0x80) ++n;
    return n;
}

static std::string trim(const std::string &s)
{
    const char *ws = " \t\r\n\f\v";
    size_t a = s.find_first_not_of(ws);
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(ws);
    return s.substr(a, b - a + 1);
}

// ---------------------------------------------------------------- JSON
static std::string jsonEscape(const std::string &raw)
{
    std::string s = sanitizeUtf8(raw), o = "\"";
    for (unsigned char c : s) {
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        default:
            if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
            else o += (char)c;
        }
    }
    return o + "\"";
}

static void appendUtf8(std::string &o, uint32_t cp)
{
    if (cp < 0x80) o += (char)cp;
    else if (cp < 0x800) { o += (char)(0xC0 | (cp >> 6)); o += (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) {
        o += (char)(0xE0 | (cp >> 12)); o += (char)(0x80 | ((cp >> 6) & 0x3F));
        o += (char)(0x80 | (cp & 0x3F));
    } else {
        o += (char)(0xF0 | (cp >> 18)); o += (char)(0x80 | ((cp >> 12) & 0x3F));
        o += (char)(0x80 | ((cp >> 6) & 0x3F)); o += (char)(0x80 | (cp & 0x3F));
    }
}

static bool parseHex4(const std::string &s, size_t pos, uint32_t &v)
{
    if (pos + 4 > s.size()) return false;
    v = 0;
    for (size_t i = 0; i < 4; ++i) {
        char c = s[pos + i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
        else return false;
    }
    return true;
}

static bool parseJsonString(const std::string &s, size_t &p, std::string &out)
{
    if (p >= s.size() || s[p] != '"') return false;
    ++p;
    out.clear();
    while (p < s.size()) {
        char c = s[p++];
        if (c == '"') return true;
        if (c != '\\') { out += c; continue; }
        if (p >= s.size()) return false;
        char e = s[p++];
        switch (e) {
        case '"': out += '"'; break;
        case '\\': out += '\\'; break;
        case '/': out += '/'; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'u': {
            uint32_t cp;
            if (!parseHex4(s, p, cp)) return false;
            p += 4;
            if (cp >= 0xD800 && cp <= 0xDBFF && p + 6 <= s.size() && s[p] == '\\' && s[p + 1] == 'u') {
                uint32_t lo;
                if (parseHex4(s, p + 2, lo) && lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    p += 6;
                }
            }
            if (cp >= 0xD800 && cp <= 0xDFFF) cp = 0xFFFD;
            appendUtf8(out, cp);
            break;
        }
        default: return false;
        }
    }
    return false;
}

static void skipWs(const std::string &s, size_t &p)
{
    while (p < s.size() && (s[p] == ' ' || s[p] == '\t' || s[p] == '\r' || s[p] == '\n')) ++p;
}

static bool skipValue(const std::string &s, size_t &p)
{
    int depth = 0;
    while (p < s.size()) {
        char c = s[p];
        if (c == '"') { std::string tmp; if (!parseJsonString(s, p, tmp)) return false; continue; }
        if (c == '{' || c == '[') ++depth;
        else if (c == '}' || c == ']') { if (depth == 0) return true; --depth; }
        else if (c == ',' && depth == 0) return true;
        ++p;
    }
    return false;
}

// Parses a flat JSON object; keeps only string-valued members.
static bool parseJsonObject(const std::string &s, std::map<std::string, std::string> &out)
{
    size_t p = 0;
    skipWs(s, p);
    if (p >= s.size() || s[p] != '{') return false;
    ++p;
    for (;;) {
        skipWs(s, p);
        if (p < s.size() && s[p] == '}') return true;
        std::string key;
        if (!parseJsonString(s, p, key)) return false;
        skipWs(s, p);
        if (p >= s.size() || s[p] != ':') return false;
        ++p;
        skipWs(s, p);
        if (p < s.size() && s[p] == '"') {
            std::string val;
            if (!parseJsonString(s, p, val)) return false;
            out[key] = val;
        } else if (!skipValue(s, p)) return false;
        skipWs(s, p);
        if (p >= s.size()) return false;
        if (s[p] == ',') { ++p; continue; }
        return s[p] == '}';
    }
}

// ---------------------------------------------------------------- sockets
static bool sendAll(SOCKET s, const std::string &d)
{
    size_t sent = 0;
    while (sent < d.size()) {
        int n = send(s, d.data() + sent, (int)(d.size() - sent), 0);
        if (n <= 0) return false;
        sent += n;
    }
    return true;
}

static void sendRefusal(SOCKET s, const std::string &reason)
{
    sendAll(s, "{\"approved\":false,\"reason\":" + jsonEscape(reason) + "}");
}

static void sendResult(SOCKET s, int exitCode, const std::string &output)
{
    sendAll(s, "{\"approved\":true,\"exit_code\":" + std::to_string(exitCode) +
               ",\"output\":" + jsonEscape(output) + "}");
}

// ---------------------------------------------------------------- run command
static std::string base64Utf16(const std::string &utf8)
{
    int wl = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), nullptr, 0);
    std::vector<wchar_t> w(wl > 0 ? wl : 1);
    if (wl > 0) MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), w.data(), wl);
    return base64((const unsigned char *)w.data(), (size_t)wl * sizeof(wchar_t), false);
}

static int runPowerShell(const std::string &command, std::string &output)
{
    output.clear();
    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) {
        output = "Execution error: could not create pipe";
        return -1;
    }
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                             OPEN_EXISTING, 0, nullptr);

    // -EncodedCommand (base64 UTF-16LE) avoids every command-line quoting problem.
    std::string enc = base64Utf16(command);
    std::wstring cmdline = L"powershell.exe -NoLogo -NoProfile -NonInteractive "
                           L"-ExecutionPolicy RemoteSigned -EncodedCommand " +
                           std::wstring(enc.begin(), enc.end());

    STARTUPINFOW si{};
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = nul;
    si.hStdOutput = wr;
    si.hStdError = wr;
    PROCESS_INFORMATION pi{};
    BOOL created = CreateProcessW(nullptr, &cmdline[0], nullptr, nullptr, TRUE,
                                  CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    DWORD err = GetLastError();
    CloseHandle(wr);
    if (nul != INVALID_HANDLE_VALUE) CloseHandle(nul);
    if (!created) {
        CloseHandle(rd);
        output = "Execution error: CreateProcess failed (" + std::to_string(err) + ")";
        return -1;
    }

    auto readAvail = [&]() -> bool {
        DWORD avail = 0;
        if (!PeekNamedPipe(rd, nullptr, 0, nullptr, &avail, nullptr) || avail == 0) return false;
        char buf[4096];
        DWORD got = 0;
        if (!ReadFile(rd, buf, avail < sizeof buf ? avail : (DWORD)sizeof buf, &got, nullptr) || !got)
            return false;
        if (output.size() < MAX_OUTPUT_BYTES) output.append(buf, got);   // keep draining past the cap
        return true;
    };

    bool timedOut = false;
    DWORD start = GetTickCount();
    for (;;) {
        if (readAvail()) continue;
        if (WaitForSingleObject(pi.hProcess, 50) == WAIT_OBJECT_0) {
            while (readAvail()) {}
            break;
        }
        if (GetTickCount() - start > COMMAND_TIMEOUT_MS) {
            timedOut = true;
            TerminateProcess(pi.hProcess, 1);
            break;
        }
    }

    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    CloseHandle(rd);

    if (timedOut) {
        output = "Command timed out after 120 seconds.";
        return -1;
    }
    if (output.size() > MAX_OUTPUT_BYTES) output.resize(MAX_OUTPUT_BYTES);
    return (int)code;
}

// ---------------------------------------------------------------- per-client
static bool askApproval(const std::string &addr, const std::string &command)
{
    EnterCriticalSection(&gPromptLock);
    EnterCriticalSection(&gLogLock);
    std::cout << "\n==================== APPROVAL NEEDED ====================\n"
              << "A paired client at " << addr << " wants to run this PowerShell command:\n\n"
              << sanitizeUtf8(command) << "\n\nRun it? [y/N]: " << std::flush;
    LeaveCriticalSection(&gLogLock);
    std::string line;
    std::getline(std::cin, line);
    LeaveCriticalSection(&gPromptLock);
    line = trim(line);
    return line == "y" || line == "Y" || line == "yes" || line == "YES";
}

struct ClientArg { SOCKET sock; std::string addr; };

static DWORD WINAPI clientThread(LPVOID param)
{
    ClientArg *arg = (ClientArg *)param;
    SOCKET s = arg->sock;
    std::string addr = arg->addr;
    delete arg;

    DWORD timeout = 10000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&timeout, sizeof timeout);

    unsigned char raw[32];
    if (!randomBytes(raw, sizeof raw)) { closesocket(s); return 0; }
    std::string challenge = toHex(raw, sizeof raw);
    sendAll(s, "{\"challenge\":\"" + challenge + "\"}\n");

    std::string data;
    char buf[2048];
    while (data.find('\n') == std::string::npos && data.size() <= MAX_REQUEST_BYTES) {
        int n = recv(s, buf, sizeof buf, 0);
        if (n <= 0) break;
        data.append(buf, n);
    }
    size_t nl = data.find('\n');
    if (nl == std::string::npos) {
        sendRefusal(s, data.size() > MAX_REQUEST_BYTES ? "Request too large"
                                                       : "Timed out waiting for request");
        logMsg("Request error from " + addr + ": incomplete request");
        shutdown(s, SD_SEND); closesocket(s);
        return 0;
    }

    std::map<std::string, std::string> req;
    if (!parseJsonObject(data.substr(0, nl), req)) {
        sendRefusal(s, "Malformed request");
        logMsg("Request error from " + addr + ": malformed JSON");
        shutdown(s, SD_SEND); closesocket(s);
        return 0;
    }

    unsigned char mac[32];
    std::string expected = hmacSha256(gToken, challenge, mac) ? toHex(mac, 32) : "";
    std::string proof = req.count("proof") ? req["proof"] : "";
    std::string command = trim(sanitizeUtf8(req.count("command") ? req["command"] : ""));

    if (expected.empty() || !constantTimeEqual(proof, expected)) {
        sendRefusal(s, "Invalid pairing token");
        logMsg("Rejected unauthenticated connection from " + addr + ".");
    } else if (command.empty() || utf8Length(command) > MAX_COMMAND_CHARS) {
        sendRefusal(s, "Invalid command length");
    } else {
        bool go = true;
        if (gRequireApproval) {
            logMsg("Command from " + addr + " awaiting approval.");
            go = askApproval(addr, command);
        }
        if (!go) {
            logMsg("Command from " + addr + " denied by user.");
            sendRefusal(s, "Command denied at the receiver");
        } else {
            logMsg("Authenticated command received from " + addr + "; executing.");
            std::string output;
            int code = runPowerShell(command, output);
            sendResult(s, code, output);
        }
    }
    shutdown(s, SD_SEND);
    closesocket(s);
    return 0;
}

// ---------------------------------------------------------------- beacon
static DWORD WINAPI beaconThread(LPVOID)
{
    SOCKET u = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (u == INVALID_SOCKET) { logMsg("Discovery warning: could not create UDP socket."); return 0; }
    BOOL yes = TRUE;
    setsockopt(u, SOL_SOCKET, SO_BROADCAST, (const char *)&yes, sizeof yes);

    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(DISCOVERY_PORT);
    dst.sin_addr.s_addr = INADDR_BROADCAST;

    char host[256] = "unknown";
    gethostname(host, sizeof host - 1);
    std::string beacon = "{\"type\":\"pocketterm_receiver\",\"name\":" + jsonEscape(host) + "}";

    bool warned = false;
    while (!gStop) {
        if (sendto(u, beacon.data(), (int)beacon.size(), 0, (sockaddr *)&dst, sizeof dst) < 0 && !warned) {
            warned = true;
            logMsg("Discovery warning: broadcast failed (error " + std::to_string(WSAGetLastError()) + ").");
        }
        for (int i = 0; i < 20 && !gStop; ++i) Sleep(100);
    }
    closesocket(u);
    return 0;
}

static BOOL WINAPI ctrlHandler(DWORD)
{
    gStop = true;
    return TRUE;
}

// ---------------------------------------------------------------- main
int main()
{
    SetConsoleOutputCP(CP_UTF8);
    InitializeCriticalSection(&gLogLock);
    InitializeCriticalSection(&gPromptLock);
    SetConsoleCtrlHandler(ctrlHandler, TRUE);

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { std::cerr << "WSAStartup failed\n"; return 1; }

    unsigned char t[24];
    if (!randomBytes(t, sizeof t)) { std::cerr << "Could not generate token\n"; return 1; }
    gToken = base64(t, sizeof t, true);

    std::cout << "PocketTerm Remote Receiver\n"
              << "--------------------------\n"
              << "This PC will be discoverable on your LAN. Keep it on a trusted private LAN;\n"
              << "command text and output are not encrypted in transit.\n\n"
              << "One-session pairing token:\n\n    " << gToken << "\n\n"
              << "Require your approval (y/n) for each command? [Y/n]: " << std::flush;
    std::string answer;
    std::getline(std::cin, answer);
    answer = trim(answer);
    gRequireApproval = !(answer == "n" || answer == "N" || answer == "no" || answer == "NO");
    if (!gRequireApproval)
        std::cout << "WARNING: commands will run immediately after token authentication.\n";

    SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    BOOL reuse = TRUE;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, (const char *)&reuse, sizeof reuse);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(COMMAND_PORT);
    addr.sin_addr.s_addr = INADDR_ANY;
    if (bind(listener, (sockaddr *)&addr, sizeof addr) != 0 || listen(listener, 8) != 0) {
        logMsg("Could not start listener (error " + std::to_string(WSAGetLastError()) + ").");
        WSACleanup();
        return 1;
    }
    logMsg("Listening on TCP port " + std::to_string(COMMAND_PORT) + ". Press Ctrl+C to stop.");

    HANDLE beacon = CreateThread(nullptr, 0, beaconThread, nullptr, 0, nullptr);

    while (!gStop) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(listener, &fds);
        timeval tv{1, 0};
        int r = select(0, &fds, nullptr, nullptr, &tv);
        if (r <= 0) continue;
        sockaddr_in peer{};
        int plen = sizeof peer;
        SOCKET c = accept(listener, (sockaddr *)&peer, &plen);
        if (c == INVALID_SOCKET) continue;
        const char *ipStr = inet_ntoa(peer.sin_addr);
        std::string ip = ipStr ? ipStr : "?";
        HANDLE th = CreateThread(nullptr, 0, clientThread, new ClientArg{c, ip}, 0, nullptr);
        if (th) CloseHandle(th);
    }

    closesocket(listener);
    if (beacon) { WaitForSingleObject(beacon, 3000); CloseHandle(beacon); }
    WSACleanup();
    return 0;
}
