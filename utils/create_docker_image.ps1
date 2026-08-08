# Script to create a Docker image with sabertables and save it to docker_images folder
#
#   .\utils\create_docker_image.ps1                     pregunta la version
#   .\utils\create_docker_image.ps1 -Version 1.2025.34  la recibe ya puesta

param(
    [string]$Version
)

# La version se puede pasar por parametro.
#
# Con Read-Host a secas el script SOLO funcionaba con una persona delante: en
# cualquier ejecucion no interactiva --automatizacion, CI, o un asistente
# haciendo el despliegue-- fallaba con "PowerShell is in NonInteractive mode".
# Se conserva la pregunta como camino por defecto, que es el que documenta
# docs/OPERACION_ANUAL.md.
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Read-Host "Enter the version for sabertables image in format x.y.z (e.g., 1.2025.34)"
}

# Validar el formato, no solo que no este vacia.
#
# El valor acaba en una etiqueta de imagen Y en un nombre de fichero, asi que un
# espacio o un caracter raro rompe una de las dos cosas mas adelante, cuando ya
# se ha construido la imagen.
if ([string]::IsNullOrWhiteSpace($Version)) {
    Write-Error "La version no puede estar vacia."
    exit 1
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Error "Formato invalido: '$Version'. Se espera x.y.z (por ejemplo 1.2025.34)."
    exit 1
}

# Comprobar que Docker responde, en vez de preguntarselo al usuario.
#
# Antes esto era un Read-Host "Is Docker Desktop running? (yes/no)": si contestabas
# que si sin estarlo, el fallo aparecia despues en mitad del build. `docker info`
# lo sabe sin preguntar.
Write-Host ""
Write-Host "Comprobando que Docker responde..."
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker no responde. Arranca Docker Desktop y vuelve a intentarlo."
    exit 1
}

# Get the root directory of the project
$rootDir = Split-Path -Parent $PSScriptRoot
$dockerImagesDir = Join-Path $rootDir "docker_images"
$dockerfile = Join-Path $rootDir "Dockerfile"

# Create docker_images directory if it doesn't exist
if (-not (Test-Path $dockerImagesDir)) {
    Write-Host "Creating docker_images directory at $dockerImagesDir"
    New-Item -ItemType Directory -Path $dockerImagesDir -Force | Out-Null
}

# Validate Dockerfile exists
if (-not (Test-Path $dockerfile)) {
    Write-Error "Dockerfile not found at $dockerfile"
    exit 1
}

$imageName = "sabertables:$Version"
$outputPath = Join-Path $dockerImagesDir "sabertables-$Version.tar"

# No pisar en silencio una version ya publicada: los tar de docker_images/ son
# lo que se transfiere al VPS, asi que sobreescribir uno cambia lo que se
# despliega sin que nadie se entere.
if (Test-Path $outputPath) {
    Write-Host ""
    Write-Warning "Ya existe $outputPath"
    $sobreescribir = Read-Host "Sobreescribir esa version? (yes/no)"
    if ($sobreescribir -ne "yes" -and $sobreescribir -ne "y") {
        Write-Host "Cancelado. Usa otro numero de version."
        exit 1
    }
}

Write-Host "Building Docker image: $imageName"
Write-Host "Output will be saved to: $outputPath"
Write-Host ""

# Build the Docker image
try {
    Push-Location $rootDir
    docker build --no-cache -t $imageName -f $dockerfile .
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker build failed with exit code $LASTEXITCODE"
        exit 1
    }
    
    Write-Host ""
    Write-Host "Image built successfully: $imageName"
    Write-Host "Saving image to $outputPath..."
    
    # Save the image to a tar file
    docker save -o $outputPath $imageName
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker save failed with exit code $LASTEXITCODE"
        exit 1
    }
    
    Write-Host "Image saved successfully!"
    Write-Host "Output file: $outputPath"
    Write-Host "Image size: $(([math]::Round((Get-Item $outputPath).Length / 1MB, 2))) MB"
}
catch {
    Write-Error "An error occurred: $_"
    exit 1
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Docker image creation completed successfully!"
