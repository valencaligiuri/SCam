# SCam

Proyecto para transmitir la pantalla en red Local, utilizando Flask, OpenCV y otras bibliotecas.

## Requisitos

- Python 3.x
- pip

## Instalación

1. Clona el repositorio:
    ```sh
    git clone https://github.com/valencaligiuri/SCam.git
    cd SCam
    ```

2. Crea un entorno virtual y actívalo:
    ```sh
    python -m venv venv
    venv\Scripts\activate
    ```

3. Instala las dependencias:
    ```sh
    python install.py
    ```

**Nota para Windows:**

Si tienes problemas al ejecutar los scripts debido a restricciones de seguridad, abre PowerShell como administrador y ejecuta el siguiente comando:

```powershell
Set-ExecutionPolicy RemoteSigned
```

Esto permitirá la ejecución de scripts locales firmados y scripts descargados de Internet.

## Uso

Ejecuta el servidor:
```sh
python app.py
```

## Contribuir

Las contribuciones son bienvenidas. Por favor, abre un issue o un pull request.