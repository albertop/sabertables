#!/bin/bash

# Script to import a Docker image tar file from ~/images folder

# Prompt the user for the version
read -p "Enter the version of the sabertables image to deploy (e.g., 1.0.0): " VERSION

# Validate that version was provided
if [ -z "$VERSION" ]; then
    echo "Error: Version cannot be empty. Please provide a version."
    exit 1
fi

# Set the tar file path in ~/images folder
TAR_FILE="$HOME/images/sabertables-$VERSION.tar"

# Validate that the tar file exists
if [ ! -f "$TAR_FILE" ]; then
    echo "Error: Tar file not found at $TAR_FILE"
    exit 1
fi

echo ""
echo "Loading Docker image from: $TAR_FILE"
echo ""

# Load the image into Docker
if docker load -i "$TAR_FILE"; then
    echo ""
    echo "Image loaded successfully!"
    echo "You can now use the image: sabertables:$VERSION"
else
    echo "Error: Failed to load Docker image from tar file"
    exit 1
fi
