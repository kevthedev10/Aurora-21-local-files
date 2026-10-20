using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

// Local FUT compatibility only. EA App still performs launch/licence operations.
// No file in the game installation is changed and no debugger is used.
internal static class LocalAuthBridge
{
    private const long AuthRva = 0x10290A0;
    private const long DestroyRva = 0x1029350;
    private const int PageSize = 4096;
    private const int AuthOffset = 0x100;
    private const int DestroyOffset = 0x500;
    private const int AuthOriginalOffset = 0x700;
    private const int DestroyOriginalOffset = 0x780;
    private const int CodeStringOffset = 0x100;
    private const int CounterOffset = 0x80;
    private const uint ReadWrite = 0x04;
    private const uint ExecuteRead = 0x20;
    private const uint ExecuteReadWrite = 0x40;
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("LOCALFUT21-AUTH-BRIDGE-V1\0");
    // Full instruction boundaries, verified against the EA build's unpacked code.
    private static readonly byte[] AuthPrologue = Hex("48895C240848896C24104889742418");
    private static readonly byte[] DestroyPrologue = Hex("40534883EC30488B0563CDF807488BD9");
    private static readonly string[] ClientIds = {
        "localfut21-blaze-server", "localfut21-blaze-sdk", "localfut21-ssf"
    };
    private const string LocalCode = "LOCAL-FUT21-AUTH-CODE";

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] bytes, UIntPtr size, out UIntPtr read);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool WriteProcessMemory(IntPtr process, IntPtr address, byte[] bytes, UIntPtr size, out UIntPtr written);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr VirtualAllocEx(IntPtr process, IntPtr address, UIntPtr size, uint type, uint protection);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool VirtualFreeEx(IntPtr process, IntPtr address, UIntPtr size, uint type);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool VirtualProtectEx(IntPtr process, IntPtr address, UIntPtr size, uint protection, out uint oldProtection);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool FlushInstructionCache(IntPtr process, IntPtr address, UIntPtr size);
    [DllImport("ntdll.dll")]
    private static extern int NtSuspendProcess(IntPtr process);
    [DllImport("ntdll.dll")]
    private static extern int NtResumeProcess(IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenThread(uint access, bool inherit, uint threadId);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetThreadContext(IntPtr thread, IntPtr context);

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate int AuthCall(ulong user, IntPtr clientId, IntPtr code, IntPtr length);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate int DestroyCall(IntPtr handle);

    private sealed class Assembler
    {
        private readonly List<byte> bytes = new List<byte>();
        private readonly Dictionary<string, int> labels = new Dictionary<string, int>();
        private readonly List<KeyValuePair<int, string>> fixups = new List<KeyValuePair<int, string>>();
        internal void Emit(params byte[] value) { bytes.AddRange(value); }
        internal void U64(long value) { Emit(BitConverter.GetBytes(value)); }
        internal void Label(string name) { labels.Add(name, bytes.Count); }
        internal void Branch(string name, params byte[] opcode)
        {
            Emit(opcode);
            fixups.Add(new KeyValuePair<int, string>(bytes.Count, name));
            Emit(0, 0, 0, 0);
        }
        internal void AbsoluteJump(long address) { Emit(0xFF, 0x25, 0, 0, 0, 0); U64(address); }
        internal byte[] Finish()
        {
            byte[] result = bytes.ToArray();
            foreach (var fixup in fixups)
                Buffer.BlockCopy(BitConverter.GetBytes(checked(labels[fixup.Value] - fixup.Key - 4)), 0, result, fixup.Key, 4);
            return result;
        }
    }

    private static byte[] Hex(string text)
    {
        return Enumerable.Range(0, text.Length / 2).Select(i => Convert.ToByte(text.Substring(i * 2, 2), 16)).ToArray();
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static byte[] Read(IntPtr process, long address, int size)
    {
        byte[] bytes = new byte[size];
        UIntPtr count;
        if (!ReadProcessMemory(process, new IntPtr(address), bytes, (UIntPtr)size, out count) || count.ToUInt64() != (ulong)size)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot read the selected FIFA process.");
        return bytes;
    }

    private static void Write(IntPtr process, long address, byte[] bytes)
    {
        UIntPtr count;
        if (!WriteProcessMemory(process, new IntPtr(address), bytes, (UIntPtr)bytes.Length, out count) || count.ToUInt64() != (ulong)bytes.Length)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot write the selected process allocation.");
    }

    private static long Allocate(IntPtr process)
    {
        IntPtr address = VirtualAllocEx(process, IntPtr.Zero, (UIntPtr)PageSize, 0x3000, ReadWrite);
        if (address == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Local bridge allocation failed.");
        return address.ToInt64();
    }

    private static void ProtectCode(IntPtr process, long address)
    {
        uint previous;
        if (!VirtualProtectEx(process, new IntPtr(address), (UIntPtr)PageSize, ExecuteRead, out previous) ||
            !FlushInstructionCache(process, new IntPtr(address), (UIntPtr)PageSize))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot finalize local bridge code.");
    }

    private static void ReplaceCode(IntPtr process, long address, byte[] expected, byte[] replacement, out uint originalProtection)
    {
        originalProtection = 0;
        Require(expected.Length == replacement.Length, "Patch length mismatch.");
        Require(Read(process, address, expected.Length).SequenceEqual(expected), "FIFA code changed during installation; refusing the patch.");
        uint previous;
        if (!VirtualProtectEx(process, new IntPtr(address), (UIntPtr)expected.Length, ExecuteReadWrite, out previous))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot protect the verified FIFA code range.");
        originalProtection = previous;
        try
        {
            Write(process, address, replacement);
            Require(Read(process, address, replacement.Length).SequenceEqual(replacement), "FIFA patch read-back failed.");
            if (!FlushInstructionCache(process, new IntPtr(address), (UIntPtr)replacement.Length))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "FIFA instruction-cache refresh failed.");
        }
        finally
        {
            uint ignored;
            if (!VirtualProtectEx(process, new IntPtr(address), (UIntPtr)expected.Length, previous, out ignored))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot restore FIFA code protection.");
        }
    }

    private static byte[] JumpPatch(long target, int length)
    {
        var code = new Assembler();
        code.AbsoluteJump(target);
        byte[] jump = code.Finish();
        Require(length >= jump.Length, "Insufficient verified patch space.");
        return jump.Concat(Enumerable.Repeat((byte)0x90, length - jump.Length)).ToArray();
    }

    private static void CheckSuspendedInstructionPointers(Process game, long imageBase)
    {
        // AMD64 CONTEXT must be 16-byte aligned. Read control registers only.
        IntPtr allocation = Marshal.AllocHGlobal(1232 + 15);
        IntPtr context = new IntPtr((allocation.ToInt64() + 15) & ~15L);
        try
        {
            game.Refresh(); // Discard the pre-unpack ProcessInfo/thread snapshot.
            foreach (ProcessThread item in game.Threads)
            {
                IntPtr thread = OpenThread(0x48, false, (uint)item.Id);
                if (thread == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot inspect a suspended FIFA thread.");
                try
                {
                    Marshal.WriteInt32(context, 48, 0x100001);
                    if (!GetThreadContext(thread, context))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot verify a suspended FIFA instruction pointer.");
                    long ip = Marshal.ReadInt64(context, 248);
                    Require(!(ip >= imageBase + AuthRva && ip < imageBase + AuthRva + AuthPrologue.Length) &&
                        !(ip >= imageBase + DestroyRva && ip < imageBase + DestroyRva + DestroyPrologue.Length),
                        "A FIFA thread is inside an auth entry point. No patch applied; retry while at the title screen.");
                }
                finally { CloseHandle(thread); }
            }
        }
        finally { Marshal.FreeHGlobal(allocation); }
    }

    private static bool RestoreAttemptedPatch(IntPtr process, long address, byte[] original, byte[] patch, uint originalProtection)
    {
        try
        {
            byte[] current = Read(process, address, original.Length);
            // A short write may contain a mixture, not the entire intended jump.
            for (int i = 0; i < current.Length; i++)
                Require(current[i] == original[i] || current[i] == patch[i], "Unexpected external change during rollback.");
            uint ignored;
            try { ReplaceCode(process, address, current, original, out ignored); }
            finally
            {
                // Do not mistake the RWX state left by a failed protection restore
                // for the original protection of this executable image page.
                if (originalProtection != 0 && !VirtualProtectEx(process, new IntPtr(address),
                    (UIntPtr)original.Length, originalProtection, out ignored))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot restore original FIFA page protection during rollback.");
            }
            return true;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("[ERROR] Auth bridge rollback failed: " + error.Message);
            return false;
        }
    }

    private static long JumpTarget(byte[] bytes)
    {
        return bytes.Length >= 14 && bytes.Take(6).SequenceEqual(new byte[] { 0xFF, 0x25, 0, 0, 0, 0 })
            ? BitConverter.ToInt64(bytes, 6) : 0;
    }

    private static byte[] BuildAuth(long data, long fallback)
    {
        var code = new Assembler();
        code.Emit(0x48, 0x85, 0xD2); // test rdx,rdx (clientId)
        code.Branch("original", 0x0F, 0x84);
        for (int i = 0; i < ClientIds.Length; i++)
        {
            code.Label("id" + i);
            code.Emit(0x49, 0xBA); code.U64(data + 0x200 + i * 0x80); // r10 = expected ID
            code.Emit(0x49, 0x89, 0xD3); // r11 = input; compare one byte at a time, including NUL
            code.Label("compare" + i);
            code.Emit(0x41, 0x8A, 0x02, 0x41, 0x3A, 0x03);
            code.Branch(i + 1 == ClientIds.Length ? "original" : "id" + (i + 1), 0x0F, 0x85);
            code.Emit(0x84, 0xC0);
            code.Branch("match" + i, 0x0F, 0x84);
            code.Emit(0x49, 0xFF, 0xC2, 0x49, 0xFF, 0xC3);
            code.Branch("compare" + i, 0xE9);
        }
        for (int i = 0; i < ClientIds.Length; i++)
        {
            code.Label("match" + i);
            code.Emit(0x49, 0xBB); code.U64(data + CounterOffset + i * 8);
            code.Branch("local", 0xE9);
        }
        code.Label("local");
        code.Emit(0x4D, 0x85, 0xC0); // output char**
        code.Branch("invalid", 0x0F, 0x84);
        code.Emit(0x4D, 0x85, 0xC9); // output length*
        code.Branch("invalid", 0x0F, 0x84);
        code.Emit(0xF0, 0x49, 0xFF, 0x03); // lock inc qword [r11]
        code.Emit(0x48, 0xB8); code.U64(data + CodeStringOffset);
        code.Emit(0x49, 0x89, 0x00); // *code = bridge-owned, process-lifetime ASCII
        code.Emit(0x48, 0xB8); code.U64(Encoding.ASCII.GetByteCount(LocalCode));
        code.Emit(0x49, 0x89, 0x01, 0x31, 0xC0, 0xC3);
        code.Label("invalid");
        code.Emit(0xB8, 1, 0, 0, 0, 0xC3); // non-success, without forwarding local IDs to EA
        code.Label("original");
        code.AbsoluteJump(fallback);
        return code.Finish();
    }

    private static byte[] BuildDestroy(long data, long fallback)
    {
        var code = new Assembler();
        code.Emit(0x48, 0xB8); code.U64(data + CodeStringOffset);
        code.Emit(0x48, 0x39, 0xC1); // only our exact synthetic handle is intercepted
        code.Branch("original", 0x0F, 0x85);
        code.Emit(0x49, 0xBA); code.U64(data + CounterOffset + ClientIds.Length * 8);
        code.Emit(0xF0, 0x49, 0xFF, 0x02, 0x31, 0xC0, 0xC3);
        code.Label("original");
        code.AbsoluteJump(fallback);
        return code.Finish();
    }

    private static byte[] DestroyTrampoline(long imageBase)
    {
        var code = new Assembler();
        code.Emit(DestroyPrologue.Take(6).ToArray()); // push rbx; sub rsp,30h
        long sourceGlobal = checked(imageBase + DestroyRva + 13 + BitConverter.ToInt32(DestroyPrologue, 9));
        code.Emit(0x48, 0xA1); code.U64(sourceGlobal); // relocated mov rax,[absolute global]
        code.Emit(DestroyPrologue.Skip(13).ToArray()); // mov rbx,rcx
        code.AbsoluteJump(imageBase + DestroyRva + DestroyPrologue.Length);
        return code.Finish();
    }

    private static void Put(byte[] page, int offset, byte[] bytes)
    {
        Require(offset >= 0 && offset + bytes.Length <= page.Length, "Bridge allocation overflow.");
        Buffer.BlockCopy(bytes, 0, page, offset, bytes.Length);
    }

    private static byte[] DataPage()
    {
        byte[] page = new byte[PageSize];
        Put(page, CodeStringOffset, Encoding.ASCII.GetBytes(LocalCode + "\0"));
        for (int i = 0; i < ClientIds.Length; i++)
            Put(page, 0x200 + i * 0x80, Encoding.ASCII.GetBytes(ClientIds[i] + "\0"));
        return page;
    }

    private static byte[] CodePage(int pid, long imageBase, long codeAddress, long dataAddress)
    {
        byte[] page = new byte[PageSize];
        Put(page, 0, Magic);
        Put(page, 32, BitConverter.GetBytes(pid));
        Put(page, 40, BitConverter.GetBytes(imageBase));
        Put(page, 48, BitConverter.GetBytes(dataAddress));
        byte[] auth = BuildAuth(dataAddress, codeAddress + AuthOriginalOffset);
        Require(AuthOffset + auth.Length <= DestroyOffset, "Auth code overlaps the handle adapter.");
        Put(page, AuthOffset, auth);
        Put(page, DestroyOffset, BuildDestroy(dataAddress, codeAddress + DestroyOriginalOffset));
        var original = new Assembler();
        original.Emit(AuthPrologue);
        original.AbsoluteJump(imageBase + AuthRva + AuthPrologue.Length);
        Put(page, AuthOriginalOffset, original.Finish());
        Put(page, DestroyOriginalOffset, DestroyTrampoline(imageBase));
        return page;
    }

    private static void CheckServer()
    {
        var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8090/localfut/auth/bootstrap");
        request.Proxy = null;
        request.AllowAutoRedirect = false;
        request.Timeout = 3000;
        request.ReadWriteTimeout = 3000;
        using (var response = (HttpWebResponse)request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream()))
        {
            Require(response.StatusCode == HttpStatusCode.OK, "Updated local auth service is not ready.");
            char[] buffer = new char[8193];
            int length = reader.ReadBlock(buffer, 0, buffer.Length);
            Require(length <= 8192, "Local auth profile exceeds its size limit.");
            var profile = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(new string(buffer, 0, length));
            Require(profile != null && profile.ContainsKey("version") && Convert.ToInt32(profile["version"]) == 1,
                "Server lacks local auth profile v1. Restart the updated Project 21 server.");
            Require(profile.ContainsKey("authCode") && (string)profile["authCode"] == LocalCode, "Local auth profile/code mismatch.");
            var ids = profile.ContainsKey("clientIds") ? profile["clientIds"] as System.Collections.IEnumerable : null;
            Require(ids != null && ids.Cast<object>().Select(Convert.ToString).SequenceEqual(ClientIds), "Local auth profile/client-ID mismatch.");
            var config = profile.ContainsKey("config") ? profile["config"] as Dictionary<string, object> : null;
            Require(config != null, "Missing native auth configuration.");
            CheckValue(config, "blazeServerClientId", ClientIds[0]);
            CheckValue(config, "blazeSdkClientId", ClientIds[1]);
            CheckValue(config, "blazeSdkClientSecret", "");
            CheckValue(config, "nucleusConnect", "http://127.0.0.1:8090/");
            CheckValue(config, "identityRedirectUri", "http://127.0.0.1:8090/localfut/callback");
        }
    }

    private static void CheckValue(Dictionary<string, object> config, string key, string expected)
    {
        Require(config.ContainsKey(key) && config[key] is string && (string)config[key] == expected,
            "Local auth configuration mismatch: " + key);
    }

    private static bool Existing(IntPtr process, int pid, long imageBase)
    {
        long entry = JumpTarget(Read(process, imageBase + AuthRva, AuthPrologue.Length));
        if (entry == 0) return false;
        long codeAddress = entry - AuthOffset;
        byte[] header = Read(process, codeAddress, 56);
        Require(header.Take(Magic.Length).SequenceEqual(Magic) && BitConverter.ToInt32(header, 32) == pid &&
            BitConverter.ToInt64(header, 40) == imageBase, "Unknown auth hook already installed; restart FIFA instead of layering patches.");
        long dataAddress = BitConverter.ToInt64(header, 48);
        Require(JumpTarget(Read(process, imageBase + DestroyRva, DestroyPrologue.Length)) == codeAddress + DestroyOffset,
            "Incomplete auth bridge installation; restart FIFA.");
        Require(Read(process, codeAddress, PageSize).SequenceEqual(CodePage(pid, imageBase, codeAddress, dataAddress)),
            "Existing auth bridge code differs from this build; restart FIFA.");
        byte[] actualData = Read(process, dataAddress, PageSize);
        Array.Clear(actualData, CounterOffset, 32); // Only the call counters are mutable.
        Require(actualData.SequenceEqual(DataPage()),
            "Existing auth bridge data differs from this build; restart FIFA.");
        Console.WriteLine("[ALREADY] Local authentication bridge is installed for PID " + pid + ".");
        PrintCounters(process, dataAddress);
        return true;
    }

    private static void PrintCounters(IntPtr process, long dataAddress)
    {
        byte[] counters = Read(process, dataAddress + CounterOffset, 32);
        Console.WriteLine("[CALLS] server={0} sdk={1} ssf={2} local-handle-release={3}",
            BitConverter.ToInt64(counters, 0), BitConverter.ToInt64(counters, 8),
            BitConverter.ToInt64(counters, 16), BitConverter.ToInt64(counters, 24));
    }

    private static void Install(int pid, string gamePath, bool statusOnly)
    {
        Require(Environment.Is64BitProcess, "Run the 64-bit local authentication bridge.");
        string expectedPath = Path.GetFullPath(gamePath);
        var file = new FileInfo(expectedPath);
        Require(file.Exists && file.Length == 571958064 &&
            FileVersionInfo.GetVersionInfo(expectedPath).ProductVersion == "21.0.4750997.0",
            "Unsupported FIFA executable. This bridge supports build 21.0.4750997.0 only.");
        using (var game = Process.GetProcessById(pid))
        {
            Require(game.ProcessName.Equals("FIFA21", StringComparison.OrdinalIgnoreCase), "Selected PID is not FIFA21.exe.");
            Require(string.Equals(Path.GetFullPath(game.MainModule.FileName), expectedPath, StringComparison.OrdinalIgnoreCase),
                "Selected FIFA process does not match the requested executable path.");
            long imageBase = game.MainModule.BaseAddress.ToInt64();
            IntPtr process = OpenProcess(statusOnly ? 0x410u : 0xC38u, false, pid);
            if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Administrator access is required for the selected FIFA process.");
            try
            {
                if (Existing(process, pid, imageBase)) return;
                Require(!statusOnly, "No local authentication bridge is installed in this process.");
                CheckServer();
                Console.WriteLine("[WAIT] Waiting for the supported game's live auth functions (up to 45 seconds).");
                var deadline = Stopwatch.StartNew();
                while (!Read(process, imageBase + AuthRva, AuthPrologue.Length).SequenceEqual(AuthPrologue) ||
                    !Read(process, imageBase + DestroyRva, DestroyPrologue.Length).SequenceEqual(DestroyPrologue))
                {
                    Require(deadline.ElapsedMilliseconds < 45000, "Live auth instructions do not match the supported build; no patch applied.");
                    Thread.Sleep(250);
                }
                long code = 0, data = 0;
                bool suspended = false, installed = false, touchedAuth = false, touchedDestroy = false, safeToResume = true;
                byte[] authPatch = null, destroyPatch = null;
                uint authProtection = 0, destroyProtection = 0;
                try
                {
                    data = Allocate(process);
                    code = Allocate(process);
                    Write(process, data, DataPage());
                    Write(process, code, CodePage(pid, imageBase, code, data));
                    ProtectCode(process, code);
                    authPatch = JumpPatch(code + AuthOffset, AuthPrologue.Length);
                    destroyPatch = JumpPatch(code + DestroyOffset, DestroyPrologue.Length);
                    Require(NtSuspendProcess(process) >= 0, "Cannot briefly suspend FIFA for an atomic bridge installation.");
                    suspended = true;
                    CheckSuspendedInstructionPointers(game, imageBase);
                    Require(Read(process, imageBase + AuthRva, AuthPrologue.Length).SequenceEqual(AuthPrologue) &&
                        Read(process, imageBase + DestroyRva, DestroyPrologue.Length).SequenceEqual(DestroyPrologue),
                        "FIFA auth code changed before installation; no patch applied.");
                    // Install handle ownership first; genuine EA handles still take their original path.
                    safeToResume = false;
                    touchedDestroy = true;
                    ReplaceCode(process, imageBase + DestroyRva, DestroyPrologue, destroyPatch, out destroyProtection);
                    touchedAuth = true;
                    ReplaceCode(process, imageBase + AuthRva, AuthPrologue, authPatch, out authProtection);
                    installed = true;
                    safeToResume = true;
                }
                finally
                {
                    try
                    {
                        if (!installed && suspended)
                        {
                            bool authRestored = !touchedAuth || RestoreAttemptedPatch(process, imageBase + AuthRva, AuthPrologue, authPatch, authProtection);
                            bool destroyRestored = !touchedDestroy || RestoreAttemptedPatch(process, imageBase + DestroyRva, DestroyPrologue, destroyPatch, destroyProtection);
                            safeToResume = authRestored && destroyRestored;
                        }
                    }
                    finally
                    {
                        if (suspended && !safeToResume)
                            throw new InvalidOperationException("FIFA remains suspended because rollback could not be verified. Close FIFA and relaunch; do not resume this process.");
                        if (suspended && NtResumeProcess(process) < 0)
                            throw new InvalidOperationException("FIFA could not be resumed after bridge installation.");
                        // Retain allocations after any attempted patch if rollback could not be proved.
                        if (!installed && !touchedAuth && !touchedDestroy)
                        {
                            if (code != 0) VirtualFreeEx(process, new IntPtr(code), UIntPtr.Zero, 0x8000);
                            if (data != 0) VirtualFreeEx(process, new IntPtr(data), UIntPtr.Zero, 0x8000);
                        }
                    }
                }
                Require(Existing(process, pid, imageBase), "Bridge installation verification failed.");
                Console.WriteLine("[READY] Local authentication bridge installed. EA launch/licence calls remain unchanged.");
                Console.WriteLine("[SCOPE] Current FIFA process only; no game files or account settings changed.");
            }
            finally { CloseHandle(process); }
        }
    }

    private static void SelfTest()
    {
        Require(Environment.Is64BitProcess, "Self-tests require an x64 build.");
        Require(AuthPrologue.Length == 15 && DestroyPrologue.Length == 16, "Invalid instruction boundaries.");
        Require(BitConverter.ToInt64(DestroyTrampoline(0x140000000), 8) == 0x148FB60C0,
            "DestroyHandle RIP-relative relocation is incorrect.");
        IntPtr process = GetCurrentProcess();
        long code = Allocate(process), data = Allocate(process);
        IntPtr output = Marshal.AllocHGlobal(16);
        try
        {
            byte[] page = new byte[PageSize];
            Put(page, AuthOffset, BuildAuth(data, code + AuthOriginalOffset));
            Put(page, DestroyOffset, BuildDestroy(data, code + DestroyOriginalOffset));
            Put(page, AuthOriginalOffset, new byte[] { 0xB8, 0x34, 0x12, 0, 0, 0xC3 });
            Put(page, DestroyOriginalOffset, new byte[] { 0xB8, 0x78, 0x56, 0, 0, 0xC3 });
            Write(process, data, DataPage()); Write(process, code, page); ProtectCode(process, code);
            var auth = (AuthCall)Marshal.GetDelegateForFunctionPointer(new IntPtr(code + AuthOffset), typeof(AuthCall));
            var destroy = (DestroyCall)Marshal.GetDelegateForFunctionPointer(new IntPtr(code + DestroyOffset), typeof(DestroyCall));
            foreach (string id in ClientIds.Concat(new[] { "FC26_PC_CLIENT", "localfut21-other", "", "localfut21-blaze-sdk-extra" }))
            {
                IntPtr name = Marshal.StringToHGlobalAnsi(id);
                try
                {
                    Marshal.WriteInt64(output, 0); Marshal.WriteInt64(output, 8, 0);
                    int result = auth(42, name, output, IntPtr.Add(output, 8));
                    if (ClientIds.Contains(id))
                    {
                        Require(result == 0 && Marshal.ReadInt64(output) == data + CodeStringOffset &&
                            Marshal.ReadInt64(output, 8) == LocalCode.Length, "Synthetic auth ABI failed.");
                        Require(auth(42, name, IntPtr.Zero, IntPtr.Add(output, 8)) != 0, "Null output was accepted.");
                        Require(auth(42, name, output, IntPtr.Zero) != 0, "Null length was accepted.");
                    }
                    else Require(result == 0x1234 && Marshal.ReadInt64(output) == 0, "Nonlocal auth was intercepted.");
                }
                finally { Marshal.FreeHGlobal(name); }
            }
            Require(auth(0, IntPtr.Zero, output, IntPtr.Add(output, 8)) == 0x1234, "Null ID did not preserve original behavior.");
            Require(destroy(new IntPtr(data + CodeStringOffset)) == 0, "Synthetic handle release failed.");
            Require(destroy(IntPtr.Zero) == 0x5678 && destroy(new IntPtr(data + CodeStringOffset + 1)) == 0x5678,
                "Nonlocal handles were intercepted.");
            byte[] counters = Read(process, data + CounterOffset, 32);
            for (int i = 0; i < 4; i++) Require(BitConverter.ToInt64(counters, i * 8) == 1, "Adapter call counters are incorrect.");
            Require(JumpTarget(JumpPatch(code, 15)) == code, "Absolute jump encoding failed.");
            byte[] entry = Enumerable.Repeat((byte)0x90, 16).ToArray();
            byte[] patch = JumpPatch(code + AuthOffset, entry.Length);
            long testEntry = data + 0x800;
            Write(process, testEntry, entry);
            uint protection;
            ReplaceCode(process, testEntry, entry, patch, out protection);
            Require(protection == ReadWrite, "Patch lost the original protection.");
            // Simulate a short write in this helper's own allocation, never FIFA.
            Write(process, testEntry, patch.Take(7).Concat(entry.Skip(7)).ToArray());
            Require(RestoreAttemptedPatch(process, testEntry, entry, patch, protection) &&
                Read(process, testEntry, entry.Length).SequenceEqual(entry), "Partial-write rollback failed.");
            Console.WriteLine("PASS native adapter: local IDs, exact matching, output ABI, null outputs, handle ownership, fallback, counters, relocation, jump encoding, partial-write rollback.");
            Console.WriteLine("No FIFA process was opened or modified by these self-tests.");
        }
        finally
        {
            Marshal.FreeHGlobal(output);
            VirtualFreeEx(process, new IntPtr(code), UIntPtr.Zero, 0x8000);
            VirtualFreeEx(process, new IntPtr(data), UIntPtr.Zero, 0x8000);
        }
    }

    private static int Main(string[] args)
    {
        StreamWriter log = null;
        TextWriter originalOutput = Console.Out, originalError = Console.Error;
        try
        {
            if (args.Length == 1 && args[0] == "--self-test") { SelfTest(); return 0; }
            int pid = 0; string path = null, logPath = null; bool statusOnly = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--status") statusOnly = true;
                else if (args[i] == "--pid" && i + 1 < args.Length) pid = int.Parse(args[++i]);
                else if (args[i] == "--game" && i + 1 < args.Length) path = args[++i];
                else if (args[i] == "--log" && i + 1 < args.Length) logPath = args[++i];
                else throw new ArgumentException("Usage: --pid N --game <FIFA21.exe> [--status] [--log <absolute path>], or --self-test");
            }
            Require(pid > 0 && !string.IsNullOrWhiteSpace(path), "Specify the exact FIFA PID and executable path.");
            if (logPath != null)
            {
                Require(Path.IsPathRooted(logPath), "The bridge log path must be absolute.");
                log = new StreamWriter(new FileStream(logPath, FileMode.Create, FileAccess.Write, FileShare.Read));
                log.AutoFlush = true;
                Console.SetOut(log);
                Console.SetError(log);
            }
            Install(pid, path, statusOnly);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("[ERROR] " + error.Message);
            return 1;
        }
        finally
        {
            Console.SetOut(originalOutput);
            Console.SetError(originalError);
            if (log != null) log.Dispose();
        }
    }
}
