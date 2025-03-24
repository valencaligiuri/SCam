import subprocess
import os

# Verifica si el entorno virtual está activado
if 'VIRTUAL_ENV' not in os.environ:
    print("Error: El entorno virtual no está activado.")
    print("Por favor, activa el entorno virtual antes de ejecutar este script.")
    exit(1)

with open("requirements.txt", "r") as file:
    for package in file:
        package = package.strip()
        if package:
            try:
                subprocess.run(["pip", "install", package], check=True)
            except subprocess.CalledProcessError:
                print(f"Error al instalar {package}, continuando con el siguiente.")
