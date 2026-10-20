[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [string]$InputPng,

    [Parameter(Position = 1)]
    [string]$ResourceId = "",

    [string]$TexconvPath = "",

    # New defaults tuned to look closer to FIFA 21 promo cards:
    # - a bit larger than v3
    # - slightly right
    # - noticeably higher
    [ValidateRange(50, 130)]
    [int]$ScalePercent = 100,

    [ValidateRange(-90, 90)]
    [int]$XOffset = 2,

    [ValidateRange(-90, 90)]
    [int]$YOffset = -10,

    [ValidateRange(80, 180)]
    [int]$FitBoxWidth = 162,

    [ValidateRange(80, 180)]
    [int]$FitBoxHeight = 170,

    [ValidateRange(0, 180)]
    [int]$FitBoxCenterX = 88,

    [ValidateRange(0, 180)]
    [int]$FitBoxBottomY = 175
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-Texconv {
    param([string]$RequestedPath)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        if (-not (Test-Path -LiteralPath $RequestedPath -PathType Leaf)) {
            throw "texconv.exe was not found at: $RequestedPath"
        }
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    $besideScript = Join-Path $PSScriptRoot "texconv.exe"
    if (Test-Path -LiteralPath $besideScript -PathType Leaf) {
        return $besideScript
    }

    $cmd = Get-Command "texconv.exe" -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        return $cmd.Source
    }

    return $null
}

function Get-VisibleBounds {
    param(
        [Parameter(Mandatory = $true)]
        [System.Drawing.Bitmap]$Bitmap,

        [byte]$AlphaThreshold = 4
    )

    $rect = New-Object System.Drawing.Rectangle(0, 0, $Bitmap.Width, $Bitmap.Height)
    $format = [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    $data = $Bitmap.LockBits(
        $rect,
        [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
        $format
    )

    try {
        $stride = [Math]::Abs($data.Stride)
        $byteCount = $stride * $Bitmap.Height
        $pixels = New-Object byte[] $byteCount
        [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $pixels, 0, $byteCount)

        $minX = $Bitmap.Width
        $minY = $Bitmap.Height
        $maxX = -1
        $maxY = -1

        for ($y = 0; $y -lt $Bitmap.Height; $y++) {
            $row = $y * $stride
            for ($x = 0; $x -lt $Bitmap.Width; $x++) {
                $alpha = $pixels[$row + ($x * 4) + 3]
                if ($alpha -gt $AlphaThreshold) {
                    if ($x -lt $minX) { $minX = $x }
                    if ($x -gt $maxX) { $maxX = $x }
                    if ($y -lt $minY) { $minY = $y }
                    if ($y -gt $maxY) { $maxY = $y }
                }
            }
        }

        if ($maxX -lt $minX -or $maxY -lt $minY) {
            return New-Object System.Drawing.Rectangle(0, 0, $Bitmap.Width, $Bitmap.Height)
        }

        return New-Object System.Drawing.Rectangle(
            $minX,
            $minY,
            ($maxX - $minX + 1),
            ($maxY - $minY + 1)
        )
    }
    finally {
        $Bitmap.UnlockBits($data)
    }
}

try {
    Add-Type -AssemblyName System.Drawing

    $inputPath = (Resolve-Path -LiteralPath $InputPng -ErrorAction Stop).Path

    if ([System.IO.Path]::GetExtension($inputPath).ToLowerInvariant() -ne ".png") {
        throw "Please use a PNG file. Received: $inputPath"
    }

    $texconv = Find-Texconv -RequestedPath $TexconvPath
    if ($null -eq $texconv) {
        throw @"
texconv.exe could not be found.

Put texconv.exe in the SAME folder as this script, or install DirectXTex
and make texconv.exe available on PATH.
"@
    }

    if ([string]::IsNullOrWhiteSpace($ResourceId)) {
        $ResourceId = Read-Host "Enter the custom card resource ID (example 50527512), or press Enter to keep the PNG name"
    }

    $sourceDir = Split-Path -Parent $inputPath
    $sourceBase = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)

    if (-not [string]::IsNullOrWhiteSpace($ResourceId)) {
        if ($ResourceId -notmatch '^\d+$') {
            throw "Resource ID must contain numbers only."
        }
        $outputBase = "p$ResourceId"
    }
    else {
        $outputBase = $sourceBase
    }

    $outputPath = Join-Path $sourceDir ($outputBase + ".dds")
    $previewPath = Join-Path $sourceDir ($outputBase + "-preview.png")

    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("fifa21-dds-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $preparedPng = Join-Path $tempDir ($outputBase + ".png")

    Write-Host ""
    Write-Host "Preparing FIFA 21 dynamic image..." -ForegroundColor Cyan
    Write-Host "  Source: $inputPath"
    Write-Host ""

    $sourceImage = [System.Drawing.Image]::FromFile($inputPath)
    try {
        $sourceBitmap = New-Object System.Drawing.Bitmap(
            $sourceImage.Width,
            $sourceImage.Height,
            [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
        )

        $copyGraphics = [System.Drawing.Graphics]::FromImage($sourceBitmap)
        try {
            $copyGraphics.Clear([System.Drawing.Color]::Transparent)
            $copyGraphics.DrawImage($sourceImage, 0, 0, $sourceImage.Width, $sourceImage.Height)
        }
        finally {
            $copyGraphics.Dispose()
        }
    }
    finally {
        $sourceImage.Dispose()
    }

    try {
        $visible = Get-VisibleBounds -Bitmap $sourceBitmap

        Write-Host "Visible subject:"
        Write-Host ("  {0}x{1} at ({2},{3})" -f $visible.Width, $visible.Height, $visible.X, $visible.Y)
        Write-Host ""

        # Fit the visible subject into a promo-safe area while preserving aspect ratio.
        $baseScale = [Math]::Min(
            $FitBoxWidth / [double]$visible.Width,
            $FitBoxHeight / [double]$visible.Height
        )

        $scale = $baseScale * ($ScalePercent / 100.0)

        $destWidth = [Math]::Max(1, [int][Math]::Round($visible.Width * $scale))
        $destHeight = [Math]::Max(1, [int][Math]::Round($visible.Height * $scale))

        $fitBoxLeft = [int][Math]::Round($FitBoxCenterX - ($FitBoxWidth / 2.0))
        $destX = [int][Math]::Round($fitBoxLeft + (($FitBoxWidth - $destWidth) / 2.0)) + $XOffset
        $destY = [int]($FitBoxBottomY - $destHeight) + $YOffset

        $canvas = New-Object System.Drawing.Bitmap(
            180,
            180,
            [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
        )

        try {
            $graphics = [System.Drawing.Graphics]::FromImage($canvas)
            try {
                $graphics.Clear([System.Drawing.Color]::Transparent)
                $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

                $destRect = New-Object System.Drawing.Rectangle($destX, $destY, $destWidth, $destHeight)

                $graphics.DrawImage(
                    $sourceBitmap,
                    $destRect,
                    $visible.X,
                    $visible.Y,
                    $visible.Width,
                    $visible.Height,
                    [System.Drawing.GraphicsUnit]::Pixel
                )
            }
            finally {
                $graphics.Dispose()
            }

            $canvas.Save($preparedPng, [System.Drawing.Imaging.ImageFormat]::Png)
            $canvas.Save($previewPath, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $canvas.Dispose()
        }
    }
    finally {
        $sourceBitmap.Dispose()
    }

    & $texconv `
        -nologo `
        -y `
        -f BC3_UNORM `
        -m 1 `
        -o $tempDir `
        $preparedPng

    if ($LASTEXITCODE -ne 0) {
        throw "texconv failed with exit code $LASTEXITCODE."
    }

    $converted = Join-Path $tempDir ($outputBase + ".dds")
    if (-not (Test-Path -LiteralPath $converted -PathType Leaf)) {
        throw "texconv did not produce the expected DDS file."
    }

    $bytes = [System.IO.File]::ReadAllBytes($converted)
    if ($bytes.Length -lt 128 -or
        $bytes[0] -ne 0x44 -or
        $bytes[1] -ne 0x44 -or
        $bytes[2] -ne 0x53 -or
        $bytes[3] -ne 0x20) {
        throw "The converted file does not have a valid DDS header."
    }

    Copy-Item -LiteralPath $converted -Destination $outputPath -Force

    Write-Host ""
    Write-Host "Done." -ForegroundColor Green
    Write-Host "Created DDS:"
    Write-Host "  $outputPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "Created preview:"
    Write-Host "  $previewPath"
    Write-Host ""
    Write-Host "Defaults used:"
    Write-Host "  ScalePercent  = $ScalePercent"
    Write-Host "  XOffset       = $XOffset"
    Write-Host "  YOffset       = $YOffset"
    Write-Host "  FitBoxWidth   = $FitBoxWidth"
    Write-Host "  FitBoxHeight  = $FitBoxHeight"
    Write-Host "  FitBoxCenterX = $FitBoxCenterX"
    Write-Host "  FitBoxBottomY = $FitBoxBottomY"
    Write-Host ""

    if (-not [string]::IsNullOrWhiteSpace($ResourceId)) {
        Write-Host "For LocalFUT custom portraits, copy the DDS to:"
        Write-Host "  localfut\runtime-source\localfut21\custom-playerheads\$outputBase.dds"
        Write-Host ""
    }

    Write-Host "If you want to fine-tune a specific player, rerun with something like:"
    Write-Host "  -ScalePercent 98 -XOffset 4 -YOffset -8"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
}
finally {
    if ($null -ne $tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ($Host.Name -match "ConsoleHost") {
        Write-Host "Press Enter to close..."
        [void](Read-Host)
    }
}
