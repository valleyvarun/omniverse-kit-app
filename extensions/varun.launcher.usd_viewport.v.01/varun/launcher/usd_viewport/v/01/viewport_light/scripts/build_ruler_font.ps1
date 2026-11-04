param([Parameter(Mandatory=$true)][string]$FontPath)

# Rasterize the application's font once; the viewport has no font-library dependency.
Add-Type -AssemblyName System.Drawing
$outputDir = Split-Path -Parent $PSScriptRoot
$characters = '0123456789-.'
$cellWidth = 32
$cellHeight = 48
$fontSize = 32
$fonts = New-Object System.Drawing.Text.PrivateFontCollection
$fonts.AddFontFile((Resolve-Path -LiteralPath $FontPath).Path)
$font = New-Object System.Drawing.Font($fonts.Families[0], $fontSize, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$bitmap = New-Object System.Drawing.Bitmap(($cellWidth * $characters.Length), $cellHeight)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::Transparent)
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$format = [System.Drawing.StringFormat]::GenericTypographic.Clone()
$advances = @{}
for ($index = 0; $index -lt $characters.Length; $index++) {
    $character = $characters.Substring($index, 1)
    $graphics.DrawString($character, $font, [System.Drawing.Brushes]::White, [single]($index * $cellWidth), [single]0, $format)
    $advances[$character] = $graphics.MeasureString($character, $font, [System.Drawing.PointF]::Empty, $format).Width
}
$bitmap.Save((Join-Path $outputDir 'ruler_font.png'), [System.Drawing.Imaging.ImageFormat]::Png)
@{characters=$characters; cell_width=$cellWidth; cell_height=$cellHeight; font_size=$fontSize; advances=$advances} | ConvertTo-Json | Set-Content (Join-Path $outputDir 'ruler_font.json')
$format.Dispose()
$graphics.Dispose()
$bitmap.Dispose()
$font.Dispose()
$fonts.Dispose()
