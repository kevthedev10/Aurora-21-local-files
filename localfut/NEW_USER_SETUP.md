# Project 21 LocalFUT — New User Setup

## 1. Install the requirements

1. Install the **EA App**, sign in to the account that owns FIFA 21, and install FIFA 21.
2. Install **Python 3 (64-bit)** from [python.org](https://www.python.org/downloads/windows/). Enable **Add Python to PATH** during installation.
3. Install the **.NET 8 Desktop Runtime (x64)** from [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/8.0). Choose **Desktop Runtime**, not the ASP.NET runtime.
4. Open the extracted `localfut` folder, then double-click:
   `runtime-source\INSTALL_PREREQUISITES.cmd`

This installs the required Python `cryptography` package. To check Python manually, open PowerShell and run:

```powershell
py -3 --version
py -3 -m pip install "cryptography>=42"
```

## 2. Prepare the build

- Extract or copy the **entire build** into one folder. Do not combine a new launcher with scripts from an older build.
- Keep the included `runtime-source`, DLL, JSON, PowerShell, and certificate files beside the launcher.
- If `runtime-source\certs` is missing, run `runtime-source\GENERATE_LOCAL_CERT.cmd` once.
- Close FIFA 21 before the first launch.

## 3. Configure and launch FIFA 21

1. Start the EA App and make sure you are signed in.
2. In the `localfut` folder, open **`Project21-LocalFUT-Launcher.exe`**.
3. Open **Settings** and select the real installed **`FIFA21.exe`**. Do not select the FIFA configuration launcher.
4. Leave the runtime source on this build's bundled `runtime-source` folder and save the settings.
5. Return to **Overview** and click **LAUNCH LOCAL FUT**.
6. Approve the Windows administrator/UAC prompt. The launcher needs it for the local hosts entry and the running-game trust bridge.
7. Wait for the launcher to report that the local services are ready. FIFA 21 should then launch through the EA App automatically.

Always start the game from **Project21-LocalFUT-Launcher.exe** when using LocalFUT. Do not launch `FIFA21.exe` directly first.

## Quick troubleshooting

- **Python not found:** reinstall Python with **Add Python to PATH**, then reopen the launcher.
- **Missing cryptography:** run `runtime-source\INSTALL_PREREQUISITES.cmd` again.
- **Connection not running:** close FIFA, use **STOP ALL** or **RESTART LOCAL FUT**, then launch again as administrator.
- **EA App does not open FIFA:** open the EA App first, confirm the account owns FIFA 21, and verify the game installation.
- **Wrong game path:** select the installed file named exactly `FIFA21.exe` in launcher Settings.
- Logs are stored in the build's `runtime` folder. The local club database is kept separately under `%LOCALAPPDATA%\FIFA21LocalFUT\data`.

## Current known bugs

- The stadium is invisible in Solo Draft matches and away Squad Battles matches.
- Icon players can display random/incorrect player names in Solo Draft.
