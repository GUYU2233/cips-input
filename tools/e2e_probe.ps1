# End-to-end probe for CIPS.
#
# Finds the CIPS text service the way a user would -- cycling the input
# language with Win+Space -- and after every hop tries the hotkey. Success is
# detected by reading the document back over the clipboard, with a sentinel
# written first so that "nothing was typed" cannot look like success.
#
# Switching input methods programmatically through the TSF profile manager is
# not reachable from PowerShell without a COM shim, and LoadKeyboardLayout on a
# text-service KLID only resolves to the language default, so cycling is the
# honest way to drive this.
#
# -Hops 1 skips the Win+Space cycling and only tests whatever input method is
# already selected, which is what you want once CIPS is set as the default.

param([int] $Hops = 10)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Runtime.InteropServices;

public static class Native
{
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct HARDWAREINPUT { public uint uMsg; public ushort wParamL; public ushort wParamH; }
    [StructLayout(LayoutKind.Explicit)]
    public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; [FieldOffset(0)] public HARDWAREINPUT hi; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public INPUTUNION u; }

    [DllImport("user32.dll")] public static extern IntPtr GetKeyboardLayout(uint idThread);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")] public static extern IntPtr GetClassLongPtr(IntPtr hWnd, int index);

    const uint INPUT_KEYBOARD = 1;
    const uint KEYEVENTF_KEYUP = 0x0002;

    static INPUT Key(ushort vk, bool up)
    {
        INPUT i = new INPUT();
        i.type = INPUT_KEYBOARD;
        i.u.ki.wVk = vk;
        i.u.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
        return i;
    }

    public static uint Press(ushort vk)
    {
        INPUT[] seq = new INPUT[] { Key(vk, false), Key(vk, true) };
        return SendInput((uint)seq.Length, seq, Marshal.SizeOf(typeof(INPUT)));
    }

    public static uint Chord(params ushort[] vks)
    {
        int n = vks.Length;
        INPUT[] seq = new INPUT[n * 2];
        for (int i = 0; i < n; i++) seq[i] = Key(vks[i], false);
        for (int i = 0; i < n; i++) seq[n + i] = Key(vks[n - 1 - i], true);
        return SendInput((uint)seq.Length, seq, Marshal.SizeOf(typeof(INPUT)));
    }
}
'@

$VK_CONTROL = 0x11; $VK_MENU = 0x12; $VK_V = 0x56; $VK_A = 0x41; $VK_C = 0x43
$VK_LWIN = 0x5B; $VK_SPACE = 0x20

$payload = 'CIPS端到端E2E-2026'
$sentinel = 'SENTINEL-NOTHING-TYPED'

function Get-DocumentText {
    Set-Clipboard -Value $sentinel
    Start-Sleep -Milliseconds 200
    [void][Native]::Chord($VK_CONTROL, $VK_A)
    Start-Sleep -Milliseconds 120
    [void][Native]::Chord($VK_CONTROL, $VK_C)
    Start-Sleep -Milliseconds 450
    return [string](Get-Clipboard -Raw)
}

$tmp = Join-Path $env:TEMP ('cips-e2e-' + [Guid]::NewGuid().ToString('N') + '.txt')
Set-Content -LiteralPath $tmp -Value '' -Encoding UTF8
$started = Get-Date
Start-Process notepad.exe -ArgumentList "`"$tmp`"" | Out-Null

$target = $null
$hwnd = [IntPtr]::Zero
for ($i = 0; $i -lt 100; $i++) {
    Start-Sleep -Milliseconds 200
    $cand = Get-Process notepad -ErrorAction SilentlyContinue |
        Where-Object { $_.StartTime -ge $started.AddSeconds(-2) -and $_.MainWindowHandle -ne 0 } |
        Sort-Object StartTime -Descending | Select-Object -First 1
    if ($cand) { $target = $cand; $hwnd = $cand.MainWindowHandle; break }
}
if (-not $target) { throw 'notepad window never appeared' }
"notepad pid={0} hwnd=0x{1:X}" -f $target.Id, $hwnd.ToInt64()

[void][Native]::ShowWindow($hwnd, 9)
[void][Native]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 800

$sawCips = $false
$attemptUsed = 0

for ($try = 1; $try -le $Hops; $try++) {
    if ([Native]::GetForegroundWindow() -ne $hwnd) {
        [void][Native]::SetForegroundWindow($hwnd)
        Start-Sleep -Milliseconds 300
    }

    $tid = [Native]::GetWindowThreadProcessId($hwnd, [ref]$null)
    $layout = [Native]::GetKeyboardLayout($tid)
    $before = Get-DocumentText
    if ($before -ne $sentinel) { $before = '' }   # document was already empty

    Set-Clipboard -Value $payload
    Start-Sleep -Milliseconds 250
    [void][Native]::Chord($VK_CONTROL, $VK_MENU, $VK_V)
    Start-Sleep -Milliseconds 1600
    $after = Get-DocumentText

    "  try {0,2}: layout=0x{1:X8} -> '{2}'" -f $try, $layout.ToInt64(), $after

    if ($after -eq $payload) {
        $sawCips = $true
        $attemptUsed = $try
        break
    }

    # Clear whatever ended up in the document before the next hop.
    if ($after -ne $sentinel -and $after -ne '') {
        [void][Native]::Chord($VK_CONTROL, $VK_A)
        Start-Sleep -Milliseconds 100
        [void][Native]::Press(0x2E)
    }

    if ($try -lt $Hops) {
        [void][Native]::Chord($VK_LWIN, $VK_SPACE)
        Start-Sleep -Milliseconds 1100
    }
}

""
if ($sawCips) {
    "RESULT: CIPS injected the payload on Win+Space hop #{0}" -f $attemptUsed
} else {
    "RESULT: no hop produced the payload after $Hops tries"
}

$cipsLog = Join-Path $env:LOCALAPPDATA 'PIME\Logs\cips.log'
"--- cips.log"
if (Test-Path $cipsLog) { Get-Content $cipsLog } else { 'cips.log absent: the CIPS backend never activated' }

if (-not $target.HasExited) { $target.CloseMainWindow() | Out-Null; Start-Sleep -Milliseconds 600 }
if (-not $target.HasExited) { $target.Kill() }
Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
