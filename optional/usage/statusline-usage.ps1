# Claude Code status line: shows plan usage, and records it where an agent can
# read it. Claude Code pipes session JSON to stdin; `rate_limits` is present only
# for claude.ai subscribers, and only after the session's first API response.
# Output file: usage-status.json in this account's config folder ($CLAUDE_CONFIG_DIR,
# default ~/.claude), read by the `coordinator` agent.
# Every session on the account writes that one file, and an idle session's rate_limits go stale, so
# the write merges (docs/launcher.md "Usage"): per window, a later resets_at (more than an hour on)
# is a new window and replaces; the same window keeps the higher percentage; an earlier one is
# dropped. A reading with any window dropped and none raised is stale: it writes nothing, so
# updated_at stays the time of the newest reading that is not. Read, merge and write run under an
# exclusive lock file (usage-status.json.lock, deleted on close), so concurrent sessions lose no
# reading; the write is a temp file moved into place, so a reader never sees a part-written file.
$ErrorActionPreference = 'SilentlyContinue'
$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { $j = $null }

function Window($w) {
    if ($null -eq $w) { return $null }
    $resets = $null
    if ($w.resets_at) { $resets = ([DateTimeOffset]::FromUnixTimeSeconds([long]$w.resets_at)).LocalDateTime.ToString('o') }
    [ordered]@{ used_percentage = $w.used_percentage; resets_at = $resets }
}

function ResetsOf($w) {
    # A window's resets_at as a DateTimeOffset, or $null. PowerShell 7's ConvertFrom-Json gives a DateTime.
    if ($null -eq $w -or $null -eq $w.resets_at -or '' -eq $w.resets_at) { return $null }
    if ($w.resets_at -is [DateTime]) { return [DateTimeOffset]$w.resets_at }
    try { return [DateTimeOffset]::Parse([string]$w.resets_at, [Globalization.CultureInfo]::InvariantCulture) } catch { return $null }
}

function Merge($old, $new) {
    # (outcome, window to keep): 'newer' takes $new, 'same' (equal figure) takes $new, 'older'
    # keeps $old, 'none' ($new gives no comparable reading) keeps $old.
    if ($null -eq $new -or $null -eq $new.used_percentage) { return 'none', $old }
    $or = ResetsOf $old
    if ($null -eq $old -or $null -eq $old.used_percentage -or $null -eq $or) { return 'newer', $new }
    $nr = ResetsOf $new
    if ($null -eq $nr) { return 'none', $old }
    $hours = ($nr - $or).TotalHours
    if ($hours -gt 1) { return 'newer', $new }
    if ($hours -lt -1) { return 'older', $old }
    $d = [double]$new.used_percentage - [double]$old.used_percentage
    if ($d -gt 0) { return 'newer', $new }
    if ($d -eq 0) { return 'same', $new }
    return 'older', $old
}

$limits = $null
if ($j) { $limits = $j.rate_limits }
$parts = @()
if ($j -and $j.model.display_name) { $parts += "[$($j.model.display_name)]" }

if ($limits) {
    # Each account's config folder gets its own file (CLAUDE_CONFIG_DIR, default ~/.claude).
    $cfg = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
    $path = Join-Path $cfg 'usage-status.json'
    $tmp = "$path.$PID.tmp"
    # The lock: no sharing, so a second session waits; the system closes (and deletes) it if this
    # process dies. Not had in 2 s: this refresh writes nothing; the next one tries again.
    $lock = $null
    for ($i = 0; $i -lt 40 -and $null -eq $lock; $i++) {
        try {
            $lock = [IO.FileStream]::new("$path.lock", [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite,
                                         [IO.FileShare]::None, 1, [IO.FileOptions]::DeleteOnClose)
        } catch { Start-Sleep -Milliseconds 50 }
    }
    if ($lock) {
        try {
            # A file that is there but cannot be read (held open by a reader) is never overwritten
            # unseen; one that reads but does not parse is.
            $old = $null
            $readable = $true
            if (Test-Path -LiteralPath $path) {
                try { $text = [IO.File]::ReadAllText($path) } catch { $readable = $false }
                if ($readable) {
                    try { $old = $text | ConvertFrom-Json } catch { $old = $null }
                    if ($old -isnot [Management.Automation.PSCustomObject]) { $old = $null }
                }
            }
            if ($readable) {
                $out = [ordered]@{ updated_at = (Get-Date).ToString('o'); session_id = $j.session_id }
                $stale = $false
                $rose = $false
                foreach ($k in 'five_hour', 'seven_day', 'spend_limit') {
                    $ow = $null
                    if ($old) { $ow = $old.$k }
                    $r = Merge $ow (Window $limits.$k)
                    if ($r[0] -eq 'older') { $stale = $true }
                    if ($r[0] -eq 'newer') { $rose = $true }
                    $out[$k] = $r[1]
                }
                if ($rose -or -not $stale) {
                    ($out | ConvertTo-Json -Depth 4) | Out-File -FilePath $tmp -Encoding utf8
                    # A target held open by a reader fails the move silently: retry once, and never
                    # leave the temp file behind.
                    Move-Item -Force $tmp $path
                    if (Test-Path -LiteralPath $tmp) {
                        Start-Sleep -Milliseconds 100
                        Move-Item -Force $tmp $path
                    }
                    if (Test-Path -LiteralPath $tmp) { Remove-Item -Force -LiteralPath $tmp }
                }
            }
        } finally {
            $lock.Dispose()
        }
    }
    # Sweep this script's own leftovers (older runs): one filter over this folder, no recursion.
    # UTC both sides: local times repeat for an hour when daylight saving ends.
    $stale = (Get-Date).ToUniversalTime().AddMinutes(-5)
    Get-ChildItem -LiteralPath $cfg -Filter 'usage-status.json.*.tmp' -File |
        Where-Object { $_.Name -match '^usage-status\.json\.\d+\.tmp$' -and $_.LastWriteTimeUtc -lt $stale } |
        Remove-Item -Force
    if ($limits.five_hour) { $parts += ('5h {0:N0}%' -f $limits.five_hour.used_percentage) }
    if ($limits.seven_day) { $parts += ('7d {0:N0}%' -f $limits.seven_day.used_percentage) }
} else {
    $parts += 'usage n/a'
}
if ($j -and $null -ne $j.context_window.used_percentage) { $parts += ('ctx {0:N0}%' -f $j.context_window.used_percentage) }
Write-Output ($parts -join '  ')
