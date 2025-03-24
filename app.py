import socket
import cv2
import socket
import cv2
import numpy as np
import threading
import pystray
import ctypes
import time
import json
import logging
from flask import Response, Flask, request
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from PIL import Image, ImageDraw

import wmi  # Import the wmi module

# Configuración del logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

frame_buffer = None
streaming = False
tray_icon = None
frame_count = 0
root = None
client_delays = {}  # Diccionario para almacenar los retrasos de cada cliente
STATS_UPDATE_INTERVAL = 5000  # Intervalo de actualización de estadísticas en ms
HEARTBEAT_INTERVAL = 10000  # Intervalo de heartbeat en ms
DELAY_LOG_INTERVAL = 2  # Intervalo mínimo entre logs de retraso (en segundos)

def is_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0

def create_tray_icon(color):
    global tray_icon, root
    image = Image.new('RGB', (64, 64), color)
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 54, 54), fill=color)

    menu = pystray.Menu(
        pystray.MenuItem("Estadísticas", lambda: show_stats()),
        pystray.MenuItem("Salir", lambda: on_exit())
    )

    if tray_icon:
        tray_icon.icon = image
        tray_icon.menu = menu
    else:
        tray_icon = pystray.Icon("ScreenStreamer", image, "Transmisión", menu=menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()

def on_exit():
    global root, tray_icon, streaming
    streaming = False
    if tray_icon:
        tray_icon.stop()
    if root:
        root.destroy()

def hide_window():
    global root
    root.withdraw()

def list_cameras():
    arr = []
    try:
        c = wmi.WMI()
        for device in c.Win32_PnPEntity():
            if device.Name and ("camera" in device.Name.lower() or "video" in device.Name.lower()):
                name = device.Name
                arr.append({"name": name, "index": len(arr)})  # Assign a unique index
    except Exception as e:
        logging.error(f"Error listing cameras with WMI: {e}")
        # Fallback to the previous method if WMI fails
        index = 0
        while True:
            cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                break
            else:
                name = f"Cámara {index}"
                arr.append({"name": name, "index": index})
            cap.release()
            index += 1
    return arr

last_delay_log_time = {}  # Diccionario para almacenar el último tiempo de log por cliente

def start_server(port, camera_index):
    global app, frame_buffer, streaming, frame_count, root, client_delays, last_delay_log_time

    if not is_port_available(port):
        messagebox.showerror("Error", f"El puerto {port} ya está en uso. Prueba con otro.")
        return  

    # Ensure the camera index is valid
    cameras = list_cameras()
    if camera_index >= len(cameras) or camera_index < 0:
        messagebox.showerror("Error", f"Índice de cámara {camera_index} fuera de rango.")
        return

    # Check if the camera is available before starting the server
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        messagebox.showerror("Error", "La cámara no se pudo abrir. Puede que esté en uso por otra aplicación.")
        if cap is not None:
            cap.release()
        return
    else:
        # Attempt to grab a frame to ensure the camera is truly available
        ret, frame = cap.read()
        if not ret:
            messagebox.showerror("Error", "La cámara está en uso por otra aplicación o no está disponible.")
            if cap is not None:
                cap.release()
            return
        else:
            messagebox.showinfo("Éxito", f"Cámara {camera_index} disponible. Iniciando transmisión.")
            if cap is not None:
                cap.release()  # Release the camera after checking its availability

    # Obtener la dirección IP local
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        local_ip = "127.0.0.1"
        logging.error(f"No se pudo obtener la IP local, usando localhost: {e}")

    streaming = True
    create_tray_icon("green")
    frame_count = 0

    if root is not None:
        root.after(0, lambda: root.withdraw())

    app = Flask(__name__)

    # Desactivar el logging de Flask
    app.logger.disabled = True
    logging.getLogger('werkzeug').disabled = True

    # HTML para el cliente con pantalla completa
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SCam</title>
        <style>
            body, html {
                margin: 0;
                padding: 0;
                height: 100%; /* Fill the entire viewport */
                width: 100%;  /* Fill the entire viewport */
                overflow: hidden; /* Prevent scrollbars */
            }
            #stream {
                display: block;
                max-width: 100%; /* Ensure it doesn't exceed the viewport width */
                max-height: 100%; /* Ensure it doesn't exceed the viewport height */
                object-fit: contain; /* Maintain aspect ratio and fit inside the viewport */
                position: absolute; /* Position it to cover the entire viewport */
                top: 0;
                left: 0;
            }
            #loading {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                font-size: 2em;
                color: white;
                z-index: 10;
            }
        </style>
    </head>
    <body>
        <div id="loading">Cargando...</div>
        <img id="stream" src="/video" alt="Fullscreen Stream" style="display:none;">

        <script>
            const streamElement = document.getElementById('stream');
            const loadingElement = document.getElementById('loading');
            const streamUrl = '/video';
            let reconnectInterval = 3000;
            let imgCache = [];
            let imgIndex = 0;
            let maxCacheSize = 3;

            function logToServer(level, message) {
                fetch('/log', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ level: level, message: message })
                }).catch(error => {
                    console.error('Error al enviar el log al servidor:', error);
                });
            }

            function loadStream() {
                const img = new Image();
                img.onload = () => {
                    imgCache.push(img.src);
                    if (imgCache.length > maxCacheSize) {
                        imgCache.shift();
                    }
                    if (imgIndex >= imgCache.length) {
                        imgIndex = imgCache.length - 1;
                    }
                    streamElement.src = imgCache[imgIndex];
                    streamElement.style.display = 'block';
                    loadingElement.style.display = 'none';
                };
                img.onerror = (error) => {
                    console.error('Error al cargar el stream. Reintentando en', reconnectInterval, 'ms', error);
                    logToServer('error', 'Error al cargar el stream. Reintentando la conexión... ' + error);
                    setTimeout(loadStream, reconnectInterval);
                };
                img.src = streamUrl + '?_=' + new Date().getTime();
            }

            loadStream();

            // Función para actualizar la imagen mostrada
            function updateImage() {
              if (imgCache.length > 0) {
                streamElement.src = imgCache[imgIndex];
                imgIndex = (imgIndex + 1) % imgCache.length;
              }
              setTimeout(updateImage, 30);
            }

            // Iniciar la actualización de la imagen
            updateImage();

            function checkHeartbeat() {
                fetch('/heartbeat', {
                    method: 'GET',
                    mode: 'cors',
                    cache: 'no-cache',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error(`Heartbeat failed with status ${response.status}`);
                        }
                        return response.json();
                    })
                    .then(data => {
                        if (data.status !== 'ok') {
                            console.error('Heartbeat failed. Reintentando la conexión...');
                            logToServer('error', 'Heartbeat failed. Reintentando la conexión...');
                        }
                    })
                    .catch(error => {
                        console.error('Error checking heartbeat:', error);
                        logToServer('error', 'Error checking heartbeat: ' + error);
                    });
            }

            setInterval(checkHeartbeat, """ + str(HEARTBEAT_INTERVAL * 2) + """);
        </script>
    </body>
    </html>
    """

    @app.route('/')
    def index():
        return html_content

    @app.route('/video')
    def video_stream():
        client_ip = request.remote_addr  # Obtener la IP del cliente
        start_time = None  # Inicializar start_time fuera del bucle

        def generate():
            nonlocal start_time
            global frame_buffer, streaming, last_delay_log_time
            while streaming:
                try:
                    if frame_buffer is not None:
                        if start_time is None:
                            start_time = time.time()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_buffer + b'\r\n')
                        # Calcular el retraso y almacenarlo
                        end_time = time.time()
                        delay = (end_time - start_time) * 1000  # Retraso en ms

                        # Throttling del log de retraso
                        now = time.time()
                        if client_ip not in last_delay_log_time or now - last_delay_log_time[client_ip] >= DELAY_LOG_INTERVAL:
                            if delay > 200:
                                logging.info(f"Client {client_ip} delay: {delay:.2f} ms")
                            last_delay_log_time[client_ip] = now  # Actualizar el tiempo del último log

                        client_delays[client_ip] = delay
                        start_time = time.time()  # Reiniciar el tiempo de inicio para el próximo frame
                        time.sleep(0.01)  # Pequeña pausa para evitar el uso excesivo de la CPU
                    else:
                        cv2.waitKey(10)
                except Exception as e:
                    logging.error(f"Error en generate(): {e}")
                    streaming = False  # Detener la transmisión en caso de error
                    break  # Salir del bucle generate

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/heartbeat')
    def heartbeat():
        return json.dumps({'status': 'ok'})

    @app.route('/log', methods=['POST'])
    def log_message():
        log_data = request.get_json()
        level = log_data['level']
        message = log_data['message']

        if level == 'error':
            logging.error(f"Client log: {message}")
        elif level == 'info':
            logging.info(f"Client log: {message}")
        elif level == 'warning':
            logging.warning(f"Client log: {message}")
        else:
            logging.info(f"Client log: {message}")

        return json.dumps({'status': 'ok'})

    def flask_thread():
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

    threading.Thread(target=flask_thread, daemon=True).start()

    while streaming:
        try:
            cap = cv2.VideoCapture(camera_index)  # Abrir la cámara seleccionada
            if not cap.isOpened():
                messagebox.showerror("Error", "La cámara está en uso por otra aplicación. Intentando de nuevo...")
                if cap is not None:
                    cap.release()
                time.sleep(5)  # Esperar 5 segundos antes de intentar de nuevo
                continue  # Volver al inicio del bucle while
            else:
                messagebox.showinfo("Éxito", f"Cámara {camera_index} disponible. Iniciando transmisión.")
                break  # Salir del bucle while si la cámara se abre correctamente
        except Exception as e:
            logging.error(f"Error al intentar abrir la cámara: {e}")
            messagebox.showerror("Error", f"Error al intentar abrir la cámara: {e}. Intentando de nuevo...")
            time.sleep(5)
            continue

    if not streaming:
        logging.info("Transmisión cancelada debido a que la cámara no está disponible.")
        create_tray_icon("red")
        return

    try:
        # Establecer la resolución de la cámara (opcional)
        # cap_width = 640
        # cap_height = 480
        # cap.set(cv2.CAP_PROP_FRAME_WIDTH, cap_width)  # Ancho
        # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cap_height)  # Alto

        while streaming:
            try:
                # Check if the camera is still available
                if cap is None or not cap.isOpened():
                    logging.error("Cámara no disponible, intentando reconectar...")
                    if cap is not None:
                        cap.release()
                    cap = None
                    time.sleep(5)  # Wait before retrying
                    try:
                        cap = cv2.VideoCapture(camera_index)
                        if not cap.isOpened():
                            logging.error("No se pudo reabrir la cámara.")
                            continue  # Skip to the next iteration of the streaming loop
                    except Exception as e:
                        logging.error(f"Error al intentar reabrir la cámara: {e}")
                        continue  # Skip to the next iteration of the streaming loop
                    logging.info("Cámara reconectada exitosamente.")

                ret, frame = cap.read()
                if not ret:
                    logging.error("Error al capturar el frame de la cámara")
                    # Attempt to re-open the camera
                    if cap is not None:
                        cap.release()
                    cap = None
                    time.sleep(5)  # Wait before retrying
                    while True:
                        try:
                            cap = cv2.VideoCapture(camera_index)
                            if not cap.isOpened():
                                logging.error("No se pudo reabrir la cámara. Intentando de nuevo...")
                                time.sleep(5)
                                continue
                             # Establecer la resolución de la cámara (opcional)
                            # cap.set(cv2.CAP_PROP_FRAME_WIDTH, cap_width)  # Ancho
                            # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cap_height)  # Alto
                            break  # Camera re-opened successfully
                        except Exception as e:
                            logging.error(f"Error al intentar reabrir la cámara: {e}")
                            time.sleep(5)
                    continue  # Skip to the next iteration of the streaming loop

                frame_count += 1

                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
                frame_buffer = buffer.tobytes()
                time.sleep(0.01)  # Pequeña pausa para evitar el uso excesivo de la CPU
            except Exception as e:
                logging.error(f"Error durante la captura del frame: {e}")
                messagebox.showerror("Error", "La cámara puede estar en uso o desconectada. Intente reiniciar la transmisión.")
                streaming = False
                break

        if cap is not None:
            cap.release()
    except Exception as e:
        logging.error(f"Error durante la captura de la cámara: {e}")
        streaming = False
        create_tray_icon("red")

def gui():
    global root
    root = tk.Tk()
    root.title("Configuración de Transmisión de Cámara")

    try:
        root.iconbitmap("icon.ico")
    except:
        logging.info("No se encontro el icono icon.ico")
    
    style = ttk.Style()
    style.theme_use('clam')

    main_frame = ttk.Frame(root, padding=(10, 10, 10, 10))
    main_frame.pack(fill=tk.BOTH, expand=True)

    port_label = ttk.Label(main_frame, text="Puerto de Transmisión:")
    port_label.grid(row=0, column=0, sticky=tk.W)

    port_entry = ttk.Entry(main_frame)
    port_entry.grid(row=0, column=1, sticky=(tk.E, tk.W))
    port_entry.insert(0, "5000")

    camera_label = ttk.Label(main_frame, text="Dispositivo de Cámara:")
    camera_label.grid(row=1, column=0, sticky=tk.W)

    # Modify camera combobox to show names
    cameras = list_cameras()
    camera_names = [cam["name"] for cam in cameras]
    camera_combobox = ttk.Combobox(main_frame, values=camera_names)
    camera_combobox.grid(row=1, column=1, sticky=(tk.E, tk.W))
    if camera_names:
        camera_combobox.current(0)

    start_button = ttk.Button(main_frame, text="Iniciar Transmisión", command=lambda: start(port_entry.get(), camera_combobox.get()))
    start_button.grid(row=2, column=0, columnspan=2, pady=10)

    status_label = ttk.Label(main_frame, text="Estado: Detenido", foreground="red")
    status_label.grid(row=3, column=0, columnspan=2)

    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    main_frame.columnconfigure(1, weight=1)

    create_tray_icon("red")

    def start(port_str, camera_name):
        try:
            port = int(port_str)
            # Find camera index by name
            cameras = list_cameras()
            camera_index = next(cam["index"] for cam in cameras if cam["name"] == camera_name)
            
            if not (1024 <= port <= 65535):
                messagebox.showerror("Error", "El puerto debe estar entre 1024 y 65535.")
                return
            status_label.config(text="Estado: Iniciando...", foreground="orange")
            threading.Thread(target=lambda: start_server_wrapper(port, camera_index), daemon=True).start()
        except (ValueError, StopIteration):
            messagebox.showerror("Error", "Por favor, introduce un número de puerto válido y selecciona una cámara.")

    def start_server_wrapper(port, camera_index):
        start_server(port, camera_index)
        root.after(0, lambda: update_status_label())

    def update_status_label():
        if streaming:
            status_label.config(text="Estado: Transmitiendo", foreground="green")
        else:
            status_label.config(text="Estado: Detenido", foreground="red")

    root.protocol("WM_DELETE_WINDOW", lambda: on_exit())  # Ahora al cerrar se termina la app
    root.mainloop()

def show_stats():
    global client_delays

    stats_window = tk.Toplevel(root)  # Crear una nueva ventana
    stats_window.title("Estadísticas de Transmisión")

    # Encabezados de la tabla
    ip_header = ttk.Label(stats_window, text="Dirección IP", font=('Arial', 10, 'bold'))
    ip_header.grid(row=0, column=0, padx=5, pady=5)
    delay_header = ttk.Label(stats_window, text="Retraso (ms)", font=('Arial', 10, 'bold'))
    delay_header.grid(row=0, column=1, padx=5, pady=5)

    # Función para actualizar las estadísticas periódicamente
    def update_stats():
        # Eliminar filas existentes (excepto los encabezados)
        for widget in stats_window.winfo_children():
            if int(widget.grid_info()['row']) > 0:
                widget.destroy()

        # Mostrar los datos de retraso de cada cliente
        row_num = 1
        for ip, delay in client_delays.items():
            ip_label = ttk.Label(stats_window, text=ip)
            ip_label.grid(row=row_num, column=0, padx=5, pady=2)
            delay_label = ttk.Label(stats_window, text=f"{delay:.2f}")  # Formatear el retraso
            delay_label.grid(row=row_num, column=1, padx=5, pady=2)
            row_num += 1
        stats_window.after(STATS_UPDATE_INTERVAL, update_stats)  # Actualizar con intervalo

    update_stats()  # Iniciar la actualización periódica

if __name__ == "__main__":
    gui()