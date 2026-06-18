#!/bin/bash

set -e

RESOURCE_DIR="external_resources"

MUSAN_URL="https://openslr.trmal.net/resources/17/musan.tar.gz"
RIRS_URL="https://openslr.trmal.net/resources/28/rirs_noises.zip"

mkdir -p "${RESOURCE_DIR}"
cd "${RESOURCE_DIR}"

echo "Downloading external resources into:"
echo "  $(pwd)"
echo ""

if [ ! -f "musan.tar.gz" ]; then
    echo "Downloading MUSAN..."
    wget "${MUSAN_URL}" -O musan.tar.gz
else
    echo "MUSAN archive already exists: musan.tar.gz"
fi

if [ ! -d "musan" ]; then
    echo "Extracting MUSAN..."
    tar -xzf musan.tar.gz
else
    echo "MUSAN folder already exists: musan"
fi

if [ ! -f "rirs_noises.zip" ]; then
    echo "Downloading OpenSLR RIRS_NOISES..."
    wget "${RIRS_URL}" -O rirs_noises.zip
else
    echo "RIRS_NOISES archive already exists: rirs_noises.zip"
fi

if [ ! -d "RIRS_NOISES" ]; then
    echo "Extracting RIRS_NOISES..."
    unzip rirs_noises.zip
else
    echo "RIRS_NOISES folder already exists: RIRS_NOISES"
fi

echo ""
echo "External resources downloaded successfully."
echo ""
echo "Expected folders:"
echo "  external_resources/musan"
echo "  external_resources/RIRS_NOISES"
echo ""
echo "Important:"
echo "  Raw MUSAN may still need to be prepared/split into musan_split/"
echo "  before it can be used as NOISE_ROOT."
