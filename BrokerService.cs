using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;
using TimeoutException = System.TimeoutException;

internal sealed class BrokerService : ServiceBase
{
    private const string Name = "ShadowEnclaveV4";
    private const string Provider = "Microsoft Passport Key Storage Provider";
    private const int Limit = 1048576;
    private const int PipeCount = 8;
    private volatile bool stopping;
    private readonly List<NamedPipeServerStream> pipes = new List<NamedPipeServerStream>();
    private readonly List<Thread> workers = new List<Thread>();
    private static readonly object StateLock = new object();
    private static readonly HashSet<ClientSession> Sessions = new HashSet<ClientSession>();
    private static JavaScriptSerializer Json
    {
        get { return new JavaScriptSerializer { MaxJsonLength = Limit, RecursionLimit = 12 }; }
    }

    private sealed class ClientSession
    {
        public string Owner;
        public int WindowsSession;
        public string Credential;
        public string PublicKey;
        public volatile bool Revoked;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct KeyName { public string Name, Algorithm; public int Legacy, Flags; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Attributes { public int Length; public IntPtr Descriptor; public int Inherit; }
    [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
    private static extern int NCryptOpenStorageProvider(out IntPtr handle, string name, int flags);
    [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
    private static extern int NCryptEnumKeys(IntPtr provider, string scope, out IntPtr key, ref IntPtr state, int flags);
    [DllImport("ncrypt.dll")] private static extern int NCryptFreeObject(IntPtr value);
    [DllImport("ncrypt.dll")] private static extern int NCryptFreeBuffer(IntPtr value);
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr token, int kind, out int data, int size, out int needed);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string sddl, uint revision, out IntPtr descriptor, IntPtr size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafePipeHandle CreateNamedPipe(string name, uint openMode, uint pipeMode, uint instances,
        uint output, uint input, uint timeout, ref Attributes attributes);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);

    private static string Text(IDictionary<string, object> data, string name)
    {
        object value;
        if (!data.TryGetValue(name, out value) || !(value is string)) throw new InvalidDataException("Invalid request.");
        return (string)value;
    }
    private static byte[] Decode(string text)
    {
        if (text.Length > Limit) throw new InvalidDataException("Request too large.");
        return Convert.FromBase64String(text);
    }
    private static NamedPipeServerStream MakePipe(bool first)
    {
        IntPtr descriptor;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptor(
            "O:LSD:P(D;;GA;;;NU)(A;;GA;;;LS)(A;;0x120083;;;IU)", 1, out descriptor, IntPtr.Zero))
            throw new Win32Exception();
        try
        {
            var attributes = new Attributes { Length = Marshal.SizeOf(typeof(Attributes)), Descriptor = descriptor };
            // FIRST_PIPE_INSTANCE prevents silently sharing a name with a squatting server.
            uint mode = 0x40000003U | (first ? 0x00080000U : 0U);
            var handle = CreateNamedPipe(@"\\.\pipe\ShadowEnclaveV4", mode, 8, PipeCount, 65536, 65536, 0, ref attributes);
            if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(); }
            try { return new NamedPipeServerStream(PipeDirection.InOut, true, false, handle); }
            catch { handle.Dispose(); throw; }
        }
        finally { LocalFree(descriptor); }
    }
    private static byte[] ReadBytes(PipeStream pipe, int count, Stopwatch timer, int timeout)
    {
        byte[] output = new byte[count];
        for (int offset = 0; offset < count;)
        {
            var operation = pipe.BeginRead(output, offset, count - offset, null, null);
            long remaining = timeout - timer.ElapsedMilliseconds;
            if (remaining <= 0 || !operation.AsyncWaitHandle.WaitOne((int)remaining))
                throw new TimeoutException("Authorization timed out.");
            int read = pipe.EndRead(operation);
            if (read == 0) throw new EndOfStreamException();
            offset += read;
        }
        return output;
    }
    private static byte[] ReadFrame(PipeStream pipe, int timeout, bool idle = false)
    {
        // An idle authenticated connection has no deadline until it starts a frame.
        int first = idle ? pipe.ReadByte() : -1;
        if (idle && first < 0) throw new EndOfStreamException();
        var timer = Stopwatch.StartNew();
        byte[] header = ReadBytes(pipe, idle ? 3 : 4, timer, timeout);
        int size = idle ? first | (header[0] << 8) | (header[1] << 16) | (header[2] << 24)
                        : BitConverter.ToInt32(header, 0);
        if (size < 1 || size > Limit) throw new InvalidDataException("Request too large.");
        return ReadBytes(pipe, size, timer, timeout);
    }
    private static void Send(PipeStream pipe, object value)
    {
        byte[] content = Encoding.UTF8.GetBytes(Json.Serialize(value));
        if (content.Length > Limit) throw new InvalidDataException("Response too large.");
        byte[] frame = new byte[content.Length + 4];
        Buffer.BlockCopy(BitConverter.GetBytes(content.Length), 0, frame, 0, 4);
        Buffer.BlockCopy(content, 0, frame, 4, content.Length);
        var operation = pipe.BeginWrite(frame, 0, frame.Length, null, null);
        if (!operation.AsyncWaitHandle.WaitOne(15000)) throw new TimeoutException();
        pipe.EndWrite(operation);
    }
    private static bool PassportPolicy(byte[] expected)
    {
        IntPtr provider;
        if (NCryptOpenStorageProvider(out provider, Provider, 0) != 0) return false;
        IntPtr state = IntPtr.Zero;
        try
        {
            for (int i = 0; i < 256; i++)
            {
                IntPtr item;
                if (NCryptEnumKeys(provider, null, out item, ref state, 0x40) != 0) return false;
                try
                {
                    var info = (KeyName)Marshal.PtrToStructure(item, typeof(KeyName));
                    if (info.Algorithm != "RSA") continue;
                    try
                    {
                        using (var key = CngKey.Open(info.Name, new CngProvider(Provider), CngKeyOpenOptions.Silent))
                        {
                            if (!StructuralComparisons.StructuralEqualityComparer.Equals(
                                expected, key.Export(new CngKeyBlobFormat("RSAPUBLICBLOB")))) continue;
                            byte[] policy;
                            try { policy = key.GetProperty("NgcCacheType", CngPropertyOptions.None).GetValue(); }
                            catch (CryptographicException) {
                                policy = key.GetProperty("NgcCacheTypeProperty", CngPropertyOptions.None).GetValue();
                            }
                            // Fail closed if this Windows build cannot expose mandatory-auth policy.
                            return key.KeySize >= 2048 && policy.Length == 4 && BitConverter.ToInt32(policy, 0) == 1
                                && ((int)key.KeyUsage & 8) == 0;
                        }
                    }
                    catch (CryptographicException) { return false; }
                }
                finally { NCryptFreeBuffer(item); }
            }
            return false;
        }
        finally { if (state != IntPtr.Zero) NCryptFreeBuffer(state); NCryptFreeObject(provider); }
    }
    private static IDictionary<string, object> Backend(object request)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        var start = new ProcessStartInfo(Path.Combine(root, "backend", "EnclaveBackend.exe")) {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        // Do not inherit user-supplied Python or CLR instrumentation variables.
        foreach (string key in new [] { "PYTHONPATH", "PYTHONHOME", "COR_ENABLE_PROFILING", "COR_PROFILER",
            "COR_PROFILER_PATH", "DOTNET_STARTUP_HOOKS" }) start.EnvironmentVariables.Remove(key);
        using (var child = Process.Start(start))
        {
            try
            {
                var output = child.StandardOutput.ReadToEndAsync();
                var errors = child.StandardError.ReadToEndAsync();
                child.StandardInput.WriteLine(Json.Serialize(request));
                child.StandardInput.Close();
                if (!child.WaitForExit(90000)) throw new TimeoutException("The protected backend timed out.");
                if (!output.Wait(5000) || !errors.Wait(5000) || output.Result.Length > Limit)
                    throw new InvalidDataException("Invalid backend response.");
                return Json.Deserialize<Dictionary<string, object>>(output.Result);
            }
            finally { if (!child.HasExited) { child.Kill(); child.WaitForExit(5000); } }
        }
    }
    private static ClientSession Identify(NamedPipeServerStream pipe)
    {
        var peer = new ClientSession();
        pipe.RunAsClient(delegate {
            using (var identity = WindowsIdentity.GetCurrent())
            {
                if (identity.ImpersonationLevel != TokenImpersonationLevel.Impersonation)
                    throw new UnauthorizedAccessException("Client impersonation is required.");
                peer.Owner = identity.User.Value;
                int length;
                if (!GetTokenInformation(identity.Token, 12, out peer.WindowsSession, 4, out length) || length != 4)
                    throw new Win32Exception();
            }
        });
        return peer;
    }

    private static bool RequireHello(NamedPipeServerStream pipe, ClientSession peer, byte[] raw,
        IDictionary<string, object> request, IDictionary<string, object> state)
    {
        string op = Text(request, "op");
        bool enrolled = (bool)state["enrolled"];
        string credential = Text(request, "credential");
        byte[] publicKey = Decode(Text(request, "public_key"));
        if (publicKey.Length < 256 || publicKey.Length > 600 || credential.Length > 160
            || !credential.StartsWith("ShadowEnclave.V4.", StringComparison.Ordinal))
            throw new InvalidDataException("Invalid V4 credential.");
        if (enrolled && (credential != Text(state, "credential") ||
            !StructuralComparisons.StructuralEqualityComparer.Equals(publicKey, Decode(Text(state, "public_key")))))
            throw new UnauthorizedAccessException("The pinned Hello credential does not match.");
        bool valid = false;
        pipe.RunAsClient(delegate { valid = PassportPolicy(publicKey); });
        if (!valid) throw new UnauthorizedAccessException("Hello key provenance or mandatory-authentication policy could not be verified.");
        byte[] nonce = new byte[32];
        using (var random = RandomNumberGenerator.Create()) random.GetBytes(nonce);
        byte[] challenge;
        using (var hash = SHA256.Create())
            challenge = Encoding.UTF8.GetBytes(String.Join("\n", new [] {
                "ShadowEnclave/V4/Broker/1", op, peer.Owner, peer.WindowsSession.ToString(),
                Convert.ToBase64String(hash.ComputeHash(raw)), Convert.ToBase64String(nonce) }));
        var timer = Stopwatch.StartNew();
        Send(pipe, new { challenge = Convert.ToBase64String(challenge) });
        var proof = Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(ReadFrame(pipe, 120000)));
        object cancelled;
        if (proof.TryGetValue("cancelled", out cancelled) && Object.Equals(cancelled, true))
            return false;
        byte[] signature = Decode(Text(proof, "signature"));
        if (timer.ElapsedMilliseconds >= 120000) throw new TimeoutException("Authorization expired.");
        using (var key = CngKey.Import(publicKey, new CngKeyBlobFormat("RSAPUBLICBLOB")))
        using (var verifier = new RSACng(key))
            if (!verifier.VerifyData(challenge, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1))
                throw new UnauthorizedAccessException("Invalid Windows Hello signature.");
        peer.Credential = credential;
        peer.PublicKey = Convert.ToBase64String(publicKey);
        return true;
    }

    private static IDictionary<string, object> State(string owner)
    {
        var state = Backend(new { op = "status", owner = owner });
        if (state.ContainsKey("error")) throw new InvalidDataException(Text(state, "error"));
        return state;
    }

    private static bool MatchesEnrollment(ClientSession peer, IDictionary<string, object> state)
    {
        return (bool)state["enrolled"] && peer.Credential == Text(state, "credential")
            && peer.PublicKey == Text(state, "public_key");
    }

    private static IDictionary<string, object> Request(byte[] raw)
    {
        var request = Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(raw));
        if (request == null) throw new InvalidDataException("Invalid request.");
        return request;
    }

    private static void Handle(NamedPipeServerStream pipe)
    {
        byte[] raw = ReadFrame(pipe, 15000);
        var request = Request(raw);
        string op = Text(request, "op");
        var peer = Identify(pipe);
        IDictionary<string, object> state;
        lock (StateLock) state = State(peer.Owner);
        if (op == "status")
        {
            state["session_protocol"] = 1;
            Send(pipe, state);
            return;
        }
        if (op != "open_session" && op != "enroll")
            throw new UnauthorizedAccessException("Open V4 with Windows Hello first.");
        if (op == "enroll" ? (bool)state["enrolled"] : !(bool)state["enrolled"])
            throw new UnauthorizedAccessException(op == "enroll" ? "Already enrolled." : "Enroll Windows Hello first.");
        if (!RequireHello(pipe, peer, raw, request, state)) { Send(pipe, new { cancelled = true }); return; }
        lock (StateLock)
        {
            var current = State(peer.Owner);
            if (op == "enroll")
            {
                if ((bool)current["enrolled"]) throw new UnauthorizedAccessException("Already enrolled.");
                var enrolled = Backend(new { op = op, owner = peer.Owner,
                    credential = peer.Credential, public_key = peer.PublicKey });
                if (enrolled.ContainsKey("error")) throw new InvalidDataException(Text(enrolled, "error"));
            }
            else if (!MatchesEnrollment(peer, current))
                throw new UnauthorizedAccessException("The enrollment changed. Open V4 again.");
            Sessions.Add(peer);
        }
        try
        {
            Send(pipe, new { session_open = true });
            while (!peer.Revoked)
            {
                raw = ReadFrame(pipe, 15000, true);
                request = Request(raw);
                bool keepOpen;
                try { keepOpen = HandleSessionRequest(pipe, peer, raw, request); }
                catch (InvalidDataException error)
                {
                    Send(pipe, new { error = error.Message });
                    continue;
                }
                catch (UnauthorizedAccessException error)
                {
                    Send(pipe, new { error = error.Message });
                    continue;
                }
                if (!keepOpen) return;
            }
        }
        finally { lock (StateLock) Sessions.Remove(peer); }
    }

    private static bool HandleSessionRequest(NamedPipeServerStream pipe, ClientSession peer,
        byte[] raw, IDictionary<string, object> request)
    {
        string op = Text(request, "op");
        bool freshHello = op == "decrypt" || op == "save_settings" || op == "clear_api_key" || op == "unenroll";
        if (!freshHello && op != "encrypt" && op != "get_api_key")
            throw new InvalidDataException("Unsupported operation.");

        IDictionary<string, object> state;
        lock (StateLock)
        {
            state = State(peer.Owner);
            if (peer.Revoked || !MatchesEnrollment(peer, state))
            {
                Send(pipe, new { session_closed = true });
                return false;
            }
        }
        if (freshHello && !RequireHello(pipe, peer, raw, request, state))
        {
            Send(pipe, new { cancelled = true });
            return true;
        }

        object payload;
        if (!request.TryGetValue("payload", out payload)) payload = new Dictionary<string, object>();
        IDictionary<string, object> result;
        lock (StateLock)
        {
            if (peer.Revoked || !MatchesEnrollment(peer, State(peer.Owner)))
            {
                Send(pipe, new { session_closed = true });
                return false;
            }
            result = Backend(new { op = op, owner = peer.Owner, payload = payload });
            if (op == "unenroll" && !result.ContainsKey("error"))
                foreach (var session in Sessions)
                    if (session.Owner == peer.Owner) session.Revoked = true;
        }
        Send(pipe, result);
        return !peer.Revoked;
    }

    private void Listen(NamedPipeServerStream pipe)
    {
        while (!stopping)
        {
            bool connected = false;
            try
            {
                pipe.WaitForConnection();
                connected = true;
                if (stopping) return;
                try { Handle(pipe); }
                catch (EndOfStreamException) { }
                catch (IOException) { }
                catch (Exception error)
                {
                    try { Send(pipe, new { error = error is UnauthorizedAccessException || error is TimeoutException
                        ? error.Message : "The broker could not complete this request." }); }
                    catch (Exception) { }
                }
                // Allow the client to read the final reply before disconnect discards unread bytes.
                try { ReadBytes(pipe, 1, Stopwatch.StartNew(), 15000); }
                catch (Exception) { }
            }
            catch (Exception) { if (!stopping) Thread.Sleep(250); }
            finally
            {
                if (!stopping && connected)
                    try { pipe.Disconnect(); } catch (IOException) { } catch (ObjectDisposedException) { }
            }
        }
    }

    protected override void OnStart(string[] args)
    {
        stopping = false;
        try
        {
            for (int i = 0; i < PipeCount; i++) pipes.Add(MakePipe(i == 0));
        }
        catch
        {
            foreach (var pipe in pipes) pipe.Dispose();
            pipes.Clear();
            throw;
        }
        foreach (var pipe in pipes)
        {
            var worker = new Thread(() => Listen(pipe)) { IsBackground = true };
            workers.Add(worker);
            worker.Start();
        }
    }

    protected override void OnStop()
    {
        stopping = true;
        foreach (var pipe in pipes) pipe.Dispose();
        var timer = Stopwatch.StartNew();
        foreach (var worker in workers)
            worker.Join(Math.Max(0, 5000 - (int)timer.ElapsedMilliseconds));
    }

    public static void Main()
    {
        using (var identity = WindowsIdentity.GetCurrent())
            if (identity.User.Value != "S-1-5-19") throw new UnauthorizedAccessException("Run through Windows Service Control Manager.");
        ServiceBase.Run(new BrokerService { ServiceName = Name, CanStop = true, AutoLog = false });
    }
}
