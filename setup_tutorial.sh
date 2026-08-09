#!/bin/bash

set -e  # Interrompe lo script se un comando fallisce

echo "=== Scarico tutti i sottomoduli"
git submodule update --init --recursive

echo "=== Aggiornamento pacchetti ==="
sudo apt-get update

echo "=== Installazione prerequisiti ==="
sudo apt-get install -y \
    automake \
    build-essential \
    clang \
    cmake \
    git \
    libboost-dev \
    libboost-filesystem-dev \
    libboost-iostreams-dev \
    libboost-thread-dev \
    libgmp-dev \
    libntl-dev \
    libsodium-dev \
    libssl-dev \
    libtool \
    python3 \
    gcc-11 \
    g++-11

echo "=== Scarico e installo boost"
rm -rf local
mkdir local
python3 deps/libOTe/build.py --setup --boost --install="$(pwd)/local"


echo "=== Verifica directory progetto ==="

# Controlla di essere nella root del progetto
if [ ! -f "Makefile" ]; then
    echo "Errore: esegui questo script dalla directory principale del progetto (dove si trova il Makefile)."
    exit 1
fi

echo "=== Configurazione ambiente ==="
make setup

echo "=== Preparazione dati Player-Data ==="

mkdir -p Player-Data

echo "1 2 3 4" > Player-Data/Input-P0-0
echo "1 2 3 4" > Player-Data/Input-P1-0

echo "File input creati:"
ls -l Player-Data/Input-P*-0

echo "=== Compilazione ed esecuzione tutorial ==="

python3 Scripts/compile-run.py mascot tutorial

echo "=== Operazione completata ==="

