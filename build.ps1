#!/usr/bin/env pwsh
# Build script for creating standalone migrator binary using Docker
# Usage: .\build.ps1

param(
    [string]$OutputDir = ".\dist",
    [switch]$SkipTest = $false
)

$ErrorActionPreference = "Stop"

$version = (Get-Content "src/utilities/version.py") -replace 'version\s*=\s*"(.*)"','$1'

if (-not $SkipTest) {
    Write-Host "🧪 Running tests..." -ForegroundColor Yellow
    python -m pytest tests -v --tb=short --cov=src --cov-report=html
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Tests failed!" -ForegroundColor Red
        exit 1
    }
    Write-Host "`n✅ Tests passed!" -ForegroundColor Green
}

Write-Host "`n🚀 Building Migrator standalone binary v$version..." -ForegroundColor Cyan

# Ensure output directory exists
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# Build the Docker image
Write-Host "`n📦 Building Docker image..." -ForegroundColor Yellow
docker build --no-cache -t migrator-builder:latest --target builder .

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker build failed!" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Docker image built successfully!" -ForegroundColor Green

# Extract the binary from the container
Write-Host "`n📤 Extracting binary from container..." -ForegroundColor Yellow

# Create a temporary container to copy from
$containerId = docker create migrator-builder:latest
try {
    Remove-Item -Path "$OutputDir/migrator" -Force -ErrorAction SilentlyContinue

    docker cp "${containerId}:/app/dist/migrator" "$OutputDir/migrator"
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to extract binary!" -ForegroundColor Red
        exit 1
    }
} finally {
    # Clean up the temporary container
    docker rm $containerId | Out-Null
}

Write-Host "✅ Binary extracted to: $OutputDir/migrator" -ForegroundColor Green

# Test the binary
if (-not $SkipTest) {
    Write-Host "`n🧪 Testing the binary..." -ForegroundColor Yellow
    
    # Make it executable (for WSL/Git Bash compatibility)
    if (Get-Command chmod -ErrorAction SilentlyContinue) {
        chmod +x "$OutputDir/migrator"
    }
    
    # Run the binary
    & "$OutputDir/migrator" info
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n✅ Binary test passed!" -ForegroundColor Green
    } else {
        Write-Host "`n⚠️ Binary test returned non-zero exit code" -ForegroundColor Yellow
    }
}

# Show file info
Write-Host "`n📊 Binary information:" -ForegroundColor Cyan
Get-Item "$OutputDir/migrator" | Select-Object Name, Length, LastWriteTime | Format-List

Write-Host "`n🎉 Build complete!" -ForegroundColor Green
Write-Host "Binary location: $((Resolve-Path "$OutputDir/migrator").Path)" -ForegroundColor White
