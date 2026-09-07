[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repoRoot "backend"
$simulatorRoot = Join-Path $repoRoot "tools\device_simulator"
$frontendRoot = Join-Path $repoRoot "frontend"
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$simulatorPython = Join-Path $simulatorRoot ".venv\Scripts\python.exe"

function Assert-ExitCode {
    param([Parameter(Mandatory = $true)][string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function New-RandomBase64 {
    param(
        [Parameter(Mandatory = $true)][int]$Length,
        [switch]$UrlSafe
    )
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] $Length
        $random.GetBytes($bytes)
        $value = [Convert]::ToBase64String($bytes)
        if ($UrlSafe) {
            $value = $value.Replace('+', '-').Replace('/', '_')
        }
        return $value
    } finally {
        $random.Dispose()
    }
}

foreach ($requiredPath in @($backendPython, $simulatorPython, (Join-Path $frontendRoot "node_modules"))) {
    if (-not (Test-Path $requiredPath)) {
        throw "Missing project dependency at $requiredPath; follow README.md setup first."
    }
}

$testDatabaseUrl = [Environment]::GetEnvironmentVariable("TEST_DATABASE_URL", "Process")
if ([string]::IsNullOrWhiteSpace($testDatabaseUrl)) {
    throw "TEST_DATABASE_URL is required and its database name must end in '_test'."
}

$previousDatabaseUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
$previousAppSecret = [Environment]::GetEnvironmentVariable("APP_SECRET_KEY", "Process")
$previousCredentialKey = [Environment]::GetEnvironmentVariable(
    "DEVICE_CREDENTIAL_ENCRYPTION_KEY",
    "Process"
)
$previousTimezone = [Environment]::GetEnvironmentVariable("TZ", "Process")

try {
    $env:DATABASE_URL = $testDatabaseUrl
    if ([string]::IsNullOrWhiteSpace($previousAppSecret)) {
        $env:APP_SECRET_KEY = New-RandomBase64 -Length 48
    }
    if ([string]::IsNullOrWhiteSpace($previousCredentialKey)) {
        $env:DEVICE_CREDENTIAL_ENCRYPTION_KEY = New-RandomBase64 -Length 32 -UrlSafe
    }

    Write-Host "[verify] validating guarded test database"
    Push-Location $backendRoot
    try {
        & $backendPython -m app.database_safety
        Assert-ExitCode "test database guard"
    } finally {
        Pop-Location
    }

    Write-Host "[verify] Docker Compose configuration"
    Push-Location $repoRoot
    try {
        docker compose config --quiet
        Assert-ExitCode "docker compose config"
        docker compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | Out-Null
        Assert-ExitCode "PostgreSQL health check"
    } finally {
        Pop-Location
    }

    Write-Host "[verify] backend migrations, tests, lint, format and types"
    Push-Location $backendRoot
    try {
        & $backendPython -m alembic upgrade head
        Assert-ExitCode "backend migrations"
        & $backendPython -m pytest -v
        Assert-ExitCode "backend tests"
        & $backendPython -m ruff check .
        Assert-ExitCode "backend Ruff"
        & $backendPython -m ruff format --check .
        Assert-ExitCode "backend format"
        & $backendPython -m mypy app
        Assert-ExitCode "backend mypy"
    } finally {
        Pop-Location
    }

    Write-Host "[verify] simulator tests, lint, format and types"
    Push-Location $simulatorRoot
    try {
        & $simulatorPython -m pytest -v
        Assert-ExitCode "simulator tests"
        & $simulatorPython -m ruff check .
        Assert-ExitCode "simulator Ruff"
        & $simulatorPython -m ruff format --check .
        Assert-ExitCode "simulator format"
        & $simulatorPython -m mypy src
        Assert-ExitCode "simulator mypy"
    } finally {
        Pop-Location
    }

    Write-Host "[verify] frontend tests (TZ=UTC), lint, types and production build"
    $env:TZ = "UTC"
    Push-Location $frontendRoot
    try {
        npm test -- --run
        Assert-ExitCode "frontend tests"
        npm run lint
        Assert-ExitCode "frontend lint"
        npm run typecheck
        Assert-ExitCode "frontend typecheck"
        npm run build
        Assert-ExitCode "frontend build"
    } finally {
        Pop-Location
    }

    Write-Host "[verify] all checks completed"
} finally {
    [Environment]::SetEnvironmentVariable("DATABASE_URL", $previousDatabaseUrl, "Process")
    [Environment]::SetEnvironmentVariable("APP_SECRET_KEY", $previousAppSecret, "Process")
    [Environment]::SetEnvironmentVariable(
        "DEVICE_CREDENTIAL_ENCRYPTION_KEY",
        $previousCredentialKey,
        "Process"
    )
    [Environment]::SetEnvironmentVariable("TZ", $previousTimezone, "Process")
}
