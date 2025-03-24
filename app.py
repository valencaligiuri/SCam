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

last_delay_log_time = {}  # Diccionario para almacenar el último tiempo de log por cliente

def start_server(port):
    global app, frame_buffer, streaming, frame_count, root, client_delays, last_delay_log_time

    if not is_port_available(port):
        messagebox.showerror("Error", f"El puerto {port} ya está en uso. Prueba con otro.")
        return

    # Obtener la dirección IP local
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        local_ip = "127.0.0.1"
        logging.error(f"No se pudo obtener la IP local, usando localhost: {e}")

    messagebox.showinfo("Éxito", f"Iniciando transmisión en el puerto {port}")
    logging.info(f"Iniciando transmisión en el puerto {port}")
    logging.info(f"Transmisión disponible en: http://localhost:{port}")
    logging.info(f"Transmisión disponible en: http://{local_ip}:{port}")

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
                height: 100%;
                overflow: hidden;
            }
            #stream {
                width: 100%;
                height: 100%;
                object-fit: contain; /* Ajusta la imagen para llenar la pantalla */
                display: block; /* Elimina el espacio extra debajo de la imagen */
            }
        </style>
    </head>
    <body>
        <img id="stream" src="/video" alt="Fullscreen Stream">

        <script>
            const streamElement = document.getElementById('stream');
            const streamUrl = '/video';
            let reconnectInterval = 5000;
            let imgCache = [];
            let imgIndex = 0;
            let maxCacheSize = 5;

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
                    setTimeout(loadStream, 0);  // Carga el siguiente frame inmediatamente
                };
                img.onerror = (error) => {
                    console.error('Error al cargar el stream. Reintentando en', reconnectInterval, 'ms', error);
                    logToServer('error', 'Error al cargar el stream. Reintentando la conexión... ' + error); // Enviar log al servidor
                    setTimeout(loadStream, reconnectInterval);
                };
                img.src = streamUrl + '?_=' + new Date().getTime(); // Evita el caché
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
                            logToServer('error', 'Heartbeat failed. Reintentando la conexión...'); // Enviar log al servidor
                        }
                    })
                    .catch(error => {
                        console.error('Error checking heartbeat:', error);
                        logToServer('error', 'Error checking heartbeat: ' + error); // Enviar log al servidor
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

    try:
        cap = cv2.VideoCapture(0)  # Abrir la cámara
        if not cap.isOpened():
            logging.error("No se pudo abrir la cámara")
            streaming = False
            create_tray_icon("red")
            return

        while streaming:
            ret, frame = cap.read()
            if not ret:
                logging.error("Error al capturar el frame de la cámara")
                streaming = False
                break

            frame_count += 1

            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
            frame_buffer = buffer.tobytes()
            time.sleep(0.01)  # Pequeña pausa para evitar el uso excesivo de la CPU

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

    start_button = ttk.Button(main_frame, text="Iniciar Transmisión", command=lambda: start(port_entry.get()))
    start_button.grid(row=1, column=0, columnspan=2, pady=10)

    status_label = ttk.Label(main_frame, text="Estado: Detenido", foreground="red")
    status_label.grid(row=2, column=0, columnspan=2)

    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    main_frame.columnconfigure(1, weight=1)

    create_tray_icon("red")

    def start(port_str):
        try:
            port = int(port_str)
            if not (1024 <= port <= 65535):
                messagebox.showerror("Error", "El puerto debe estar entre 1024 y 65535.")
                return
            status_label.config(text="Estado: Iniciando...", foreground="orange")
            threading.Thread(target=lambda: start_server_wrapper(port), daemon=True).start()
        except ValueError:
            messagebox.showerror("Error", "Por favor, introduce un número de puerto válido.")

    def start_server_wrapper(port):
        start_server(port)
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