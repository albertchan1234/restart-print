# ============================================================
# Restart Print Spooler + QZ Tray + Check Vita Printers
# Version 1.2.0
# ============================================================

import subprocess
import time
import os
import sys
import json
import ctypes
import traceback
import urllib.request
from datetime import datetime

APP_VERSION = "1.2.0"
GITHUB_OWNER = "albertchan1234"
GITHUB_REPO = "restart-print"
GITHUB_EXE_ASSET = "Restart_Print_QZ.exe"
UPDATE_TIMEOUT = 12

SPOOLER_TIMEOUT_SEC = 25
QZ_KILL_TIMEOUT_SEC = 15
QZ_START_WAIT_SEC = 8
EVENT_LOOKBACK_HOURS = 6
EVENT_MAX_ITEMS = 15
QZ_LOG_TAIL_LINES = 40


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_cmd(cmd, timeout=30):
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )
        return result.returncode == 0, result.stdout, result.stderr, False
    except subprocess.TimeoutExpired:
        return False, "", f"TIMEOUT after {timeout} seconds", True
    except Exception as e:
        return False, "", str(e), False


def parse_version(text):
    parts = []
    for piece in str(text).strip().lstrip("vV").split("."):
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote_version, local_version):
    return parse_version(remote_version) > parse_version(local_version)


def https_get_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"RestartPrintQZ/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def https_download_file(url, dest_path):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"RestartPrintQZ/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, "wb") as out:
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            out.write(chunk)


def running_as_exe():
    return bool(getattr(sys, "frozen", False))


def check_git_source_update():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(script_dir, ".git")):
        return "skip", "Not a git repository."

    ok, stdout, stderr, _timed_out = run_cmd(f'git -C "{script_dir}" pull origin main')
    text = ((stdout or "") + "\n" + (stderr or "")).strip()
    if not ok:
        return "error", text or "git pull failed."
    if "Already up to date" in text or "Already up-to-date" in text:
        return "current", text
    return "updated", text


def apply_exe_update(current_exe, new_exe):
    bat_path = os.path.join(os.path.dirname(current_exe), "_update_restart_print.bat")
    pid = os.getpid()
    bat = f"""@echo off
setlocal
:wait
timeout /t 1 /nobreak >nul
tasklist /FI "PID eq {pid}" | find "{pid}" >nul
if not errorlevel 1 goto wait
copy /Y "{new_exe}" "{current_exe}" >nul
if exist "{current_exe}" start "" "{current_exe}"
del "{new_exe}" >nul 2>&1
del "%~f0" >nul 2>&1
"""
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat)
    subprocess.Popen(["cmd.exe", "/c", bat_path], close_fds=True)
    print("    Update downloaded. Restarting with the new version...")
    time.sleep(1)
    sys.exit(0)


def check_exe_update():
    api = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    try:
        data = https_get_json(api)
    except Exception as e:
        return "error", f"Cannot reach GitHub: {e}"

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return "error", "Latest GitHub release has no tag."

    if not is_newer(tag, APP_VERSION):
        return "current", f"GitHub latest is {tag}."

    asset_url = None
    for asset in data.get("assets") or []:
        if asset.get("name") == GITHUB_EXE_ASSET:
            asset_url = asset.get("browser_download_url")
            break

    if not asset_url:
        asset_url = (
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/releases/latest/download/{GITHUB_EXE_ASSET}"
        )

    current_exe = sys.executable
    new_exe = os.path.join(os.path.dirname(current_exe), "Restart_Print_QZ_new.exe")
    try:
        print(f"    New version found: {tag} (this app is {APP_VERSION})")
        print("    Downloading update over HTTPS...")
        https_download_file(asset_url, new_exe)
        if not os.path.isfile(new_exe) or os.path.getsize(new_exe) < 1000:
            raise RuntimeError("Downloaded file is missing or too small.")
    except Exception as e:
        try:
            if os.path.exists(new_exe):
                os.remove(new_exe)
        except Exception:
            pass
        return "error", f"Download failed: {e}"

    apply_exe_update(current_exe, new_exe)
    return "updated", tag


def check_for_updates():
    print(f"[0] Checking for updates (version {APP_VERSION})...")
    if running_as_exe():
        status, detail = check_exe_update()
    else:
        status, detail = check_git_source_update()

    if status == "current":
        print(f"    Already up to date. {detail}")
    elif status == "updated":
        print("    Source updated from GitHub.")
        print("    Restarting this script so the new code is used...")
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    elif status == "skip":
        print(f"    Update check skipped. {detail}")
    else:
        print(f"    Update check failed: {detail}")
        print("    Continue with the current version.")
    print()


def get_service_status(service_name="Spooler"):
    success, stdout, _stderr, _timed_out = run_cmd(f"sc query {service_name}")
    if "RUNNING" in stdout:
        return "RUNNING"
    elif "STOPPED" in stdout:
        return "STOPPED"
    return "UNKNOWN"


def is_process_running(process_name):
    success, stdout, _stderr, _timed_out = run_cmd(f'tasklist /FI "IMAGENAME eq {process_name}"')
    return process_name.lower() in stdout.lower()


def is_qz_running():
    if is_process_running("qz-tray.exe") or is_process_running("qz-tray-console.exe"):
        return True

    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*qz-tray*' } | "
        "Select-Object -First 1 ProcessId"
    )
    success, stdout, _stderr, _timed_out = run_cmd(f'powershell -NoProfile -Command "{ps_cmd}"')
    if success and stdout.strip() and any(c.isdigit() for c in stdout):
        return True

    success, stdout, _stderr, _timed_out = run_cmd(
        'netstat -ano | findstr ":8181 :8182 :8282 :8383 :8484"'
    )
    if success and "LISTENING" in stdout.upper():
        return True

    return False


def restart_spooler_with_timer():
    info = {
        "status_before": get_service_status("Spooler"),
        "stop_ok": False,
        "stop_timed_out": False,
        "start_ok": False,
        "start_timed_out": False,
        "clear_jobs_ok": False,
        "status_after": "UNKNOWN",
        "result": "FAILED",
        "error": "",
        "seconds_used": 0,
    }

    started = time.time()
    print(f"    Timer: stop/start must finish within {SPOOLER_TIMEOUT_SEC} seconds each")

    print("[2] Stopping Print Spooler...")
    stop_ok, _out, stop_err, stop_timeout = run_cmd("net stop spooler", timeout=SPOOLER_TIMEOUT_SEC)
    info["stop_ok"] = stop_ok
    info["stop_timed_out"] = stop_timeout
    if stop_timeout:
        info["error"] = "Print Spooler STOP was stuck and timed out."
        print(f"    STOP timed out after {SPOOLER_TIMEOUT_SEC} seconds.")
    elif not stop_ok:
        info["error"] = stop_err.strip() or "Failed to stop Print Spooler."
        print(f"    STOP failed: {info['error']}")

    time.sleep(2)

    print("[3] Clearing stuck print jobs...")
    clear_ok, _out, clear_err, clear_timeout = run_cmd(
        r'del /Q /F /S "%systemroot%\System32\spool\PRINTERS\*.*"',
        timeout=15,
    )
    info["clear_jobs_ok"] = clear_ok and not clear_timeout
    if clear_timeout:
        print("    Clearing print jobs timed out.")

    print("[4] Starting Print Spooler...")
    start_ok, _out, start_err, start_timeout = run_cmd("net start spooler", timeout=SPOOLER_TIMEOUT_SEC)
    info["start_ok"] = start_ok
    info["start_timed_out"] = start_timeout
    if start_timeout:
        info["error"] = "Print Spooler START was stuck and timed out."
        print(f"    START timed out after {SPOOLER_TIMEOUT_SEC} seconds.")
    elif not start_ok:
        info["error"] = start_err.strip() or "Failed to start Print Spooler."
        print(f"    START failed: {info['error']}")

    time.sleep(2)
    info["status_after"] = get_service_status("Spooler")
    info["seconds_used"] = round(time.time() - started, 1)

    if info["status_after"] == "RUNNING" and not info["stop_timed_out"] and not info["start_timed_out"]:
        info["result"] = "OK"
    else:
        info["result"] = "FAILED"

    return info


def restart_qz_with_timer():
    info = {
        "path": r"C:\Program Files\QZ Tray\qz-tray.exe",
        "path_found": False,
        "kill_timed_out": False,
        "start_error": "",
        "process_running": False,
        "result": "FAILED",
        "seconds_used": 0,
    }

    started = time.time()
    qz_path = info["path"]
    info["path_found"] = os.path.exists(qz_path)

    print("[5] Stopping QZ Tray...")
    _ok1, _o1, _e1, t1 = run_cmd(
        'wmic process where "Name like \'%java%\' and CommandLine like \'%qz-tray.jar%\'" call terminate',
        timeout=QZ_KILL_TIMEOUT_SEC,
    )
    _ok2, _o2, _e2, t2 = run_cmd("taskkill /F /IM qz-tray.exe /T", timeout=QZ_KILL_TIMEOUT_SEC)
    _ok3, _o3, _e3, t3 = run_cmd("taskkill /F /IM qz-tray-console.exe /T", timeout=QZ_KILL_TIMEOUT_SEC)
    info["kill_timed_out"] = t1 or t2 or t3
    if info["kill_timed_out"]:
        print(f"    QZ Tray stop timed out after {QZ_KILL_TIMEOUT_SEC} seconds.")
    time.sleep(2)

    print("[6] Starting QZ Tray...")
    if info["path_found"]:
        try:
            subprocess.Popen([qz_path], shell=False)
            print(f"    Waiting {QZ_START_WAIT_SEC} seconds for QZ Tray to load...")
            time.sleep(QZ_START_WAIT_SEC)
            info["process_running"] = is_qz_running()
        except Exception as e:
            info["start_error"] = str(e)
            print(f"    Error starting QZ Tray: {e}")
            print(traceback.format_exc())
    else:
        print("    QZ Tray path not found!")

    info["seconds_used"] = round(time.time() - started, 1)
    if info["path_found"] and info["process_running"] and not info["kill_timed_out"]:
        info["result"] = "OK"
    else:
        info["result"] = "FAILED"
    return info


def get_print_event_logs():
    events = []
    ps_script = (
        f"$start = (Get-Date).AddHours(-{EVENT_LOOKBACK_HOURS}); "
        "$logs = @('System','Application','Microsoft-Windows-PrintService/Admin','Microsoft-Windows-PrintService/Operational'); "
        "$items = @(); "
        "foreach ($log in $logs) { "
        "  try { "
        f"    $ev = Get-WinEvent -FilterHashtable @{{LogName=$log; StartTime=$start}} -MaxEvents 120 -ErrorAction SilentlyContinue | "
        "          Where-Object { "
        "            ($_.LevelDisplayName -in @('Critical','Error','Warning')) -and "
        "            ($_.ProviderName -match 'Print|Spooler|QZ|Java' -or $_.Message -match 'print|spooler|printer|qz tray') "
        "          }; "
        "    if ($ev) { $items += $ev } "
        "  } catch {} "
        "} "
        f"$items | Sort-Object TimeCreated -Descending | Select-Object -First {EVENT_MAX_ITEMS} "
        "TimeCreated, Id, LevelDisplayName, ProviderName, Message | "
        "ForEach-Object { "
        "  $_ | Add-Member -NotePropertyName TimeText -NotePropertyValue $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') -Force; $_ "
        "} | Select-Object TimeText, Id, LevelDisplayName, ProviderName, Message | ConvertTo-Json -Compress"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="ignore",
        )
        stdout = (result.stdout or "").strip()
        if not stdout:
            return events
        data = json.loads(stdout)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            msg = str(item.get("Message") or "").replace("\r", " ").replace("\n", " ").strip()
            events.append({
                "time": item.get("TimeText", ""),
                "event_id": item.get("Id", ""),
                "level": item.get("LevelDisplayName", ""),
                "source": item.get("ProviderName", ""),
                "message": msg[:400],
            })
    except Exception as e:
        events.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_id": "",
            "level": "Error",
            "source": "script",
            "message": f"Failed to read Event Viewer: {e}",
        })
    return events


def get_qz_log_tail():
    log_dir = os.path.join(os.environ.get("APPDATA", ""), "qz")
    if not os.path.isdir(log_dir):
        return {"found": False, "path": log_dir, "lines": []}

    candidates = []
    for name in os.listdir(log_dir):
        lower = name.lower()
        if lower.endswith(".log") or "debug" in lower or "error" in lower:
            path = os.path.join(log_dir, name)
            if os.path.isfile(path):
                candidates.append((os.path.getmtime(path), path))

    if not candidates:
        return {"found": False, "path": log_dir, "lines": []}

    latest = sorted(candidates, reverse=True)[0][1]
    try:
        with open(latest, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-QZ_LOG_TAIL_LINES:]
        return {
            "found": True,
            "path": latest,
            "lines": [line.rstrip() for line in lines if line.strip()],
        }
    except Exception as e:
        return {"found": False, "path": latest, "lines": [f"Cannot read QZ log: {e}"]}


def get_vita_printers():
    printers = []
    ps_script = (
        "Get-CimInstance Win32_Printer | Where-Object {$_.Name -like 'Vita_*'} | "
        "Select-Object Name, PrinterStatus, PortName, DriverName, WorkOffline, DetectedErrorState | "
        "ConvertTo-Json -Compress"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="ignore",
        )

        stdout = result.stdout.strip()
        if not stdout:
            return printers

        data = json.loads(stdout)
        if isinstance(data, dict):
            data = [data]

        status_map = {
            1: "Other",
            2: "Unknown",
            3: "Idle",
            4: "Printing",
            5: "Warmup",
            6: "Stopped Printing",
            7: "Offline / Error",
        }

        for p in data:
            name = p.get("Name", "")
            status_code = p.get("PrinterStatus", 1)
            port = p.get("PortName", "")
            driver = p.get("DriverName", "")
            work_offline = p.get("WorkOffline", False)
            detected_error = p.get("DetectedErrorState", 0)

            port_upper = str(port).upper()
            if port_upper.startswith("USB") or "DOT4" in port_upper or "LPT" in port_upper:
                connection_type = "USB"
            elif port_upper.startswith("IP_") or "WSD" in port_upper or "." in port_upper:
                connection_type = "Network"
            else:
                connection_type = "Other"

            if work_offline:
                status_text = "Offline"
                has_error = True
            elif detected_error not in [0, 2]:
                status_text = f"Error (code {detected_error})"
                has_error = True
            else:
                status_text = status_map.get(status_code, f"Status {status_code}")
                has_error = False

            printers.append({
                "name": name,
                "status": status_text,
                "status_code": status_code,
                "work_offline": work_offline,
                "detected_error_state": detected_error,
                "has_error": has_error,
                "port": port,
                "connection_type": connection_type,
                "driver": driver,
            })

    except Exception as e:
        print(f"    Error getting printers: {e}")
        print(traceback.format_exc())

    return printers


def save_json_report(report):
    report_name = f"Restart_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    if running_as_exe():
        primary = os.path.join(os.path.dirname(sys.executable), report_name)
    else:
        primary = os.path.join(os.path.dirname(os.path.abspath(__file__)), report_name)

    fallback = os.path.join(os.environ.get("TEMP", "."), report_name)
    last_error = None

    for path in (primary, fallback):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            return True, path, None
        except Exception as e:
            last_error = e
            print(f"    Cannot write JSON report to:\n    {path}")
            print(f"    Reason: {e}")

    return False, primary, last_error


def print_result_in_window(report):
    print()
    print("=" * 55)
    print(f"  RESULT: {report.get('overall_result', '')}")
    print(f"  Version : {report.get('app_version', APP_VERSION)}")
    print(f"  Time    : {report.get('datetime', '')}")
    print(f"  Computer: {report.get('computer', '')}")
    print(f"  User    : {report.get('user', '')}")
    print("=" * 55)

    spooler = report.get("print_spooler") or {}
    print()
    print("[Print Spooler]")
    print(f"    Before : {spooler.get('status_before', '')}")
    print(f"    After  : {spooler.get('status_after', '')}")
    print(f"    Result : {spooler.get('result', '')}")
    print(f"    Time   : {spooler.get('seconds_used', '')} sec")
    if spooler.get("error"):
        print(f"    Error  : {spooler.get('error')}")

    qz = report.get("qz_tray") or {}
    print()
    print("[QZ Tray]")
    print(f"    Path found      : {qz.get('path_found', '')}")
    print(f"    Path            : {qz.get('path', '')}")
    print(f"    Process running : {qz.get('process_running', '')}")
    print(f"    Result          : {qz.get('result', '')}")
    print(f"    Time            : {qz.get('seconds_used', '')} sec")

    printers = report.get("vita_printers") or []
    print()
    print("[Vita printers]")
    if not printers:
        print("    No Vita_* printers found.")
    else:
        for p in printers:
            mark = "  ← PROBLEM" if p.get("has_error") else ""
            print(f"    • {p.get('name', '')}")
            print(f"        Status : {p.get('status', '')}{mark}")
            print(f"        Port   : {p.get('port', '')} ({p.get('connection_type', '')})")
            print(f"        Driver : {p.get('driver', '')}")

    events = report.get("event_viewer") or []
    print()
    print("[Event Viewer / errors and warnings]")
    if not events:
        print("    No recent print/QZ errors or warnings found.")
    else:
        for ev in events[:8]:
            print(f"    • {ev.get('time', '')} [{ev.get('level', '')}] {ev.get('source', '')} ID {ev.get('event_id', '')}")
            print(f"      {ev.get('message', '')[:180]}")
    print()


def main():
    print("=" * 55)
    print("  Restart Print Spooler + QZ Tray + Vita Printer Check")
    print(f"  Version {APP_VERSION}")
    print("=" * 55)
    print()

    if not is_admin():
        print("[ERROR] Please run this program as Administrator!")
        print("Right-click the .exe → Run as administrator")
        input("\nPress Enter to exit...")
        sys.exit(1)

    check_for_updates()

    report = {
        "app_version": APP_VERSION,
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "computer": os.environ.get("COMPUTERNAME", "Unknown"),
        "user": os.environ.get("USERNAME", "Unknown"),
        "print_spooler": {},
        "qz_tray": {},
        "vita_printers": [],
        "event_viewer": [],
        "qz_log": {},
        "overall_result": "",
        "report_path": "",
        "report_error": "",
    }
    has_problem = False

    print("[1] Checking Print Spooler status...")
    spooler_info = restart_spooler_with_timer()
    report["print_spooler"] = spooler_info
    if spooler_info.get("result") != "OK":
        has_problem = True
    print(f"    Status after : {spooler_info.get('status_after')} → {spooler_info.get('result')}")
    print()

    qz_info = restart_qz_with_timer()
    report["qz_tray"] = qz_info
    if qz_info.get("result") != "OK":
        has_problem = True
    print(f"    Path found     : {qz_info.get('path_found')}")
    print(f"    Process running: {qz_info.get('process_running')} → {qz_info.get('result')}")
    print()

    print("[7] Checking Vita_* printers...")
    vita_printers = get_vita_printers()
    report["vita_printers"] = vita_printers

    if not vita_printers:
        print("    No Vita_* printers found.")
    else:
        for p in vita_printers:
            error_mark = "  ← PROBLEM" if p["has_error"] else ""
            print(f"    • {p['name']}")
            print(f"        Status     : {p['status']}{error_mark}")
            print(f"        Port       : {p['port']} ({p['connection_type']})")
            print(f"        Driver     : {p['driver']}")
            print()
            if p["has_error"]:
                has_problem = True

    print("[7b] Reading Event Viewer and QZ Tray logs...")
    report["event_viewer"] = get_print_event_logs()
    report["qz_log"] = get_qz_log_tail()
    print(f"    Event Viewer items : {len(report['event_viewer'])}")
    print(f"    QZ log found       : {report['qz_log'].get('found')}")
    print()

    report["overall_result"] = "SUCCESS" if not has_problem else "PROBLEMS_DETECTED"

    print("[8] Saving JSON report and showing result...")
    saved, report_path, report_error = save_json_report(report)
    if saved:
        report["report_path"] = report_path
        print(f"    JSON report saved to:\n    {report_path}")
    else:
        report["report_error"] = str(report_error)
        print(f"    JSON report failed: {report_error}")
        print("    The same information is shown in this window.")

    print_result_in_window(report)
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
