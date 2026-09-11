# ============================================================
# Restart Print Spooler + QZ Tray + Check Vita Printers
# Version 1.1.1
# ============================================================
# .exe  -> HTTPS GET GitHub latest release, download new exe if newer
# .py   -> git pull if this folder is a git repo
# Results are printed in the window AND saved as a local JSON file.
# The JSON file stays on this PC. It is not uploaded to GitHub.
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

APP_VERSION = "1.1.1"
GITHUB_OWNER = "albertchan1234"
GITHUB_REPO = "restart-print"
GITHUB_EXE_ASSET = "Restart_Print_QZ.exe"
UPDATE_TIMEOUT = 12


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_cmd(cmd):
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore",
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


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
    # HTTPS GET only. Downloads GitHub release info. Does not upload clinic data.
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

    ok, stdout, stderr = run_cmd(f'git -C "{script_dir}" pull origin main')
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
    success, stdout, _ = run_cmd(f"sc query {service_name}")
    if "RUNNING" in stdout:
        return "RUNNING"
    elif "STOPPED" in stdout:
        return "STOPPED"
    return "UNKNOWN"


def is_process_running(process_name):
    success, stdout, _ = run_cmd(f'tasklist /FI "IMAGENAME eq {process_name}"')
    return process_name.lower() in stdout.lower()


def is_qz_running():
    if is_process_running("qz-tray.exe") or is_process_running("qz-tray-console.exe"):
        return True

    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*qz-tray*' } | "
        "Select-Object -First 1 ProcessId"
    )
    success, stdout, _ = run_cmd(f'powershell -NoProfile -Command "{ps_cmd}"')
    if success and stdout.strip() and any(c.isdigit() for c in stdout):
        return True

    success, stdout, _ = run_cmd('netstat -ano | findstr ":8181 :8182 :8282 :8383 :8484"')
    if success and "LISTENING" in stdout.upper():
        return True

    return False


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
        print("    Printer check details:")
        print(traceback.format_exc())

    return printers


def save_json_report(report):
    # Local file only. Not uploaded to GitHub.
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

    qz = report.get("qz_tray") or {}
    print()
    print("[QZ Tray]")
    print(f"    Path found      : {qz.get('path_found', '')}")
    print(f"    Path            : {qz.get('path', '')}")
    print(f"    Process running : {qz.get('process_running', '')}")
    print(f"    Result          : {qz.get('result', '')}")

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
        "overall_result": "",
        "report_path": "",
        "report_error": "",
    }
    has_problem = False

    print("[1] Checking Print Spooler status...")
    status_before = get_service_status("Spooler")
    print(f"    Status before: {status_before}")

    print("[2] Stopping Print Spooler...")
    run_cmd("net stop spooler")
    time.sleep(2)

    print("[3] Clearing stuck print jobs...")
    run_cmd(r'del /Q /F /S "%systemroot%\System32\spool\PRINTERS\*.*"')

    print("[4] Starting Print Spooler...")
    run_cmd("net start spooler")
    time.sleep(2)

    status_after = get_service_status("Spooler")
    spooler_result = "OK" if status_after == "RUNNING" else "FAILED"
    if spooler_result != "OK":
        has_problem = True

    report["print_spooler"] = {
        "status_before": status_before,
        "status_after": status_after,
        "result": spooler_result,
    }
    print(f"    Status after : {status_after} → {spooler_result}")
    print()

    print("[5] Stopping QZ Tray...")
    run_cmd('wmic process where "Name like \'%java%\' and CommandLine like \'%qz-tray.jar%\'" call terminate')
    run_cmd("taskkill /F /IM qz-tray.exe /T")
    run_cmd("taskkill /F /IM qz-tray-console.exe /T")
    time.sleep(2)

    print("[6] Starting QZ Tray...")
    qz_path = r"C:\Program Files\QZ Tray\qz-tray.exe"
    path_found = os.path.exists(qz_path)
    process_running = False

    if path_found:
        try:
            subprocess.Popen([qz_path], shell=False)
            time.sleep(8)
            process_running = is_qz_running()
        except Exception as e:
            print(f"    Error starting QZ Tray: {e}")
            print(traceback.format_exc())
    else:
        print("    QZ Tray path not found!")

    qz_result = "OK" if (path_found and process_running) else "FAILED"
    if qz_result != "OK":
        has_problem = True

    report["qz_tray"] = {
        "path_found": path_found,
        "path": qz_path if path_found else None,
        "process_running": process_running,
        "result": qz_result,
    }
    print(f"    Path found     : {path_found}")
    print(f"    Process running: {process_running} → {qz_result}")
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