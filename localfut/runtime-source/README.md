# FIFA 21 Local FUT — Alpha

This build adds the missing **FIFA21.exe-only localhost trust bridge** to the
local FUT runtime, based on the proven FIFA 20 Local FUT trust-bridge approach.

## What the bridge does

FIFA 21 ships a native DirtySDK/GOS trust table. It contains **two** CA entries
(identified directly inside FIFA21.exe):

1. `GOS 2015 Certificate Authority` (256-byte RSA modulus, first bytes
   `cc6d54b6f4e484e7`, sha256 `1e7a2384...392aff`) — the same trust root that
   FIFA 20 uses for `spring18.gosredirector.ea.com`.
2. `Gameplay Services 2019 Certificate Authority` (first bytes `04133f219386f765`,
   sha256 `03826e36...c5264d`).

The bridge:

1. Generates a new local RSA CA whose X.509 identity matches each GOS CA entry.
2. Signs a local `spring18.gosredirector.ea.com` certificate with it.
3. While FIFA21.exe is running, searches its memory for **exact 256-byte copies**
   of the already-identified original GOS RSA public moduli.
4. Replaces only those exact copies with the local CA public modulus.
5. Changes no FIFA file on disk and changes no Windows certificate policy.

When FIFA21.exe exits, the change is gone.

This is deliberately narrow: FIFA21.exe + the local GOS redirector only.

## FIRST-TIME SETUP

Extract the project to a writable folder. Run:

    INSTALL_PREREQUISITES.cmd

Then:

    GENERATE_LOCAL_CERT.cmd

You only need to regenerate the certificate if you want a new local CA/key pair.

## TEST ORDER

### 1. Make sure the hosts redirect is OFF

Run PowerShell as Administrator:

    Set-ExecutionPolicy -Scope Process Bypass
    .\REMOVE_HOSTS_REDIRECT.ps1

### 2. Launch FIFA 21 normally

Use the EA App as usual. The game binary is `E:\fifa\FIFA 21\FIFA21.exe` on this
machine.

Wait until FIFA 21 is fully at its main menu.

Do NOT click Ultimate Team yet.

The unified launcher now handles the LSX boundary automatically. If EA App is
running, it detects EA App's listener on `127.0.0.1:3216` and disables the
replacement LSX bind. If you are testing the local replacement helper instead,
close EA App/Origin first and run `Launch-LocalFUT.cmd -LSXMode replacement`.
Do not let both modes compete for port `3216`.

In EA App mode the unified launcher also starts the game through EA App's
`origin2://game/launch/` handler, using the configured FIFA 21 offer ID. This
preserves the EA App launch context that a direct `FIFA21.exe` start omits.

The replacement LSX session keeps its established TCP connection open while
FIFA is idle. The handshake timeout remains finite; the post-handshake receive
loop does not close the session merely because no LSX request arrived.

### 3. Patch the running FIFA21.exe trust entries

Right-click `PATCH_RUNNING_FIFA21_TRUST.cmd` and choose:

    Run as administrator

By default only the **GOS 2015** entry is patched (the proven FIFA 20 needle).
To also patch the GS 2019 entry, run:

    py PATCH_RUNNING_FIFA21_TRUST.py --all

Expected result:

    Exact original copies found: 1 or more
    [PATCHED] ...
    [SUCCESS] Patched ...

Leave FIFA 21 running.

### 4. Redirect the FIFA 21 GOS redirector to localhost

In Administrator PowerShell:

    Set-ExecutionPolicy -Scope Process Bypass
    .\INSTALL_HOSTS_REDIRECT.ps1

### 5. Start the local runtime

Run:

    START_LOCAL_FUT21.cmd

You should have the FUT/Blaze server and TLS redirector windows open.

The TLS redirector should say:

    FIFA 21 TLS redirector listening on 0.0.0.0:42230
    SNI target: spring18.gosredirector.ea.com

### 6. Click Ultimate Team ONCE

Watch the TLS redirector console.

The important milestone (proven for FIFA 20) is that, instead of dying
immediately after the certificate is sent, FIFA 21 should complete TLS and the
redirector should print an HTTP request:

    POST /redirector/getServerInstance

If it prints a request, the certificate/trust barrier is passed and the next
work is Blaze/redirector message compatibility for FIFA 21.

If it still rejects the connection before any redirector request is printed,
send a screenshot of:

- PATCH_RUNNING_FIFA21_TRUST.cmd result
- TLS redirector window
- FIFA error

## CLEANUP

After every test, remove the hosts redirect:

    .\REMOVE_HOSTS_REDIRECT.ps1

The memory patch disappears automatically when FIFA21.exe exits.

If you want to restore the original public keys without closing FIFA, run:

    RESTORE_RUNNING_FIFA21_TRUST.cmd

## Safety boundaries

- Does not modify FIFA21.exe on disk.
- Does not install a Windows root CA.
- Does not disable TLS validation globally.
- Does not contain EA private keys or EA account credentials.
- Only replaces exact, previously identified public-key byte sequences inside
  the running FIFA21.exe process.
