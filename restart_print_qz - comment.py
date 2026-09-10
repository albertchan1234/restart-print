# ============================================================
# Restart Print Spooler + QZ Tray + Check Vita Printers
# Author: Custom script for clinic printer maintenance
# ============================================================

import subprocess      # Used to run Windows commands (net, taskkill, powershell...)
import time            # Used to wait (sleep) between actions
import os              # Used to get computer name, file paths, etc.
import sys             # Used to check if running as .exe or normal script
import json            # Used to create the JSON report file
import ctypes          # Used to check if the program is running as Administrator
from datetime import datetime   # Used to get current date and time

# ------------------------------------------------------------
# Function: Check if the program is running as Administrator
# ------------------------------------------------------------
def is_admin():
    try:
        # This Windows API call returns True if we have admin rights
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# ------------------------------------------------------------
# Function: Run any Windows command and return the result
# ------------------------------------------------------------
def run_cmd(cmd):
    try:
        result = subprocess.run(
            cmd,
            shell=True,                 # Allow running system commands
            capture_output=True,        # Capture the output text
            text=True,                  # Return output as text (not bytes)
            timeout=30,                 # Stop if command takes more than 30 seconds
            encoding="utf-8",           # Handle Chinese / special characters
            errors="ignore"             # Ignore any encoding errors
        )
        # Return: success (True/False), output text, error text
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

# ------------------------------------------------------------
# Function: Check if Print Spooler service is running
# ------------------------------------------------------------
def get_service_status(service_name="Spooler"):
    success, stdout, _ = run_cmd(f'sc query {service_name}')
    if "RUNNING" in stdout:
        return "RUNNING"
    elif "STOPPED" in stdout:
        return "STOPPED"
    return "UNKNOWN"

# ------------------------------------------------------------
# Function: Check if a process is running by name
# ------------------------------------------------------------
def is_process_running(process_name):
    success, stdout, _ = run_cmd(f'tasklist /FI "IMAGENAME eq {process_name}"')
    return process_name.lower() in stdout.lower()

# ------------------------------------------------------------
# Function: Check if QZ Tray is currently running
# We use 3 different methods because QZ Tray can run in different ways
# ------------------------------------------------------------
def is_qz_running():
    # Method 1: Check normal process names
    if is_process_running("qz-tray.exe") or is_process_running("qz-tray-console.exe"):
        return True

    # Method 2: Look for any Java process that has "qz-tray" in its command line
    ps_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*qz-tray*' } | "
        "Select-Object -First 1 ProcessId"
    )
    success, stdout, _ = run_cmd(f'powershell -NoProfile -Command "{ps_cmd}"')
    if success and stdout.strip() and any(c.isdigit() for c in stdout):
        return True

    # Method 3: Check if QZ Tray ports are open (very reliable)
    success, stdout, _ = run_cmd('netstat -ano | findstr ":8181 :8182 :8282 :8383 :8484"')
    if success and "LISTENING" in stdout.upper():
        return True

    return False

# ------------------------------------------------------------
# Function: Get all printers that start with "Vita_"
# We use Win32_Printer because it correctly reports Offline status
# ------------------------------------------------------------
def get_vita_printers():
    printers = []

    # PowerShell command to get printer information
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
            errors="ignore"
        )

        stdout = result.stdout.strip()
        if not stdout:
            return printers

        data = json.loads(stdout)

        # If only one printer is found, PowerShell returns a dictionary instead of a list
        if isinstance(data, dict):
            data = [data]

        # Map status numbers to readable text
        status_map = {
            1: "Other",
            2: "Unknown",
            3: "Idle",
            4: "Printing",
            5: "Warmup",
            6: "Stopped Printing",
            7: "Offline / Error"
        }

        for p in data:
            name = p.get("Name", "")
            status_code = p.get("PrinterStatus", 1)
            port = p.get("PortName", "")
            driver = p.get("DriverName", "")
            work_offline = p.get("WorkOffline", False)
            detected_error = p.get("DetectedErrorState", 0)

            # Decide if the printer is USB or Network
            port_upper = str(port).upper()
            if port_upper.startswith("USB") or "DOT4" in port_upper or "LPT" in port_upper:
                connection_type = "USB"
            elif port_upper.startswith("IP_") or "WSD" in port_upper or "." in port_upper:
                connection_type = "Network"
            else:
                connection_type = "Other"

            # Decide the final status (Offline is the most important check)
            if work_offline:
                status_text = "Offline"
                has_error = True
            elif detected_error not in [0, 2]:
                status_text = f"Error (code {detected_error})"
                has_error = True
            else:
                status_text = status_map.get(status_code, f"Status {status_code}")
                has_error = False

            # Save all information into a dictionary
            printers.append({
                "name": name,
                "status": status_text,
                "status_code": status_code,
                "work_offline": work_offline,
                "detected_error_state": detected_error,
                "has_error": has_error,
                "port": port,
                "connection_type": connection_type,
                "driver": driver
            })

    except Exception as e:
        print(f"    Error getting printers: {e}")

    return printers

# ------------------------------------------------------------
# Main program starts here
# ------------------------------------------------------------
def main():
    print("=" * 55)
    print("  Restart Print Spooler + QZ Tray + Vita Printer Check")
    print("=" * 55)
    print()

    # Check Administrator rights
    if not is_admin():
        print("[ERROR] Please run this program as Administrator!")
        print("Right-click the .exe → Run as administrator")
        input("\nPress Enter to exit...")
        sys.exit(1)

    # Prepare the JSON report structure
    report = {
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "computer": os.environ.get("COMPUTERNAME", "Unknown"),
        "user": os.environ.get("USERNAME", "Unknown"),
        "print_spooler": {},
        "qz_tray": {},
        "vita_printers": [],
        "overall_result": ""
    }

    has_problem = False   # Will become True if any problem is found

    # ========== 1. Restart Print Spooler ==========
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
        "result": spooler_result
    }
    print(f"    Status after : {status_after} → {spooler_result}")
    print()

    # ========== 2. Restart QZ Tray ==========
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
            time.sleep(8)          # Wait for QZ Tray to fully start
            process_running = is_qz_running()
        except Exception as e:
            print(f"    Error starting QZ Tray: {e}")
    else:
        print("    QZ Tray path not found!")

    qz_result = "OK" if (path_found and process_running) else "FAILED"
    if qz_result != "OK":
        has_problem = True

    report["qz_tray"] = {
        "path_found": path_found,
        "path": qz_path if path_found else None,
        "process_running": process_running,
        "result": qz_result
    }

    print(f"    Path found     : {path_found}")
    print(f"    Process running: {process_running} → {qz_result}")
    print()

    # ========== 3. Check Vita_* Printers ==========
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

    # ========== Final Result ==========
    overall = "SUCCESS" if not has_problem else "PROBLEMS_DETECTED"
    report["overall_result"] = overall

    # Create the JSON report file name with date and time
    report_name = f"Restart_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # Decide where to save the report
    if getattr(sys, 'frozen', False):
        # Running as .exe → save next to the .exe file
        report_path = os.path.join(os.path.dirname(sys.executable), report_name)
    else:
        # Running as .py → save next to the script
        report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), report_name)

    # Write the JSON file
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("=" * 55)
    print(f"  RESULT: {overall}")
    print("=" * 55)
    print(f"\nJSON Report saved to:\n{report_path}")
    print()
    input("Press Enter to exit...")

# This line makes the program start when you run the file
if __name__ == "__main__":
    main()